"""Nightly public Assist cancellation during an in-flight provider response."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _speech,
)
from tests_stress.conftest import record


async def _say(hass: HomeAssistant, entry_id: str, text: str) -> Any:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


@pytest.mark.asyncio
async def test_unload_cancels_blocked_provider_turn_and_new_runtime_recovers(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
) -> None:
    """Unloading during an HTTP response leaves the recreated agent usable."""
    old_agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    entry_id = old_agent.entry.entry_id
    wire = _install_wire(
        monkeypatch, old_agent, [_chat_sse_text("Old response must be discarded.")]
    )
    original_send = wire.send
    response_ready = asyncio.Event()
    release = asyncio.Event()

    async def blocked_send(*args: Any, **kwargs: Any) -> Any:
        response = await original_send(*args, **kwargs)
        response_ready.set()
        await release.wait()
        return response

    monkeypatch.setattr(_raw_client(old_agent)._client, "send", blocked_send)
    turn = asyncio.create_task(_say(hass, entry_id, "Begin an interrupted turn"))
    try:
        await asyncio.wait_for(response_ready.wait(), timeout=10)
        assert len(wire.requests) == 1
        assert await asyncio.wait_for(
            hass.config_entries.async_unload(entry_id), timeout=10
        )
        assert conversation.async_get_agent(hass, entry_id) is None
    finally:
        turn.cancel()
        release.set()
        with suppress(asyncio.CancelledError):
            await turn

    assert turn.done()
    assert await hass.config_entries.async_setup(entry_id)
    await hass.async_block_till_done()
    new_agent = conversation.async_get_agent(hass, entry_id)
    assert new_agent is not None
    assert new_agent is not old_agent
    fresh_wire = _install_wire(
        monkeypatch, new_agent, [_chat_sse_text("New runtime is healthy.")]
    )
    result = await _say(hass, entry_id, "Continue after reload")
    assert _speech(result) == "New runtime is healthy."
    assert len(fresh_wire.requests) == 1
    record(
        stress_trace,
        "summary",
        layer="Real HA",
        active_unload_recoveries=1,
        provider_requests=2,
    )
