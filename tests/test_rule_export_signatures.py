"""Public export, quarantine and nested-entry regressions from the follow-up audit."""

from copy import deepcopy
import json

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    transfer,
)
from tests.test_backup import _document
from tests.test_request_rules import local_rule, RequestRules, MemoryStore


@pytest.mark.parametrize("kind", ["setup", "custom", "legacy"])
async def test_no_credential_survives_any_redacted_rule_export(hass, kind):
    secret = "synthetic-audit-token-do-not-use"
    store = MemoryStore()
    manager = RequestRules(store)
    await manager.async_initialize()
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "rest_command.report_measurement", "data": {"api_key": secret}}
    ]
    await manager.async_create(rule)
    saved = await manager.async_backup_data()
    before = deepcopy(saved)
    assert secret in saved["rules"][0]["action"]["canonical_signature"]
    if kind == "legacy":
        document = _document()
        document["request_rules"] = saved
        exported = backup.finalize_backup_snapshot(document)
    else:
        document = {
            "format": transfer.TRANSFER_FORMAT,
            "version": 1,
            "mode": "setup",
            "created_at": "2026-10-19T12:00:00+00:00",
            "integration_version": "1",
            "agent": {
                "title": "Export",
                "source_entry_id": "entry",
                "source_subentry_id": "agent",
            },
            "sections": {"configuration": {}, "request_rules": saved},
        }
        exported = (
            transfer.finalize_setup_export(document)
            if kind == "setup"
            else {"json": json.dumps(transfer.redact_transfer_document(document))}
        )
    assert secret not in exported["json"]
    assert await manager.async_backup_data() == before
    # Derived signatures are rebuilt from restored actions, never trusted from export.
    decoded = json.loads(exported["json"])
    exported_rules = (
        decoded["request_rules"]
        if kind == "legacy"
        else decoded["sections"]["request_rules"]
    )
    action = exported_rules["rules"][0]["action"]
    assert "canonical_signature" not in action
    assert action["actions"][0]["data"]["api_key"] == {
        "__extended_openai_redacted_secret__": True
    }
    assert (
        "canonical_signature"
        in RequestRules.validate_backup_data(
            backup.restore_redacted_secrets(exported_rules)
        )["rules"][0]["action"]
    )


async def test_forged_signature_cannot_authorize_secret_recovery(hass, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    manager = RequestRules(MemoryStore())
    await manager.async_initialize()
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": "rest_command.send",
            "data": {"destination": "trusted", "api_key": "private-value"},
        }
    ]
    await manager.async_create(rule)
    document = _document()
    document["request_rules"] = await manager.async_backup_data()
    current = backup.inspect_backup(document, "agent-new")
    exported = backup.finalize_backup_snapshot(document)["document"]
    action = exported["request_rules"]["rules"][0]["action"]
    action["actions"][0]["data"]["destination"] = "untrusted"
    # Supplying the original derived string does not authorize changed public context.
    action["canonical_signature"] = current.request_rules["rules"][0]["action"][
        "canonical_signature"
    ]
    imported = transfer.inspect_transfer(
        exported, "agent-new", sections=[transfer.SECTION_REQUEST_RULES]
    )
    monkeypatch.setattr(
        transfer, "_async_validate_request_rule_function_dependencies", AsyncMock()
    )
    with pytest.raises(
        backup.BackupError, match="Cannot safely restore unavailable secrets"
    ):
        await transfer.async_materialize_restore(
            hass,
            SimpleNamespace(data={}),
            SimpleNamespace(subentry_id="agent-new"),
            imported,
            sections=[transfer.SECTION_REQUEST_RULES],
            current_snapshot=current,
        )
