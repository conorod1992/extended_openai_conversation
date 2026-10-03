"""Pending/corrupt restore evidence must quarantine only its owning assistant."""

from __future__ import annotations

import errno
import json
from pathlib import Path

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_provider_wire_e2e import _agent, _chat_sse_text, _install_wire
from tests_stress.conftest import record
from tests_stress.fresh_restore import assert_fresh_contents
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def _populated(hass):
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    entry, subentry = agent.entry, agent.subentry
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    await memory.async_add("owner", "TARGET MEMORY", "test", "explicit")
    await knowledge.async_create("TARGET KNOWLEDGE", "", "target contents")
    target = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    target["agent"]["config"]["prompt"] = "TARGET CONFIGURATION"
    await memory.async_replace_backup([])
    await knowledge.async_replace_backup([])
    await memory.async_add("owner", "PREVIOUS MEMORY", "test", "explicit")
    await knowledge.async_create("PREVIOUS KNOWLEDGE", "", "previous contents")
    previous = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    return agent, memory, knowledge, target, previous


async def _assert_rejected(hass, monkeypatch, agent, memory, knowledge):
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("UNEXPECTED")])
    admin = await hass.auth.async_create_user(
        "Recovery admin", group_ids=["system-admin"]
    )
    operations = [
        lambda: memory.async_add("owner", "MUST NOT ACKNOWLEDGE", "test", "explicit"),
        lambda: knowledge.async_create("MUST NOT ACKNOWLEDGE", "", "new contents"),
        lambda: async_management_command(
            hass,
            admin.id,
            True,
            {
                "entry_id": agent.entry.entry_id,
                "subentry_id": agent.subentry.subentry_id,
                "section": "configuration",
                "action": "save",
                "config": {"prompt": "UNSAFE CONFIGURATION"},
            },
        ),
        lambda: backup.async_create_backup(hass, agent.entry, agent.subentry),
    ]
    for operation in operations:
        with pytest.raises(
            HomeAssistantError, match=r"recovery.*required|retry after recovery"
        ):
            await operation()
    from homeassistant.components import conversation

    if conversation.async_get_agent(hass, agent.entry.entry_id) is None:
        with pytest.raises(ValueError, match="not found"):
            await _say(hass, agent, "try to operate while recovery is pending")
    else:
        result = await _say(hass, agent, "try to operate while recovery is pending")
        assert result.response.error_code is not None
        assert "recovery" in str(result.response.as_dict()).lower()
    assert not wire.requests


@pytest.mark.parametrize("phase", ["rollback", "completion"])
@pytest.mark.usefixtures("real_store_io")
async def test_pending_restore_rejects_writes_until_durable_recovery(
    hass, monkeypatch, stress_trace, phase
):
    agent, memory, knowledge, target, previous = await _populated(hass)
    entry, subentry = agent.entry, agent.subentry
    replace = atomicwrites.replace_atomic
    destination = (
        knowledge._storage._store.path
        if phase == "rollback"
        else hass.config_entries._store.path
    )
    faults = []

    def fail_replace(source, path):
        if Path(path) == Path(destination):
            faults.append(str(path))
            raise OSError(errno.EROFS, "restore destination is read-only")
        return replace(source, path)

    with monkeypatch.context() as fault:
        if phase == "rollback":
            fault.setattr(atomicwrites, "replace_atomic", fail_replace)
        else:

            def fail_configuration_write(data):
                faults.append("native Core configuration write")
                raise OSError(errno.EROFS, "configuration destination is read-only")

            fault.setattr(
                hass.config_entries._store, "_write_data", fail_configuration_write
            )
        with pytest.raises(backup.BackupError, match="pending"):
            await backup.async_restore_backup(hass, entry, subentry, target)
        assert len(faults) >= (2 if phase == "rollback" else 1)
        store = restore_recovery._journal_store(
            hass, entry.entry_id, subentry.subentry_id
        )
        journal_bytes = Path(store.path).read_bytes()
        journal = json.loads(journal_bytes)["data"]
        assert journal["phase"] == ("applying" if phase == "rollback" else "committed")
        assert get_agent_maintenance_gate(
            hass, entry.entry_id, subentry.subentry_id
        ).recovery_required
        await _assert_rejected(hass, monkeypatch, agent, memory, knowledge)
        assert Path(store.path).read_bytes() == journal_bytes

    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    expected = previous if phase == "rollback" else target
    await assert_fresh_contents(hass, entry, subentry, expected)
    assert subentry.data["prompt"] == expected["agent"]["config"]["prompt"]
    assert not Path(store.path).exists()
    assert not get_agent_maintenance_gate(
        hass, entry.entry_id, subentry.subentry_id
    ).recovery_required
    await memory.async_add("owner", "ACKNOWLEDGED AFTER RECOVERY", "test", "explicit")
    await knowledge.async_create(
        "ACKNOWLEDGED AFTER RECOVERY", "", "durable new content"
    )
    acknowledged = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await assert_fresh_contents(hass, entry, subentry, acknowledged)
    assert not await restore_recovery.async_recover_pending_restore(
        hass, entry, subentry
    )
    await assert_fresh_contents(hass, entry, subentry, acknowledged)
    record(
        stress_trace,
        "summary",
        restore_pending_cases=1,
        rejected_recovery_operations=5,
        native_restore_faults=len(faults),
        fresh_reads=3,
        reloads=1,
    )


