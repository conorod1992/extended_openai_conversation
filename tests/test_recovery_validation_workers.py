"""Restart recovery uses the same off-loop bulk validation as normal restore."""

import asyncio
from copy import deepcopy
import threading

import pytest

from custom_components.extended_openai_conversation_responses import backup
from homeassistant.util import dt as dt_util
from tests import test_restore_recovery as harness
from tests.test_usage_detail_lifecycle import _request, _run


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize("count", [0, 2000])
async def test_public_recovery_validates_bulk_off_loop(hass, monkeypatch, phase, count):
    target, rollback = harness._states()
    timestamp = dt_util.utcnow().isoformat()
    for prepared in (target, rollback):
        prepared.usage_requests = [_request(str(i), timestamp) for i in range(count)]
        prepared.usage_runs = [_run(str(i), timestamp) for i in range(count)]
    journal = harness.restore_recovery._new_journal(
        "entry-1", "agent-new", target, rollback
    )
    journal["phase"] = phase

    class WorkerJournal(harness.MemoryJournalStore):
        async def async_load(self):
            return await asyncio.to_thread(deepcopy, self.data)

    monkeypatch.setattr(harness, "MemoryJournalStore", WorkerJournal)
    main = threading.get_ident()
    bulk_threads, config_threads, counts = [], [], []
    validate = backup._validate_data_categories

    def bulk(*args, **kwargs):
        bulk_threads.append(threading.get_ident())
        result = validate(*args, **kwargs)
        counts.append((len(result["usage"][2]), len(result["usage"][3])))
        return result

    config = backup.private_configuration_snapshot

    def configuration(*args, **kwargs):
        config_threads.append(threading.get_ident())
        return config(*args, **kwargs)

    monkeypatch.setattr(backup, "_validate_data_categories", bulk)
    monkeypatch.setattr(backup, "private_configuration_snapshot", configuration)
    (
        _hass,
        _entry,
        subentry,
        store,
        selected,
        recovered,
    ) = await harness._recover_with_journal(monkeypatch, journal)
    expected = "Restored" if phase == "committed" else "Before restore"
    assert recovered and selected == [expected] and subentry.title == expected
    assert store.data is None and store.removed == 1
    assert counts == [(count, count), (count, count)]
    assert config_threads and set(config_threads) == {main}
    assert bulk_threads and main not in bulk_threads
