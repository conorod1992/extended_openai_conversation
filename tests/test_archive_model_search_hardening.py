"""Regression tests for Archive and model-facing local search hardening."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
    conversation_archive,
    model_search_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_MEMORY_MODE,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    archive_tools,
)
from custom_components.extended_openai_conversation_responses.knowledge import knowledge_tools
from custom_components.extended_openai_conversation_responses.memory import memory_tools
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
    """Each model-search owner advertises minLength before request assembly."""
    owned_tools = [
        *memory_tools(),
        *archive_tools(),
        *knowledge_tools(),
    ]
    by_name = {tool["spec"]["name"]: tool for tool in owned_tools}

    for name in ("memory_search", "conversation_search", "knowledge_search"):
        assert (
            by_name[name]["spec"]["parameters"]["properties"]["query"]["minLength"] == 1
        )

    assembled = assemble_integration_function_tools(
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
    assembled_by_name = {tool["spec"]["name"]: tool for tool in assembled}
    for name in ("memory_search", "conversation_search", "knowledge_search"):
        assert assembled_by_name[name]["spec"] == by_name[name]["spec"]


def _runtime_agent():
    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(
        data={"archive_enabled": True, "archive_model_search_enabled": True}
    )
    agent._archive = SimpleNamespace(async_search=AsyncMock())
    agent._async_search_memories = AsyncMock()
    return agent


async def test_model_search_wrappers_reject_blank_before_backend_work() -> None:
    """All three model-facing search paths reject whitespace before their backend."""
    agent = _runtime_agent()

    with pytest.raises(ValueError, match="blank"):
        await agent._async_execute_memory_tool("search", {"query": "  "}, None)
    with pytest.raises(ValueError, match="blank"):
        await agent._async_execute_knowledge_tool("search", {"query": "\t"})
    with pytest.raises(ValueError, match="blank"):
        await agent._async_execute_archive_tool("search", {"query": "\n"})

    agent._archive.async_search.assert_not_awaited()


async def test_blank_automatic_memory_ranking_skips_original_retrieval() -> None:
    """A useless automatic-memory query must not reach embedding/retrieval work."""
    agent = _runtime_agent()

    result = await agent._async_rank_memories(["alice"], "   ", 3)

    assert result == []
    agent._async_search_memories.assert_not_awaited()


async def test_archive_wrapper_reports_archive_specific_unavailability() -> None:
    """Unexpected Archive failures are not mislabeled as Knowledge failures."""
    agent = _runtime_agent()

    agent._archive.async_search.side_effect = OSError("archive backend failed")
    token = owner._ACTIVE_SCOPE.set(SimpleNamespace(scope_id="user:alice"))
    active = owner._ACTIVE_ARCHIVE.set(("session", "id"))
    try:
        result = await agent._async_execute_archive_tool("search", {"query": "kitchen"})
    finally:
        owner._ACTIVE_ARCHIVE.reset(active)
        owner._ACTIVE_SCOPE.reset(token)

    assert result == {
        "status": "unavailable",
        "error": "Conversation Archive is temporarily unavailable",
    }


async def test_archive_wrapper_preserves_expected_runtime_errors() -> None:
    """Expected Archive state/input errors still flow to the shared error result path."""
    agent = _runtime_agent()

    agent._archive = None
    with pytest.raises(RuntimeError, match="archive is unavailable"):
        await agent._async_execute_archive_tool(
            "search", {"query": "kitchen", "state_error": True}
        )


@pytest.mark.parametrize("arguments", [{}, {"query": None}, {"query": 123}])
def test_require_nonblank_query_rejects_missing_or_non_string_query(arguments) -> None:
    with pytest.raises(ValueError, match="query is required"):
        hardening._require_nonblank_query(arguments)


@pytest.mark.asyncio
async def test_knowledge_search_with_valid_query_delegates():
    from custom_components.extended_openai_conversation_responses import (
        conversation as owner,
    )
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestCapabilityPolicy,
    )

    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"knowledge_enabled": True})
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    search = AsyncMock(return_value=[])
    agent._knowledge = SimpleNamespace(
        source_count=1,
        async_search=search,
        resolve_source_filter=lambda _ids: (None, []),
    )
    assert await agent._async_execute_knowledge_tool(
        "search", {"query": "temperature"}
    ) == {"results": []}
    search.assert_awaited_once_with("temperature", None, 5)


@pytest.mark.asyncio
async def test_archive_non_search_operation_does_not_require_query():
    from custom_components.extended_openai_conversation_responses import (
        conversation as owner,
    )

    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data={"archive_enabled": True})
    expected = {"status": "ok"}
    get = AsyncMock(return_value=expected)
    agent._archive = SimpleNamespace(async_get=get)
    scope = owner._ACTIVE_SCOPE.set(SimpleNamespace(scope_id="user:alice"))
    active = owner._ACTIVE_ARCHIVE.set(("session", "id"))
    try:
        assert (
            await agent._async_execute_archive_tool("get", {"session_id": "item-1"})
            is expected
        )
    finally:
        owner._ACTIVE_SCOPE.reset(scope)
        owner._ACTIVE_ARCHIVE.reset(active)
    get.assert_awaited_once_with("user:alice", "item-1", 0, 6)
