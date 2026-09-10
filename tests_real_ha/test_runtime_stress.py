"""Bounded runtime stress tests against Home Assistant's genuine test harness.

These tests deliberately force overlap with asyncio Events rather than relying on
scheduler timing.  They are small enough to remain useful in normal acceptance CI
while repeatedly exercising the lifecycle and request-isolation boundaries most
likely to fail under concurrent use.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.template import (
    DATA_TEMPLATE_MANAGER,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

_CONCURRENT_CONVERSATIONS = 8
_CONCURRENT_ROUNDS = 3
_CANCELLATION_REQUESTS = 6
_RELOAD_CYCLES = 8


class _ArrivalGate:
    """Release a batch only after every expected coroutine has arrived."""

    def __init__(self, expected: int) -> None:
        self._expected = expected
        self._arrived = 0
        self._lock = asyncio.Lock()
        self._all_arrived = asyncio.Event()
        self._release = asyncio.Event()

    async def arrive_and_wait(self) -> None:
        """Record this arrival and wait for the test to release the batch."""
        async with self._lock:
            self._arrived += 1
            if self._arrived == self._expected:
                self._all_arrived.set()
        await self._release.wait()

    async def wait_until_full(self) -> None:
        """Wait until all expected coroutines are simultaneously blocked here."""
        await self._all_arrived.wait()

    def release(self) -> None:
        """Release every coroutine currently waiting at the gate."""
        self._release.set()


def _subentry(subentry_type: str, title: str, data: dict | None = None) -> dict:
    """Return storage-shaped subentry data for MockConfigEntry."""
    return {
        "data": data or {},
        "subentry_type": subentry_type,
        "title": title,
        "unique_id": None,
    }


def _make_entry(title: str = "Stress") -> MockConfigEntry:
    """Create a conversation-only entry that never authenticates externally."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-stress-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[_subentry("conversation", f"{title} Conversation")],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up one entry through Home Assistant's config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _agent(hass: HomeAssistant, entry: MockConfigEntry) -> ExtendedOpenAIAgentEntity:
    """Return the loaded conversation agent for an entry."""
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)
    return agent


def _content_strings(log: conversation.ChatLog) -> list[str]:
    """Return string content currently present in a chat log."""
    return [
        item.content
        for item in log.content
        if isinstance(getattr(item, "content", None), str)
    ]


def _speech(result: conversation.ConversationResult) -> str:
    """Return plain speech from a public Conversation result."""
    return result.response.as_dict()["speech"]["plain"]["speech"]