@pytest.mark.parametrize(
    "fault", ["structure", "truncated", "corrupt", "unreadable", "destination"]
)
@pytest.mark.usefixtures("real_store_io")
async def test_bad_journal_is_preserved_and_other_assistant_remains_usable(
    hass, monkeypatch, stress_trace, fault
):
    agent, memory, knowledge, target, previous = await _populated(hass)
    entry, subentry = agent.entry, agent.subentry
    sibling = await _agent(hass, API_MODE_CHAT_COMPLETIONS)

    store = restore_recovery._journal_store(hass, entry.entry_id, subentry.subentry_id)
    prepared = backup.inspect_backup(target, subentry.subentry_id)
    rollback = backup.inspect_backup(previous, subentry.subentry_id)
    valid = restore_recovery._new_journal(
        entry.entry_id, subentry.subentry_id, prepared, rollback
    )
    await store.async_save(valid)
    path = Path(store.path)
    valid_bytes = path.read_bytes()
    if fault == "structure":
        envelope = json.loads(valid_bytes)
        envelope["data"] = {"phase": "applying"}
        path.write_text(json.dumps(envelope))
    elif fault == "truncated":
        path.write_bytes(valid_bytes[: len(valid_bytes) // 2])
    elif fault == "corrupt":
        path.write_bytes(b"{not JSON or a recoverable generation}")
    evidence = path.read_bytes()
    read_text, replace = Path.read_text, atomicwrites.replace_atomic
    observed_faults = []

    def fail_read(current, *args, **kwargs):
        if current == path:
            observed_faults.append("read")
            raise PermissionError(errno.EACCES, "journal cannot be read")
        return read_text(current, *args, **kwargs)

    def fail_destination(source, destination):
        if Path(destination) == Path(memory._storage._store.path):
            observed_faults.append("replace")
            raise OSError(errno.EROFS, "destination cannot recover")
        return replace(source, destination)

    with monkeypatch.context() as problem:
        if fault == "unreadable":
            problem.setattr(Path, "read_text", fail_read)
        if fault == "destination":
            problem.setattr(atomicwrites, "replace_atomic", fail_destination)
        # Startup recovery catches only the affected assistant's failure and keeps
        # its journal, rather than aborting integration setup for every assistant.
        await restore_recovery.async_recover_pending_restores(hass)
        assert get_agent_maintenance_gate(
            hass, entry.entry_id, subentry.subentry_id
        ).recovery_required
        assert not get_agent_maintenance_gate(
            hass, sibling.entry.entry_id, sibling.subentry.subentry_id
        ).recovery_required
        assert path.read_bytes() == evidence
        assert not list(path.parent.glob(path.name + ".corrupt*"))
        if fault in {"unreadable", "destination"}:
            assert observed_faults
        await _assert_rejected(hass, monkeypatch, agent, memory, knowledge)
        sibling_wire = _install_wire(
            monkeypatch, sibling, [_chat_sse_text("SIBLING HEALTHY")] * 2
        )
        assert (
            _speech(await _say(hass, sibling, "unaffected public request"))
            == "SIBLING HEALTHY"
        )
        assert len(sibling_wire.requests) == 1
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        # No affected conversation entity may initialize against the mixed state.
        from homeassistant.components import conversation

        assert conversation.async_get_agent(hass, entry.entry_id) is None

    # Repair the evidence, retaining its original authoritative rollback decision.
    path.write_bytes(valid_bytes)
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert not path.exists()
    await assert_fresh_contents(hass, entry, subentry, previous)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert conversation.async_get_agent(hass, entry.entry_id) is not None
    assert (
        _speech(await _say(hass, sibling, "still healthy after repair"))
        == "SIBLING HEALTHY"
    )
    assert len(sibling_wire.requests) == 2
    record(
        stress_trace,
        "summary",
        restore_journal_fault_cases=1,
        rejected_recovery_operations=5,
        provider_requests=2,
        public_turns=2,
        fresh_reads=1,
        reloads=2,
    )
