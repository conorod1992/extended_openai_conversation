"""Bounded lifecycle and persistence contention tests against genuine Home Assistant.

The tests in this module synchronize deliberately with asyncio Events. They exercise
real config-entry unload/reload and Home Assistant Store writes without relying on
wall-clock races or high iteration counts, so they remain suitable for normal PR CI.
"""

from __future__ import annotations

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses import memory as memory_module
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant

_PERSISTENCE_WRITES = 8
_WAIT_TIMEOUT = 10


class _StartGate:
    """Hold a batch until every coroutine is ready to start its operation."""

    def __init__(self, expected: int) -> None:
        self._expected = expected
        self._arrived = 0
        self._lock = asyncio.Lock()
        self._all_arrived = asyncio.Event()
        self._release = asyncio.Event()

    async def arrive_and_wait(self) -> None:
        """Register one waiter, then block until the complete batch is released."""
        async with self._lock:
            self._arrived += 1
            if self._arrived == self._expected:
                self._all_arrived.set()
        await self._release.wait()

    async def wait_until_full(self) -> None:
        """Wait until every expected coroutine is blocked at the gate."""
        await self._all_arrived.wait()

    def release(self) -> None:
        """Release the full batch together."""
        self._release.set()


def _subentry(title: str, data: dict | None = None) -> dict:
    """Return one storage-shaped conversation subentry."""
    return {
        "data": data or {},
        "subentry_type": "conversation",
        "title": title,
        "unique_id": None,
    }


def _make_entry(
    title: str,
    *,
    conversation_data: dict | None = None,
) -> MockConfigEntry:
    """Create a local-only conversation entry with no external authentication."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        data={
            CONF_API_KEY: "sk-lifecycle-concurrency",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[_subentry(f"{title} Conversation", conversation_data)],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up an entry through Home Assistant's actual config-entry manager."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _agent(hass: HomeAssistant, entry: MockConfigEntry) -> ExtendedOpenAIAgentEntity:
    """Return the currently registered conversation agent for an entry."""
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)
    return agent


def _speech(result: conversation.ConversationResult) -> str:
    """Return plain speech from a public Assist result."""
    return result.response.as_dict()["speech"]["plain"]["speech"]


def _current_text(log: conversation.ChatLog) -> str:
    """Return the latest textual chat-log item."""
    return next(
        item.content
        for item in reversed(log.content)
        if isinstance(getattr(item, "content", None), str)
    )


def _install_blocking_model(
    monkeypatch: pytest.MonkeyPatch,
    agent: ExtendedOpenAIAgentEntity,
    entered: asyncio.Event,
    release: asyncio.Event,
    *,
    response_prefix: str,
) -> None:
    """Block one agent inside its model seam until the lifecycle action completes."""

    async def model(log: conversation.ChatLog, **kwargs) -> None:
        del kwargs
        current = _current_text(log)
        entered.set()
        await release.wait()
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id,
                content=f"{response_prefix}:{current}",
            )
        )
        return None

    monkeypatch.setattr(agent, "_async_handle_chat_log", model)


def _install_echo_model(
    monkeypatch: pytest.MonkeyPatch,
    agent: ExtendedOpenAIAgentEntity,
    *,
    response_prefix: str,
) -> None:
    """Install a provider-free model seam for a post-lifecycle health probe."""

    async def model(log: conversation.ChatLog, **kwargs) -> None:
        del kwargs
        current = _current_text(log)
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id,
                content=f"{response_prefix}:{current}",
            )
        )
        return None

    monkeypatch.setattr(agent, "_async_handle_chat_log", model)


async def _converse(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    text: str,
) -> conversation.ConversationResult:
    """Run one public Assist request against the configured entry."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )


