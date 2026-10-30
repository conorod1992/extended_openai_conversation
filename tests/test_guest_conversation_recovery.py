"""A running conversation retries required Guest state without a panel refresh."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_configuration as runtime,
    conversation,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_GUEST_MODE_ENABLED,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
    async_get_guest_mode,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_configuration_lifecycle_hardening import _disabled_runtime_config


@pytest.mark.parametrize("after_write", [False, True])
async def test_conversation_recovers_guest_state_and_keeps_corruption_fail_closed(
    hass,
    entry_agent,
    entry_input,
    monkeypatch,
    caplog,
    after_write,
):
    options = {
        **_disabled_runtime_config(),
        "functions": "[]",
        CONF_GUEST_MODE_ENABLED: True,
    }
    entry_agent.subentry.data = options
    entry_agent._set_subsystem_status = lambda *_args, **_kwargs: None
    guest = await async_get_guest_mode(hass, "entry", "agent")
    await guest.async_update_trusted(indefinite=True)
    entry_agent._guest_mode = guest
    monkeypatch.setattr(
        conversation,
        "async_reconcile_runtime_configuration",
        runtime.async_reconcile_runtime_configuration,
    )
    observed = []

    async def continue_request(_request):
        active = entry_agent._guest_mode.is_active()
        observed.append(active)
        return "processed"

    entry_agent._async_process_with_continuity = continue_request
    assert await entry_agent.async_process(entry_input) == "processed"
    write = guest._store._write_data
    load = guest._store.async_load
    save = guest._store.async_save

    def fail(data):
        if after_write:
            write(data)
        raise OSError("PRIVATE-GUEST-WRITE")

    monkeypatch.setattr(guest._store, "_write_data", fail)
    monkeypatch.setattr(
        guest._store, "async_load", AsyncMock(side_effect=OSError("PRIVATE-GUEST-READ"))
    )
    with pytest.raises(OSError):
        await guest.async_disable_trusted()
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await entry_agent.async_process(entry_input)
    assert observed == [True]
    monkeypatch.setattr(guest._store, "_write_data", write)
    monkeypatch.setattr(guest._store, "async_load", load)
    path = Path(guest._store.path)
    authoritative = json.loads(path.read_text())["data"]
    # A readable but damaged schedule must still prevent processing and writes.
    await save({"schedule": {"active_from": "PRIVATE-DAMAGED-SCHEDULE"}})
    damaged = path.read_bytes()
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await entry_agent.async_process(entry_input)
    assert path.read_bytes() == damaged and observed == [True]
    # Administrator repair, followed only by the ordinary conversation entry.
    await save(authoritative)
    reads = AsyncMock(wraps=load)
    monkeypatch.setattr(guest._store, "async_load", reads)
    assert await entry_agent.async_process(entry_input) == "processed"
    fresh = GuestModeManager(hass, "entry", "agent")
    await fresh.async_initialize()
    assert observed == [True, fresh.is_active()]
    assert fresh.is_active() is (not after_write)
    await entry_agent._async_execute_guest_mode_tool({"make_indefinite": True})
    assert await entry_agent.async_process(entry_input) == "processed"
    assert observed == [True, not after_write, True]
    reads.assert_awaited_once()
    final = GuestModeManager(hass, "entry", "agent")
    await final.async_initialize()
    assert final.is_active() and final.schedule.active_until is None
    assert "PRIVATE-GUEST" not in caplog.text and "PRIVATE-DAMAGED" not in caplog.text
