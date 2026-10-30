"""Unavailable optional Guest status cannot reject a committed Function repair."""

from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.guest_mode import (
    async_get_guest_mode,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_acknowledgement_recovery import (
    function_agent as function_agent,
    send,
)


@pytest.mark.parametrize(
    "action", ["save_one", "delete_one", "save", "configuration_save"]
)
@pytest.mark.parametrize("guest_unavailable", [False, True])
async def test_repair_acknowledgement_survives_optional_guest_status_and_next_save(
    function_agent,
    monkeypatch,
    action,
    guest_unavailable,
):
    hass, entry, subentry, _rules, _store = function_agent
    guest = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
    await guest.async_disable_trusted()
    guest_bytes = Path(guest._store.path).read_bytes()
    if guest_unavailable:
        save, load = guest._store.async_save, guest._store.async_load
        monkeypatch.setattr(
            guest._store, "async_save", AsyncMock(side_effect=OSError("write failed"))
        )
        monkeypatch.setattr(
            guest._store, "async_load", AsyncMock(side_effect=OSError("read failed"))
        )
        with pytest.raises(OSError):
            await guest.async_update_trusted(indefinite=True)
        monkeypatch.setattr(guest._store, "async_save", save)
        monkeypatch.setattr(guest._store, "async_load", load)
    tools = yaml.safe_load(subentry.data["functions"])
    repaired = {
        **deepcopy(tools[0]),
        "spec": {**tools[0]["spec"], "name": "broken_tool"},
    }
    broken = deepcopy(repaired)
    broken["function"]["name"] = "missing_legacy_implementation"
    subentry.data = {**subentry.data, "functions": yaml.safe_dump([*tools, broken])}
    extra = (
        {"index": 1, "tool": repaired}
        if action == "save_one"
        else {"index": 1}
        if action == "delete_one"
        else {"tools": tools}
        if action == "save"
        else {"config": {"prompt": "Saved during Guest recovery"}}
    )
    first = await send(function_agent, action, section="function_repair", **extra)
    assert first.send_error.call_args is None, first.send_error.call_args
    saved = first.send_result.call_args.args[1]
    assert saved["revision"] and isinstance(saved["config"]["functions"], list)
    assert isinstance(saved["config"]["function_groups"], list)
    assert ("agent" in saved) is (not guest_unavailable)
    if action == "configuration_save":
        assert subentry.data["prompt"] == "Saved during Guest recovery"
        assert yaml.safe_load(subentry.data["functions"])[1] == broken
    elif action == "save_one":
        assert (
            yaml.safe_load(subentry.data["functions"])[1]["function"]
            == repaired["function"]
        )
    else:
        assert len(yaml.safe_load(subentry.data["functions"])) == 1
    # Adopt the actual acknowledged revision for the next user action.
    second = await send(
        function_agent,
        "set_enabled",
        revision=saved["revision"],
        name="native_probe",
        enabled=False,
    )
    second.send_error.assert_not_called()
    assert yaml.safe_load(subentry.data["functions"])[0]["enabled"] is False
    assert hass.config_entries.async_update_subentry.call_count == 2
    assert Path(guest._store.path).read_bytes() == guest_bytes
    if guest_unavailable:
        with pytest.raises(HomeAssistantError, match="unavailable"):
            guest.is_active()
