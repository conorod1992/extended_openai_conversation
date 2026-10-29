"""A restore journal receipt requires file evidence, including during shutdown."""

from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.restore_recovery import (
    _async_write_journal_verified,
    _journal_store,
    _JournalVerificationUnavailable,
)
from homeassistant.core import CoreState


async def test_pending_ram_candidate_is_not_disk_evidence(hass):
    store = _journal_store(hass, "entry", "agent")
    store._data = {"data": {"candidate": True}}
    assert await store.async_load() is None
    assert not Path(store.path).exists()


async def test_journal_readback_ignores_replaced_disk_generation(hass):
    store = _journal_store(hass, "entry", "agent")
    await store.async_save({"generation": 1})
    assert await store.async_load() == {"generation": 1}
    other = _journal_store(hass, "entry", "agent")
    await other.async_save({"generation": 2})
    assert await store.async_load() == {"generation": 2}


async def test_shutdown_save_finishes_on_disk_before_verification(hass):
    hass.state = CoreState.stopping
    store = _journal_store(hass, "entry", "agent")
    assert await _async_write_journal_verified(store, {"candidate": True})
    assert await _journal_store(hass, "entry", "agent").async_load() == {
        "candidate": True
    }
    assert store._data is None


async def test_shutdown_write_failure_cannot_be_verified_as_success(hass, monkeypatch):
    hass.state = CoreState.stopping
    store = _journal_store(hass, "entry", "agent")

    def fail(_data):
        raise OSError("write failed")

    monkeypatch.setattr(store, "_write_data", fail)
    with pytest.raises(_JournalVerificationUnavailable):
        await _async_write_journal_verified(store, {"candidate": True})
    assert await store.async_load() is None
