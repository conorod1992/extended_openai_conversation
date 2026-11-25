"""Unreadable agent journals may contain a shared provider transaction."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components import extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses.provider_credentials import (
    require_provider_restore_complete,
)
from custom_components.extended_openai_conversation_responses.restore_recovery import (
    _journal_store,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_backup_transfer_protocol import _conversation_subentry, _entry


@pytest.fixture
def real_store_io():
    """Exercise the strict disk reader rather than fixture storage."""


@pytest.mark.parametrize("damage", ["truncated file", "missing transaction fields"])
async def test_unreadable_live_journal_blocks_only_its_provider(
    hass, real_store_io, monkeypatch, damage
):
    affected = _entry()
    healthy = _entry()
    affected.add_to_hass(hass)
    healthy.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        affected, data={**affected.data, "api_key": "sk-affected"}
    )
    owner = _conversation_subentry(affected)
    store = _journal_store(hass, affected.entry_id, owner.subentry_id)
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A truncated envelope gives no reliable evidence about credential ownership.
    damaged = (
        b'{"version":1,"data":'
        if damage == "truncated file"
        else json.dumps({"key": store.key, "version": store.version, "data": {}}).encode()
    )
    path.write_bytes(damaged)

    authenticate = AsyncMock(wraps=integration.get_authenticated_client)
    monkeypatch.setattr(integration, "get_authenticated_client", authenticate)
    assert await hass.config_entries.async_setup(healthy.entry_id)
    assert healthy.state is ConfigEntryState.LOADED
    assert affected.state is ConfigEntryState.SETUP_RETRY
    assert {call.kwargs["api_key"] for call in authenticate.await_args_list} == {
        healthy.data["api_key"]
    }
    assert path.read_bytes() == damaged
    with pytest.raises(HomeAssistantError, match="shared provider connection"):
        require_provider_restore_complete(hass, affected.entry_id)
    require_provider_restore_complete(hass, healthy.entry_id)

    # Simulate an operator moving the damaged file into their recovery backup.
    preserved = path.with_name(path.name + ".saved")
    path.rename(preserved)
    assert await hass.config_entries.async_reload(affected.entry_id)
    assert affected.state is ConfigEntryState.LOADED
    require_provider_restore_complete(hass, affected.entry_id)
    assert preserved.read_bytes() == damaged
