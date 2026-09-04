"""Regression tests for Archive and model-facing local search hardening."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_archive,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_MEMORY_MODE,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.model_search_hardening import (
    _install_on_agent_class,
)
from custom_components.extended_openai_conversation_responses.request import (
    assemble_integration_function_tools,
)
from custom_components.extended_openai_conversation_responses.scope import (
    unretained_scope,
    user_scope,
)


class _ArchiveStorage:
    """Small in-memory Archive storage with write counters."""

    def __init__(self) -> None:
        self.metadata = None
        self.partitions: dict[str, dict] = {}
        self.metadata_save_count = 0

    async def async_load_metadata(self):
        return deepcopy(self.metadata)

    async def async_save_metadata(self, data):
        self.metadata_save_count += 1
        self.metadata = deepcopy(data)

    async def async_load_partition(self, partition):
        return deepcopy(self.partitions.get(partition))

    async def async_save_partition(self, partition, data):
        self.partitions[partition] = deepcopy(data)


async def _archive() -> tuple[ConversationArchive, _ArchiveStorage]:
    storage = _ArchiveStorage()
    archive = ConversationArchive(storage, "agent-1")
    await archive.async_initialize()
    return archive, storage


async def test_archive_rejects_blank_search_before_scoring(monkeypatch) -> None:
    """Whitespace-only Archive searches must fail before any worker scan."""
    archive, _storage = await _archive()
    called = False

    async def to_thread(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(conversation_archive.asyncio, "to_thread", to_thread)

    with pytest.raises(ValueError, match="blank"):
        await archive.async_search("user:alice", "   ")

    assert called is False


async def test_archive_search_scores_snapshot_in_worker(monkeypatch) -> None:
    """Archive candidate scoring must leave the event loop after snapshotting."""
    archive, _storage = await _archive()
    session = await archive.async_begin_session(
        "browser",
        user_scope("alice", source="authenticated_user"),
        "conversation-1",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session is not None
    await archive.async_record_turn(
        session.session_id,
        run_id="run-1",
        user_text="The kitchen light is too bright",
        assistant_text="I dimmed the kitchen light",
        successful=True,
    )

    calls = []

    async def to_thread(function, *args):
        calls.append((function, args))
        return function(*args)

    monkeypatch.setattr(conversation_archive.asyncio, "to_thread", to_thread)

    result = await archive.async_search("user:alice", "kitchen", limit=5)

    assert [call[0] for call in calls] == [
        conversation_archive._search_archive_snapshot
    ]
    assert len(result["results"]) == 1
    assert result["results"][0]["session_id"] == session.session_id


async def test_scope_counts_excludes_sessions_made_private() -> None:
    """Private Archive sessions are not retained scope totals."""
    archive, _storage = await _archive()
    session = await archive.async_begin_session(
        "browser",
        user_scope("alice", source="authenticated_user"),
        "conversation-1",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session is not None
    assert archive.scope_counts() == {"user:alice": 1}

    await archive.async_make_private(session.session_id)

    assert archive.scope_counts() == {}


async def test_runtime_only_archive_session_skips_metadata_write() -> None:
    """Creating an unretained runtime session must not rewrite durable metadata."""
    archive, storage = await _archive()

    session = await archive.async_begin_session(
        "satellite",
        unretained_scope(device_id="satellite-1"),
        "conversation-1",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert session is not None
    assert session.retention_state == "unretained"
    assert storage.metadata_save_count == 0


async def test_runtime_only_session_does_not_hide_pending_archive_transaction() -> None:
    """The unretained fast path must not suppress a pending partition journal."""
    archive, storage = await _archive()
    archive._pending_partitions.add("2026-09")

    session = await archive.async_begin_session(
        "satellite",
        unretained_scope(device_id="satellite-1"),
        "conversation-1",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )

    assert session is not None
    assert storage.metadata_save_count == 1
    assert storage.metadata is not None
    assert "2026-09" in storage.metadata["pending_partitions"]


def test_model_search_schemas_require_nonempty_query() -> None:
    """Model-facing Archive, Memory and Knowledge searches advertise minLength."""
    tools = assemble_integration_function_tools(
        {
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_ARCHIVE_ENABLED: True,
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED: True,
        },
        set(),
        memory_scope_available=True,
        temporary_scope_available=False,
        knowledge_available=True,
        archive_available=True,
    )
    by_name = {tool["spec"]["name"]: tool for tool in tools}

    for name in ("memory_search", "conversation_search", "knowledge_search"):
        assert (
            by_name[name]["spec"]["parameters"]["properties"]["query"]["minLength"]
            == 1
        )


class _FakeAgent:
    """Agent seam used to test wrappers without mutating the production class."""

    rank_calls = 0
    memory_calls = 0
    knowledge_calls = 0
    archive_calls = 0

    async def _async_rank_memories(self, scope_ids, query, limit):
        type(self).rank_calls += 1
        return [scope_ids, query, limit]

    async def _async_execute_memory_tool(self, operation, arguments, llm_context):
        type(self).memory_calls += 1
        return {"operation": operation, "arguments": arguments}

    async def _async_execute_knowledge_tool(self, operation, arguments):
        type(self).knowledge_calls += 1
        return {"operation": operation, "arguments": arguments}

    async def _async_execute_archive_tool(self, operation, arguments):
        type(self).archive_calls += 1
        if arguments.get("explode"):
            raise OSError("archive backend failed")
        if arguments.get("state_error"):
            raise RuntimeError("archive unavailable")
        return {"operation": operation, "arguments": arguments}


def _wrapped_fake_agent() -> _FakeAgent:
    class WrappedFakeAgent(_FakeAgent):
        pass

    _install_on_agent_class(WrappedFakeAgent)
    return WrappedFakeAgent()


async def test_model_search_wrappers_reject_blank_before_backend_work() -> None:
    """All three model-facing search paths reject whitespace before their backend."""
    agent = _wrapped_fake_agent()

    with pytest.raises(ValueError, match="blank"):
        await agent._async_execute_memory_tool("search", {"query": "  "}, None)
    with pytest.raises(ValueError, match="blank"):
        await agent._async_execute_knowledge_tool("search", {"query": "\t"})
    with pytest.raises(ValueError, match="blank"):
        await agent._async_execute_archive_tool("search", {"query": "\n"})

    assert type(agent).memory_calls == 0
    assert type(agent).knowledge_calls == 0
    assert type(agent).archive_calls == 0


async def test_blank_automatic_memory_ranking_skips_original_retrieval() -> None:
    """A useless automatic-memory query must not reach embedding/retrieval work."""
    agent = _wrapped_fake_agent()

    result = await agent._async_rank_memories(["alice"], "   ", 3)

    assert result == []
    assert type(agent).rank_calls == 0


async def test_archive_wrapper_reports_archive_specific_unavailability() -> None:
    """Unexpected Archive failures are not mislabeled as Knowledge failures."""
    agent = _wrapped_fake_agent()

    result = await agent._async_execute_archive_tool(
        "search", {"query": "kitchen", "explode": True}
    )

    assert result == {
        "status": "unavailable",
        "error": "Conversation Archive is temporarily unavailable",
    }


async def test_archive_wrapper_preserves_expected_runtime_errors() -> None:
    """Expected Archive state/input errors still flow to the shared error result path."""
    agent = _wrapped_fake_agent()

    with pytest.raises(RuntimeError, match="archive unavailable"):
        await agent._async_execute_archive_tool(
            "search", {"query": "kitchen", "state_error": True}
        )