def _registry_entity_ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    """Return stable entity-registry IDs owned by an entry."""
    registry = er.async_get(hass)
    return {
        row.entity_id
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


def _assert_no_foreign_markers(contents: Iterable[str], own_marker: str) -> None:
    """Assert a request history contains no marker from another conversation."""
    joined = "\n".join(contents)
    assert own_marker in joined
    for index in range(_CONCURRENT_CONVERSATIONS):
        marker = f"stress-marker-{index}"
        if marker != own_marker:
            assert marker not in joined


@pytest.mark.asyncio
async def test_concurrent_public_conversations_keep_histories_isolated(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlapping public Assist calls never acquire a sibling's conversation history."""
    entry = _make_entry("Concurrent isolation")
    await _setup_entry(hass, entry)
    agent = _agent(hass, entry)

    active_gate: _ArrivalGate | None = None
    observed_histories: dict[str, list[list[str]]] = {
        f"stress-marker-{index}": [] for index in range(_CONCURRENT_CONVERSATIONS)
    }

    async def model(log: conversation.ChatLog, **kwargs) -> None:
        del kwargs
        nonlocal active_gate
        contents = _content_strings(log)
        current = contents[-1]
        marker = next(
            value
            for value in observed_histories
            if value in current
        )
        _assert_no_foreign_markers(contents, marker)
        observed_histories[marker].append(contents)

        if current.startswith("follow-up"):
            assert active_gate is not None
            await active_gate.arrive_and_wait()

        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id,
                content=f"ack:{current}",
            )
        )
        return None

    monkeypatch.setattr(agent, "_async_handle_chat_log", model)

    conversation_ids: dict[str, str] = {}
    for index in range(_CONCURRENT_CONVERSATIONS):
        marker = f"stress-marker-{index}"
        seed_text = f"seed {marker}"
        result = await conversation.async_converse(
            hass=hass,
            text=seed_text,
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.conversation_id is not None
        assert _speech(result) == f"ack:{seed_text}"
        conversation_ids[marker] = result.conversation_id

    for round_number in range(_CONCURRENT_ROUNDS):
        active_gate = _ArrivalGate(_CONCURRENT_CONVERSATIONS)
        tasks = []
        expected_speech = []
        for index in range(_CONCURRENT_CONVERSATIONS):
            marker = f"stress-marker-{index}"
            text = f"follow-up round-{round_number} {marker}"
            expected_speech.append(f"ack:{text}")
            tasks.append(
                asyncio.create_task(
                    conversation.async_converse(
                        hass=hass,
                        text=text,
                        conversation_id=conversation_ids[marker],
                        context=Context(),
                        language="en",
                        agent_id=entry.entry_id,
                    )
                )
            )

        await active_gate.wait_until_full()
        active_gate.release()
        results = await asyncio.gather(*tasks)

        assert [_speech(result) for result in results] == expected_speech
        assert [result.conversation_id for result in results] == [
            conversation_ids[f"stress-marker-{index}"]
            for index in range(_CONCURRENT_CONVERSATIONS)
        ]

    for marker, histories in observed_histories.items():
        assert len(histories) == 1 + _CONCURRENT_ROUNDS
        for contents in histories:
            _assert_no_foreign_markers(contents, marker)


@pytest.mark.asyncio
async def test_cancellation_under_load_does_not_break_sibling_or_next_request(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling half a blocked request batch leaves siblings and a fresh call healthy."""
    entry = _make_entry("Cancellation load")
    await _setup_entry(hass, entry)
    agent = _agent(hass, entry)

    gate = _ArrivalGate(_CANCELLATION_REQUESTS)
    cancelled_in_handler: set[str] = set()
    entered: set[str] = set()

    async def model(log: conversation.ChatLog, **kwargs) -> None:
        del kwargs
        contents = _content_strings(log)
        current = contents[-1]

        if current.startswith("cancel-load-"):
            entered.add(current)
            try:
                await gate.arrive_and_wait()
            except asyncio.CancelledError:
                cancelled_in_handler.add(current)
                raise
        else:
            assert current == "fresh request after cancellation"
            assert all("cancel-load-" not in content for content in contents)

        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id,
                content=f"ack:{current}",
            )
        )
        return None

    monkeypatch.setattr(agent, "_async_handle_chat_log", model)

    tasks = [
        asyncio.create_task(
            conversation.async_converse(
                hass=hass,
                text=f"cancel-load-{index}",
                conversation_id=None,
                context=Context(),
                language="en",
                agent_id=entry.entry_id,
            )
        )
        for index in range(_CANCELLATION_REQUESTS)
    ]

    await gate.wait_until_full()
    cancelled_indexes = {0, 2, 4}
    for index in cancelled_indexes:
        tasks[index].cancel()

    cancelled_results = await asyncio.gather(
        *(tasks[index] for index in sorted(cancelled_indexes)),
        return_exceptions=True,
    )
    assert all(isinstance(result, asyncio.CancelledError) for result in cancelled_results)

    gate.release()
    survivor_indexes = [
        index for index in range(_CANCELLATION_REQUESTS) if index not in cancelled_indexes
    ]
    survivor_results = await asyncio.gather(*(tasks[index] for index in survivor_indexes))

    assert entered == {f"cancel-load-{index}" for index in range(_CANCELLATION_REQUESTS)}
    assert cancelled_in_handler == {
        f"cancel-load-{index}" for index in cancelled_indexes
    }
    assert [_speech(result) for result in survivor_results] == [
        f"ack:cancel-load-{index}" for index in survivor_indexes
    ]

    fresh = await conversation.async_converse(
        hass=hass,
        text="fresh request after cancellation",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(fresh) == "ack:fresh request after cancellation"


@pytest.mark.asyncio
async def test_repeated_setup_use_unload_reload_keeps_runtime_healthy(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated real-HA reload cycles preserve a usable agent and stable registry rows."""
    entry = _make_entry("Reload stress")
    await _setup_entry(hass, entry)

    expected_entity_ids = _registry_entity_ids(hass, entry)
    assert expected_entity_ids
    managers = []

    for cycle in range(_RELOAD_CYCLES):
        agent = _agent(hass, entry)
        manager = hass.data[DOMAIN][DATA_TEMPLATE_MANAGER]
        assert all(manager is not previous for previous in managers)
        managers.append(manager)

        async def model(log: conversation.ChatLog, **kwargs) -> None:
            del kwargs
            current = _content_strings(log)[-1]
            log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=agent.entity_id,
                    content=f"cycle-ok:{current}",
                )
            )
            return None

        monkeypatch.setattr(agent, "_async_handle_chat_log", model)
        text = f"runtime cycle {cycle}"
        result = await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )
        assert _speech(result) == f"cycle-ok:{text}"
        assert _registry_entity_ids(hass, entry) == expected_entity_ids

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert conversation.async_get_agent(hass, entry.entry_id) is None
        assert DATA_TEMPLATE_MANAGER not in hass.data.get(DOMAIN, {})

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert _registry_entity_ids(hass, entry) == expected_entity_ids

    # Prove the final reloaded instance is usable too, rather than stopping immediately
    # after the last successful setup.
    final_agent = _agent(hass, entry)

    async def final_model(log: conversation.ChatLog, **kwargs) -> None:
        del kwargs
        current = _content_strings(log)[-1]
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=final_agent.entity_id,
                content="final runtime healthy",
            )
        )
        return None

    monkeypatch.setattr(final_agent, "_async_handle_chat_log", final_model)
    final_result = await conversation.async_converse(
        hass=hass,
        text="final runtime probe",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(final_result) == "final runtime healthy"
