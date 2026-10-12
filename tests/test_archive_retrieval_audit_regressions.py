"""Cross-boundary regressions for archive and model-facing retrieval pages."""

import asyncio
from dataclasses import replace
import gc
from inspect import unwrap
import json
from threading import Event
from types import SimpleNamespace
import weakref

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_archive as archive_module,
    debug_ui,
    management_history_queries as history,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.debug import DebugManager
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.runtime_hardening import (
    MAX_MODEL_TOOL_RESULT_CHARACTERS,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from tests.test_conversation_archive import FakeArchiveStorage
from tests.test_debug_ui import _Connection
from tests.test_management_history_results import _session, _turn
from tests.test_modest_scale_retrieval_regressions import (
    _knowledge_source,
    _KnowledgeStorage,
    _memory,
    _MemoryStorage,
)


async def retained_archive(text="alpha", response="reply"):
    storage = FakeArchiveStorage()
    archive = archive_module.ConversationArchive(storage, "agent")
    await archive.async_initialize()
    session = await archive.async_begin_session(
        "browser",
        user_scope("alice", source="authenticated_user"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text=text,
        assistant_text=response,
        successful=True,
    )
    return archive, storage, session


@pytest.mark.parametrize("query", ["Oscar", "bread", "Wi Fi", "C++", "C#", "R"])
async def test_persisted_compounds_possessives_and_symbolic_terms(query):
    _, storage, _ = await retained_archive(
        "Oscar\u2019s bread-making notes use Wi-Fi, C++, C# and R."
    )
    restored = archive_module.ConversationArchive(storage, "agent")
    await restored.async_initialize()
    assert (await restored.async_search("user:alice", query))["results"]
    assert (await history.archive_search_page(restored, "user:alice", query))["results"]
    assert not (await restored.async_search("user:bob", query))["results"]


@pytest.mark.parametrize("mutation", ["record", "private", "delete"])
async def test_management_snapshot_allows_mutations_and_rejects_revoked_results(
    monkeypatch, mutation
):
    archive, _, session = await retained_archive()
    entered, release = Event(), Event()
    original = history._archive_search_sync

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(history, "_archive_search_sync", blocked)
    task = asyncio.create_task(
        history.archive_search_page(archive, "user:alice", "alpha")
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not archive._lock.locked()
        if mutation == "record":
            await asyncio.wait_for(
                archive.async_record_turn(
                    session.session_id,
                    run_id="second",
                    user_text="alpha later",
                    assistant_text="later",
                    successful=True,
                ),
                1,
            )
        elif mutation == "private":
            await asyncio.wait_for(archive.async_make_private(session.session_id), 1)
        else:
            await asyncio.wait_for(
                archive.async_delete_session("user:alice", session.session_id), 1
            )
        release.set()
        if mutation == "record":
            result = await task
            assert result["total"] == 1
        else:
            with pytest.raises(HomeAssistantError, match="changed during search"):
                await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_search_heap_preserves_ties_and_reuses_bounded_weak_features(monkeypatch):
    session = _session(0, turn_count=1000)
    turns = tuple(
        replace(_turn(session.session_id, i), timestamp=session.started_at)
        for i in range(1000)
    )
    calls = 0
    original = archive_module._tokens

    def tokens(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(archive_module, "_tokens", tokens)
    peak = 0
    push = archive_module.heappush

    def track(heap, value):
        nonlocal peak
        push(heap, value)
        peak = max(peak, len(heap))

    monkeypatch.setattr(archive_module, "heappush", track)
    args = (((session, turns),), "alpha", None, None, 3, 2)
    first = archive_module._search_archive_snapshot(*args)
    cold_calls = calls
    second = archive_module._search_archive_snapshot(*args)
    assert first == second
    assert [row["turn_id"] for row in first["results"]] == [
        "turn-2",
        "turn-3",
        "turn-4",
    ]
    assert peak == 5
    assert calls - cold_calls < 10  # query and excerpts only; turn features reused
    assert archive_module._SEARCH_FEATURE_BYTES <= archive_module._SEARCH_CACHE_BYTES
    turn = replace(turns[0], turn_id="ephemeral")
    key, reference = id(turn), weakref.ref(turn)
    archive_module._search_features(turn)
    del turn
    gc.collect()
    assert reference() is None and key not in archive_module._SEARCH_FEATURES


async def test_model_search_rejects_unbounded_offset():
    archive, _, _ = await retained_archive()
    with pytest.raises(ValueError, match="offset must not exceed"):
        await archive.async_search("user:alice", "alpha", offset=10_001)


async def test_management_command_reads_every_fragment_of_a_long_turn(
    hass, management_message, monkeypatch
):
    user, assistant = ('"\\' * 15_000), ('answer "\\' * 4_000)
    archive, _, session = await retained_archive(user, assistant)
    monkeypatch.setattr(
        management_ui, "async_get_archive", lambda *_: asyncio.sleep(0, result=archive)
    )
    pieces, cursor = [], {"start_turn": 0, "text_offset": 0}
    for _ in range(10):
        result = await management_ui.async_management_command(
            hass,
            "alice",
            True,
            management_message(
                "conversations",
                "get",
                scope_id="user:alice",
                session_id=session.session_id,
                **cursor,
            ),
        )
        assert result["text_offset"] == cursor["text_offset"]
        pieces.extend(result["turns"])
        if not result["has_more"]:
            break
        next_cursor = {
            "start_turn": result["next_start_turn"],
            "text_offset": result["next_text_offset"],
        }
        assert next_cursor != cursor
        assert result["next_offset"] == next_cursor["start_turn"]
        cursor = next_cursor
    else:
        pytest.fail("cursor did not terminate")
    assert len(pieces) > 1
    assert "".join(row["user_text"] for row in pieces) == user
    assert (
        "".join(row["assistant_text"] for row in pieces)
        == assistant[: archive_module.MAX_TEXT_LENGTH]
    )


@pytest.mark.parametrize("latency", [0, 42])
async def test_debug_ui_websocket_uses_first_request_with_text(
    hass, monkeypatch, latency
):
    manager = DebugManager()
    manager.configure(enabled=True, limit=10)
    trace = manager.begin(
        entry_id="entry",
        subentry_id="agent",
        user_input={},
        incoming_conversation_id=None,
    )
    trace.start_provider_request("embeddings", (), {})
    trace.start_provider_request("responses", (), {})  # tool-only
    trace.start_provider_request("responses", (), {}).first_text_ms = latency
    manager.finish(trace, successful=True)
    monkeypatch.setattr(debug_ui, "_manager", lambda *_: manager)
    connection = _Connection()
    await unwrap(debug_ui.websocket_request_debug)(
        hass,
        connection,
        {"id": 1, "action": "runs", "entry_id": "entry", "subentry_id": "agent"},
    )
    assert not connection.errors
    assert (
        connection.results[0][1]["runs"][0]["first_text_ms"]
        == trace.summary()["first_text_ms"]
        == latency
    )


@pytest.mark.parametrize(
    "kind,limit",
    [("memory", None), ("memory", 100), ("knowledge", None), ("knowledge", 50)],
)
async def test_complete_model_list_pages_survive_outer_result_guard(hass, kind, limit):
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(entry_id="entry", data={})
    entity.subentry = SimpleNamespace(
        subentry_id="agent", data={"memory_enabled": True, "knowledge_enabled": True}
    )
    entity._temporary_memory = None
    entity._knowledge = None
    entity._memory = None
    if kind == "memory":
        rows = [_memory(i, '"\\' * 500) for i in range(101)]
        entity._memory = PersistentMemory(_MemoryStorage(rows))
        await entity._memory.async_initialize()
        records_key, id_key = "memories", "memory_id"
    else:
        rows = [_knowledge_source(i) for i in range(51)]
        for row in rows:
            row.update(title="t" * 120, description='"\\' * 250, content="valid")
        entity._knowledge = KnowledgeLibrary(_KnowledgeStorage(rows))
        await entity._knowledge.async_initialize()
        records_key, id_key = "sources", "source_id"
    seen, offset = [], 0
    for _ in range(30):
        arguments = {"offset": offset}
        if limit is not None:
            arguments["limit"] = limit
        tool = {
            "spec": {"name": f"{kind}_list"},
            "function": {"type": kind, "operation": "list"},
        }
        result = await entity._execute_function_tool(
            tool,
            llm.ToolInput(
                id="list", tool_name=f"{kind}_list", tool_args=arguments, external=True
            ),
            SimpleNamespace(context=SimpleNamespace(user_id="scale-user")),
            [],
        )
        wire = tool_result_data(result)["result"]
        assert len(wire) <= MAX_MODEL_TOOL_RESULT_CHARACTERS
        page = json.loads(wire)
        assert records_key in page, page
        seen.extend(row[id_key] for row in page[records_key])
        assert page["returned"] == len(page[records_key])
        if not page["has_more"]:
            break
        assert page["next_offset"] == offset + page["returned"] > offset
        offset = page["next_offset"]
    else:
        pytest.fail("list cursor did not terminate")
    assert len(seen) == len(set(seen)) == len(rows)
    assert set(seen) == {row[id_key] for row in rows}
