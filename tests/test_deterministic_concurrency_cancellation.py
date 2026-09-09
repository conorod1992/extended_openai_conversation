"""Deterministic gap coverage for request-local concurrency and cancellation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.components import conversation
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
    current_tool_recovery_state,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    ToolSnapshot,
    current_snapshot,
    tool_snapshot_scope,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)


class _BlockingLoadStorage:
    """Knowledge storage whose first load is released explicitly by the test."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {"sources": []}
        self.load_calls = 0
        self.load_started = asyncio.Event()
        self.release_load = asyncio.Event()

    async def async_load(self) -> dict[str, Any]:
        self.load_calls += 1
        self.load_started.set()
        await self.release_load.wait()
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)


class _BlockingFirstSaveStorage:
    """Knowledge storage whose first save is held at the persistence boundary."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {"sources": []}
        self.save_calls = 0
        self.first_save_started = asyncio.Event()
        self.release_first_save = asyncio.Event()

    async def async_load(self) -> dict[str, Any]:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        candidate = deepcopy(data)
        self.save_calls += 1
        if self.save_calls == 1:
            self.first_save_started.set()
            await self.release_first_save.wait()
        self.data = candidate


async def test_cancelled_tool_snapshot_scope_does_not_leak_or_disturb_sibling() -> None:
    """Cancelling one request resets its ContextVar while a sibling keeps its own."""
    alice_snapshot = ToolSnapshot(unavailable_sources=["alice"])
    bob_snapshot = ToolSnapshot(unavailable_sources=["bob"])
    alice_entered = asyncio.Event()
    bob_entered = asyncio.Event()
    alice_cleaned = asyncio.Event()
    alice_release = asyncio.Event()

    async def alice_request() -> None:
        try:
            with tool_snapshot_scope(alice_snapshot):
                assert current_snapshot() is alice_snapshot
                alice_entered.set()
                await alice_release.wait()
        except asyncio.CancelledError:
            assert current_snapshot().unavailable_sources == []
            alice_cleaned.set()
            raise

    async def bob_request() -> None:
        with tool_snapshot_scope(bob_snapshot):
            assert current_snapshot() is bob_snapshot
            bob_entered.set()
            await alice_cleaned.wait()
            assert current_snapshot() is bob_snapshot

    alice = asyncio.create_task(alice_request())
    bob = asyncio.create_task(bob_request())
    await alice_entered.wait()
    await bob_entered.wait()

    alice.cancel()
    with pytest.raises(asyncio.CancelledError):
        await alice
    await bob

    assert current_snapshot().tools == {}
    assert current_snapshot().unavailable_sources == []


async def test_cancelled_tool_execution_resets_recovery_context_and_closes_call(
    hass,
) -> None:
    """Cancellation during dispatch leaves no request ContextVar or open tool call."""
    tool = {
        "spec": {
            "name": "cancel_probe",
            "description": "Block until the caller cancels the tool.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "function": {"type": "knowledge", "operation": "search"},
    }
    tool_call = llm.ToolInput(
        id="cancel-call",
        tool_name="cancel_probe",
        tool_args={},
        external=True,
    )
    chat_log = conversation.ChatLog(hass, "cancel-exchange")
    chat_log.content[0] = conversation.SystemContent(content="System")
    chat_log.async_add_user_content(conversation.UserContent(content="Run the tool"))
    chat_log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(
            agent_id="conversation.cancel",
            tool_calls=[tool_call],
        )
    )

    execution_started = asyncio.Event()
    never_release = asyncio.Event()
    recovery_state = ToolRecoveryState(enabled=True)

    async def execute_function_tool(
        _function_tool: dict[str, Any],
        _tool_input: llm.ToolInput,
        _llm_context: llm.LLMContext | None,
        _exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        assert current_tool_recovery_state() is recovery_state
        execution_started.set()
        await never_release.wait()
        raise AssertionError("cancelled execution unexpectedly resumed")

    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.cancel",
        _execute_function_tool=execute_function_tool,
    )
    budget = FunctionCallBudget(1)
    context_after_cancel: list[ToolRecoveryState | None] = []

    async def run_exchange() -> None:
        try:
            await async_execute_tool_exchange(
                entity,
                chat_log,
                [tool_call],
                [tool],
                budget,
                None,
                [],
                recovery_state=recovery_state,
            )
        except asyncio.CancelledError:
            context_after_cancel.append(current_tool_recovery_state())
            raise

    exchange = asyncio.create_task(run_exchange())
    await execution_started.wait()
    exchange.cancel()

    with pytest.raises(asyncio.CancelledError):
        await exchange

    assert context_after_cancel == [None]
    assert budget.used == 1
    assert recovery_state.used == 0

    results = [
        item
        for item in chat_log.content
        if isinstance(item, conversation.ToolResultContent)
    ]
    assert len(results) == 1
    assert results[0].tool_call_id == "cancel-call"
    result = results[0].tool_result["result"]
    assert result["status"] == "error"
    assert "CancelledError" in result["error"]


async def test_concurrent_knowledge_initialization_loads_storage_once() -> None:
    """A second initializer waits for the first and does not start another load."""
    storage = _BlockingLoadStorage()
    library = KnowledgeLibrary(storage)
    second_entered = asyncio.Event()

    first = asyncio.create_task(library.async_initialize())
    await storage.load_started.wait()

    async def initialize_again() -> None:
        second_entered.set()
        await library.async_initialize()

    second = asyncio.create_task(initialize_again())
    await second_entered.wait()

    assert storage.load_calls == 1
    assert not second.done()

    storage.release_load.set()
    await asyncio.gather(first, second)

    assert storage.load_calls == 1
    assert library.total_source_count == 0


async def test_concurrent_knowledge_writes_serialize_without_losing_update() -> None:
    """A write waiting behind an in-flight save is included in the next snapshot."""
    storage = _BlockingFirstSaveStorage()
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    second_entered = asyncio.Event()

    first = asyncio.create_task(
        library.async_create("First", "Reference", "First body")
    )
    await storage.first_save_started.wait()

    async def create_second() -> Any:
        second_entered.set()
        return await library.async_create("Second", "Reference", "Second body")

    second = asyncio.create_task(create_second())
    await second_entered.wait()

    assert storage.save_calls == 1
    assert not second.done()

    storage.release_first_save.set()
    first_source, second_source = await asyncio.gather(first, second)

    assert storage.save_calls == 2
    assert {item["source_id"] for item in await library.async_list()} == {
        first_source.source_id,
        second_source.source_id,
    }
    assert {item["source_id"] for item in storage.data["sources"]} == {
        first_source.source_id,
        second_source.source_id,
    }
