"""Real HA registration teardown must retain ownership across awaited startup."""

from __future__ import annotations

import asyncio
from typing import Any

from custom_components.extended_openai_conversation_responses import (
    conversation as product_conversation,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech


async def _converse(hass, entry_id):
    return await conversation.async_converse(
        hass=hass,
        text="Check the current registration",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


async def test_unload_during_rename_initialization_cannot_publish_after_removal(
    hass, monkeypatch
):
    agent = await _agent(hass, title="Registration ownership")
    entry = agent.entry
    entered, release = asyncio.Event(), asyncio.Event()
    removing = asyncio.Event()
    original_initialize = agent._async_initialize_optional_managers
    original_remove = agent.async_will_remove_from_hass
    original_track = product_conversation.async_track_time_interval
    original_register = product_conversation.register_legacy_agent
    intervals: list[dict[str, Any]] = []
    publications = []
    unload = None

    async def held_initialize():
        # Complete real manager initialization before holding the registration.
        # Teardown can then finish without cancelling a partial RestoreEntity add
        # or leaving test-created storage work behind the barrier.
        await original_initialize()
        entered.set()
        await release.wait()

    async def observed_remove():
        if entered.is_set() and not release.is_set():
            removing.set()
        await original_remove()

    def track(hass, action, interval):
        cancel = original_track(hass, action, interval)
        record = {"owner": action.__self__, "cancelled": False}

        def dispose():
            cancel()
            record["cancelled"] = True

        record["dispose"] = dispose
        intervals.append(record)
        return dispose

    def register(hass, entry, owner):
        publications.append(owner)
        return original_register(hass, entry, owner)

    monkeypatch.setattr(agent, "_async_initialize_optional_managers", held_initialize)
    monkeypatch.setattr(agent, "async_will_remove_from_hass", observed_remove)
    monkeypatch.setattr(product_conversation, "async_track_time_interval", track)
    monkeypatch.setattr(product_conversation, "register_legacy_agent", register)

    try:
        renamed = "conversation.registration_renamed"
        er.async_get(hass).async_update_entity(agent.entity_id, new_entity_id=renamed)
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert agent.entity_id == renamed
        assert not agent._agent_ready.is_set()

        unload = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
        await asyncio.wait_for(removing.wait(), timeout=10)
        assert not unload.done(), "Removal released ownership before startup settled"
        assert intervals == []
        assert publications == []

        release.set()
        assert await asyncio.wait_for(unload, timeout=10)
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert conversation.async_get_agent(hass, entry.entry_id) is None
        assert intervals == []
        assert publications == []

        await asyncio.wait_for(hass.async_block_till_done(), timeout=10)
        assert intervals == [], "A removed registration installed a retention interval"
        assert publications == [], "A removed registration reclaimed the entry alias"
        assert conversation.async_get_agent(hass, entry.entry_id) is None

        # A later legitimate registration must still publish and process requests.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        current = conversation.async_get_agent(hass, entry.entry_id)
        assert current is not None and current is not agent
        assert publications == [current]
        assert len(intervals) == 1
        assert intervals[0]["owner"] is current
        assert not intervals[0]["cancelled"]
        wire = _install_wire(
            monkeypatch, current, [_chat_sse_text("The current registration is ready")]
        )
        result = await _converse(hass, entry.entry_id)
        assert _speech(result) == "The current registration is ready"
        assert len(wire.requests) == 1

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert all(record["cancelled"] for record in intervals)
        assert conversation.async_get_agent(hass, entry.entry_id) is None
    finally:
        release.set()
        if unload is not None:
            await asyncio.gather(unload, return_exceptions=True)
        await hass.async_block_till_done()
        # Preserve a failed leak assertion while keeping genuine timer handles
        # from contaminating unrelated fixture teardown or later tests.
        for record in intervals:
            if not record["cancelled"]:
                record["dispose"]()
        product_conversation.unregister_legacy_agent(hass, entry, agent)


async def test_request_waiting_on_retired_readiness_cannot_enter_new_registration(
    hass, monkeypatch
):
    agent = await _agent(hass, title="Queued registration ownership")
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("A request owned by the current registration")],
    )
    entered = asyncio.Event()

    class ObservedReady(asyncio.Event):
        async def wait(self):
            entered.set()
            return await super().wait()

    old_ready = ObservedReady()
    agent._agent_ready = old_ready
    stale = asyncio.create_task(_converse(hass, agent.entry.entry_id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert not wire.requests

        new_ready = asyncio.Event()
        agent._agent_ready = new_ready
        agent._agent_initialization_failed = False
        old_ready.set()
        rejected = await asyncio.wait_for(stale, timeout=10)
        assert rejected.response.error_code is not None
        assert "not ready" in rejected.response.as_dict()["speech"]["plain"]["speech"]
        assert not new_ready.is_set()
        assert not wire.requests

        new_ready.set()
        accepted = await _converse(hass, agent.entry.entry_id)
        assert _speech(accepted) == "A request owned by the current registration"
        assert len(wire.requests) == 1
    finally:
        old_ready.set()
        agent._agent_ready.set()
        if not stale.done():
            stale.cancel()
        await asyncio.gather(stale, return_exceptions=True)
