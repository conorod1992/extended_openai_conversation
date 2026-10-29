"""Restore evidence survives the production WebSocket and recovery boundaries."""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup_transfer,
    management_ui,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.mutation_outcomes import (
    mutation_failure,
)
from tests.test_backup import _document
from tests.test_restore_phase_fault_injection import _prepare_live_restore


@pytest.mark.parametrize("endpoint", ["management", "transfer"])
@pytest.mark.parametrize(
    "fault,outcome,pending",
    [
        ("write_ahead", "rejected", False),
        ("rollback", "rejected", False),
        ("rollback_failed", "uncertain", True),
        ("commit_verification", "uncertain", True),
        ("completion", "committed", True),
    ],
)
async def test_restore_failure_outcome_through_websocket_and_recovery(
    monkeypatch, hass, endpoint, fault, outcome, pending
):
    runtime, entry, subentry, journal, _, _ = await _prepare_live_restore(monkeypatch)
    apply = AsyncMock()
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    if fault == "write_ahead":
        journal.fail_next_save = True
    elif fault in {"rollback", "rollback_failed"}:
        apply.side_effect = [
            OSError("apply failed"),
            OSError("rollback failed") if fault == "rollback_failed" else None,
        ]
    elif fault == "completion":
        restore_recovery._async_persist_config_entries.side_effect = [
            OSError("completion failed"),
            None,
        ]
    else:
        save = journal.async_save

        async def lost_ack(value):
            await save(value)
            if value["phase"] == "committed":
                raise OSError("commit acknowledgement lost")

        monkeypatch.setattr(journal, "async_save", lost_ack)

    async def command(*_):
        return await restore_recovery.async_restore_backup_recoverably(
            runtime, entry, subentry, _document()
        )

    module = management_ui if endpoint == "management" else backup_transfer
    command_name = (
        "async_management_command"
        if endpoint == "management"
        else "async_backup_transfer_command"
    )
    handler = (
        module.websocket_management
        if endpoint == "management"
        else module.websocket_backup_transfer
    )
    monkeypatch.setattr(module, command_name, command)
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_error=Mock(),
        send_result=Mock(),
    )
    await unwrap(handler)(runtime, connection, {"id": 1})
    code = f"eoai_mutation_{outcome}" + ("_recovery_pending" if pending else "")
    assert connection.send_error.call_args.args[:2] == (1, code)
    connection.send_result.assert_not_called()
    if pending:
        apply.side_effect = None
        assert await restore_recovery.async_recover_pending_restore(
            runtime, entry, subentry
        )
        assert journal.data is None
        assert not await restore_recovery.async_recover_pending_restore(
            runtime, entry, subentry
        )
    apply.side_effect = None
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    # Subsequent commands are still admitted after authoritative recovery.
    if fault == "commit_verification":
        monkeypatch.setattr(journal, "async_save", save)
    assert (await command())["status"] == "restored"


async def test_failed_save_preserves_exception_type_and_marks_uncertainty():
    from custom_components.extended_openai_conversation_responses.persistence_hardening import (
        _async_settle_transactional_save,
    )

    error = RuntimeError("lost acknowledgement")
    with pytest.raises(RuntimeError) as caught:
        await _async_settle_transactional_save(
            AsyncMock(side_effect=error)(), Mock(), Mock(), AsyncMock(), Mock()
        )
    assert caught.value is error
    assert mutation_failure(error).outcome == "uncertain"
