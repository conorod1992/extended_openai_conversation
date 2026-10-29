"""Critical writes cannot acknowledge swallowed serialization or writer failures."""

import errno
import json
from pathlib import Path
from unittest.mock import AsyncMock

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    HomeAssistantKnowledgeStorage,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.strict_store import (
    PropagatingWriteStore,
)
from homeassistant.helpers.storage import Store
from tests.test_backup import _document
from tests.test_restore_phase_fault_injection import _prepare_live_restore


class _PrivateValue:
    def __repr__(self):
        return "PRIVATE-SERIALIZATION-SENTINEL"


@pytest.mark.parametrize("serialize_in_event_loop", [False, True])
@pytest.mark.parametrize("bound", [False, True])
async def test_serialization_failure_preserves_disk_and_allows_next_save(
    hass, caplog, serialize_in_event_loop, bound
):
    store = PropagatingWriteStore(
        hass,
        1,
        "critical-serialization",
        private=True,
        atomic_writes=True,
        serialize_in_event_loop=serialize_in_event_loop,
    )
    if bound:
        store.bind_agent("entry", "agent")
    await store.async_save({"value": "before"})
    path = Path(store.path)
    before = path.read_bytes()
    with pytest.raises(OSError, match="Private storage serialization failed") as raised:
        await store.async_save({"value": _PrivateValue()})
    assert "PRIVATE-SERIALIZATION-SENTINEL" not in str(raised.value)
    assert "PRIVATE-SERIALIZATION-SENTINEL" not in caplog.text
    assert raised.value.__cause__ is None
    assert path.read_bytes() == before
    assert await store.async_load() == {"value": "before"}
    await store.async_save({"value": "after"})
    fresh = PropagatingWriteStore(hass, 1, store.key)
    assert await fresh.async_load() == {"value": "after"}


async def test_manager_reconciles_serialization_failure_before_next_mutation(
    hass, monkeypatch
):
    storage = HomeAssistantKnowledgeStorage(hass, "entry", "agent")
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    await library.async_create("Original", "", "established searchable fact")
    before = await library.async_backup_data()
    path = Path(storage._store.path)
    before_bytes = path.read_bytes()
    native_write = storage._store._write_data

    def fail_serialization(envelope):
        native_write({**envelope, "private": _PrivateValue()})

    with monkeypatch.context() as fault:
        fault.setattr(storage._store, "_write_data", fail_serialization)
        with pytest.raises(OSError, match="serialization failed"):
            await library.async_create("Candidate", "", "uncommitted candidate fact")
    assert await library.async_backup_data() == before
    assert not await library.async_search("uncommitted")
    assert path.read_bytes() == before_bytes
    restarted = KnowledgeLibrary(HomeAssistantKnowledgeStorage(hass, "entry", "agent"))
    await restarted.async_initialize()
    assert await restarted.async_backup_data() == before
    await library.async_create("Healthy", "", "subsequent healthy fact")
    reloaded = KnowledgeLibrary(HomeAssistantKnowledgeStorage(hass, "entry", "agent"))
    await reloaded.async_initialize()
    assert await reloaded.async_backup_data() == await library.async_backup_data()
    assert reloaded.total_source_count == 2


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize(
    "boundary", ["serialization", "before_replace", "after_replace"]
)
async def test_native_journal_failure_keeps_disk_phase_for_recovery(
    hass, monkeypatch, phase, boundary
):
    # Only participants/configuration are doubled. Journal serialization, atomic
    # replacement and strict disk reads use the production Store implementation.
    factory = restore_recovery._journal_store
    runtime, entry, subentry, _, managers, _ = await _prepare_live_restore(monkeypatch)
    monkeypatch.setattr(
        restore_recovery,
        "_journal_store",
        lambda *_: factory(hass, entry.entry_id, subentry.subentry_id),
    )
    apply = AsyncMock()
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    path = Path(factory(hass, entry.entry_id, subentry.subentry_id).path)
    native_replace = atomicwrites._replace_atomic

    def failed_replace(source, destination):
        targeted = (
            Path(destination) == path
            and json.loads(Path(source).read_text(encoding="utf-8"))["data"]["phase"]
            == phase
        )
        if targeted and boundary == "before_replace":
            raise OSError(errno.EIO, "injected replace failure")
        native_replace(source, destination)
        if targeted and boundary == "after_replace":
            raise OSError(errno.EIO, "injected post-replace failure")

    # Corrupt only the outgoing envelope, leaving transaction RAM state intact.
    native_write = Store._write_data

    def failed_serialization(store, envelope):
        if Path(store.path) == path and envelope["data"]["phase"] == phase:
            envelope = {**envelope, "private": _PrivateValue()}
        native_write(store, envelope)

    with monkeypatch.context() as fault:
        if boundary == "serialization":
            fault.setattr(Store, "_write_data", failed_serialization)
        else:
            fault.setattr(atomicwrites, "_replace_atomic", failed_replace)
        with pytest.raises(backup.BackupError, match=r"could not be.*verified"):
            await restore_recovery.async_restore_backup_recoverably(
                runtime, entry, subentry, _document()
            )

    assert apply.await_count == (0 if phase == "applying" else 1)
    durable = await factory(hass, entry.entry_id, subentry.subentry_id).async_load()
    gate = get_agent_maintenance_gate(runtime, entry.entry_id, subentry.subentry_id)
    assert gate.recovery_required is (durable is not None)
    if durable is None:
        assert not path.exists()
    else:
        expected_phase = phase if boundary == "after_replace" else "applying"
        assert durable["phase"] == expected_phase
        _, target, rollback = await restore_recovery._async_load_journal(
            durable, entry.entry_id, subentry.subentry_id
        )
        selected = target if expected_phase == "committed" else rollback
        apply.reset_mock()
        assert await restore_recovery.async_recover_pending_restore(
            runtime, entry, subentry
        )
        apply.assert_awaited_once_with(managers, selected)
        assert not path.exists()
        assert not gate.recovery_required
        assert not await restore_recovery.async_recover_pending_restore(
            runtime, entry, subentry
        )
    # A healthy subsequent restore must still succeed after failure/recovery.
    result = await restore_recovery.async_restore_backup_recoverably(
        runtime, entry, subentry, _document()
    )
    assert result["status"] == "restored"
    assert not path.exists()