@pytest.mark.asyncio
async def test_unload_during_in_flight_request_does_not_strand_runtime(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked request can finish after unload and the entry can be set up cleanly."""
    entry = _make_entry("In-flight unload")
    await _setup_entry(hass, entry)
    old_agent = _agent(hass, entry)

    entered = asyncio.Event()
    release = asyncio.Event()
    _install_blocking_model(
        monkeypatch,
        old_agent,
        entered,
        release,
        response_prefix="old-unload",
    )

    request_task = asyncio.create_task(_converse(hass, entry, "request crossing unload"))
    await asyncio.wait_for(entered.wait(), timeout=_WAIT_TIMEOUT)
    assert not request_task.done()

    assert await asyncio.wait_for(
        hass.config_entries.async_unload(entry.entry_id),
        timeout=_WAIT_TIMEOUT,
    )
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is None
    # The request is still deliberately blocked in the old agent. Unload must not
    # manufacture a response, lose the task, or require that request to finish first.
    assert not request_task.done()

    release.set()
    old_result = await asyncio.wait_for(request_task, timeout=_WAIT_TIMEOUT)
    assert _speech(old_result) == "old-unload:request crossing unload"

    await _setup_entry(hass, entry)
    new_agent = _agent(hass, entry)
    assert new_agent is not old_agent
    _install_echo_model(
        monkeypatch,
        new_agent,
        response_prefix="new-after-unload",
    )
    fresh = await _converse(hass, entry, "fresh after unload")
    assert _speech(fresh) == "new-after-unload:fresh after unload"


@pytest.mark.asyncio
async def test_reload_during_in_flight_request_uses_fresh_runtime_independently(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reload replaces the registered agent while an old request remains in flight."""
    entry = _make_entry("In-flight reload")
    await _setup_entry(hass, entry)
    old_agent = _agent(hass, entry)

    entered = asyncio.Event()
    release = asyncio.Event()
    _install_blocking_model(
        monkeypatch,
        old_agent,
        entered,
        release,
        response_prefix="old-reload",
    )

    old_request = asyncio.create_task(_converse(hass, entry, "request crossing reload"))
    await asyncio.wait_for(entered.wait(), timeout=_WAIT_TIMEOUT)
    assert not old_request.done()

    await asyncio.wait_for(
        hass.config_entries.async_reload(entry.entry_id),
        timeout=_WAIT_TIMEOUT,
    )
    assert entry.state is ConfigEntryState.LOADED
    new_agent = _agent(hass, entry)
    assert new_agent is not old_agent
    assert not old_request.done()

    # The replacement runtime must be usable before the stale generation is allowed
    # to finish, proving the two generations do not depend on each other completing.
    _install_echo_model(
        monkeypatch,
        new_agent,
        response_prefix="new-after-reload",
    )
    fresh = await _converse(hass, entry, "fresh while old request is blocked")
    assert _speech(fresh) == "new-after-reload:fresh while old request is blocked"
    assert not old_request.done()

    release.set()
    old_result = await asyncio.wait_for(old_request, timeout=_WAIT_TIMEOUT)
    assert _speech(old_result) == "old-reload:request crossing reload"


@pytest.mark.asyncio
async def test_concurrent_memory_writes_survive_fresh_store_rehydration(
    hass: HomeAssistant,
) -> None:
    """Contending durable writes are serialized without losing any committed record."""
    entry = _make_entry(
        "Persistence contention",
        conversation_data={CONF_MEMORY_MODE: MEMORY_MODE_MANUAL},
    )
    await _setup_entry(hass, entry)
    first_agent = _agent(hass, entry)
    assert first_agent._memory is not None
    memory = first_agent._memory

    gate = _StartGate(_PERSISTENCE_WRITES)

    async def add_memory(index: int) -> dict:
        await gate.arrive_and_wait()
        return await memory.async_add(
            "user:stress",
            f"Concurrent durable fact {index}",
            "stress",
            "explicit",
            key=f"stress.concurrent.{index}",
        )

    tasks = [
        asyncio.create_task(add_memory(index)) for index in range(_PERSISTENCE_WRITES)
    ]
    await asyncio.wait_for(gate.wait_until_full(), timeout=_WAIT_TIMEOUT)
    assert all(not task.done() for task in tasks)
    gate.release()
    results = await asyncio.wait_for(
        asyncio.gather(*tasks),
        timeout=_WAIT_TIMEOUT,
    )
    assert [result["status"] for result in results] == [
        "created"
    ] * _PERSISTENCE_WRITES

    before = await memory.async_backup_data()
    before_records = before["memories"]
    assert {record["key"] for record in before_records} == {
        f"stress.concurrent.{index}" for index in range(_PERSISTENCE_WRITES)
    }

    # Do not trust only the live in-memory collection: unload the entry, discard the
    # process-local manager cache, then reconstruct it from Home Assistant's .storage.
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.data.pop(memory_module._MEMORY_MANAGERS, None)
    await _setup_entry(hass, entry)

    second_agent = _agent(hass, entry)
    assert second_agent is not first_agent
    assert second_agent._memory is not None
    assert second_agent._memory is not memory
    after = await second_agent._memory.async_backup_data()
    assert after == before
