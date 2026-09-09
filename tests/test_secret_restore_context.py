"""A public import cannot authorize credentials by identity or position alone."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    LITERAL_TEXT_KEY,
    REDACTED_SECRET_SENTINEL as MARKER,
    redact_secrets,
    restore_redacted_secrets,
)
from tests.test_backup import _document
from tests.test_selective_restore_concurrency import RestoreHarness
from tests.test_transfer import _entry_and_subentry, _function_rule, _rules_backup


def _rest_tool():
    tool = deepcopy(_document()["agent"]["config"]["functions"][0])
    tool["spec"]["name"] = "weather"
    tool["enabled"] = True
    tool["function"] = {
        "type": "rest",
        "resource": "https://weather.example.test/api",
        "method": "GET",
        "headers": {"Authorization": "Bearer local-credential"},
    }
    return tool


@pytest.mark.parametrize(
    "change", ["resource", "method", "added", "removed", "container", "unchanged"]
)
async def test_rest_credential_restore_requires_unchanged_context(
    hass, monkeypatch, change
):
    tx = RestoreHarness(monkeypatch)
    tx.subentry.data["functions"] = [_rest_tool()]
    tx.subentry.data["function_groups"] = []
    raw = redact_secrets(agent_config_snapshot(tx.subentry.data))
    function = raw["functions"][0]["function"]
    if change == "resource":
        function["resource"] = "https://attacker.example.test/collect"
    elif change == "method":
        function["method"] = "POST"
    elif change == "added":
        function["headers"]["Host"] = "attacker.example.test"
    elif change == "removed":
        del function["method"]
    elif change == "container":
        # A wildcard must not hide a container holding the credential's context.
        function["headers"] = MARKER
    tx.imported.available_sections = frozenset({transfer.SECTION_CONFIGURATION})
    tx.imported.raw_configuration = raw
    before = deepcopy(tx.subentry.data)
    if change == "unchanged":
        result = await transfer.async_restore_transfer(
            tx.hass, tx.entry, tx.subentry, tx.imported
        )
        assert result["transfer"]["preserved_sensitive_field_count"] == 1
        assert (
            agent_config_snapshot(tx.subentry.data)["functions"] == before["functions"]
        )
        return
    with pytest.raises(
        backup.BackupError, match="Cannot safely restore unavailable secrets"
    ):
        await transfer.async_restore_transfer(
            tx.hass, tx.entry, tx.subentry, tx.imported
        )
    assert tx.subentry.data == before
    assert not tx.writes
    assert not tx.gate._writer_active


def _actions():
    return [
        {
            "action": "rest_command.send",
            "data": {"destination": destination, "token": f"secret-{destination}"},
        }
        for destination in ("a", "b")
    ]


@pytest.mark.parametrize("nested", [False, True])
async def test_real_request_rule_actions_reorder_by_unique_context(monkeypatch, nested):
    document = _document()
    rule = _function_rule()
    rule["action"]["actions"] = [{"parallel": _actions()}] if nested else _actions()
    document["request_rules"] = _rules_backup([rule])
    current = backup.inspect_backup(document, "agent-1")
    entry, subentry = _entry_and_subentry()
    imported_document = transfer._new_transfer_document(entry, subentry, "custom")
    raw = redact_secrets(current.request_rules)
    actions = raw["rules"][0]["action"]["actions"]
    if nested:
        actions = actions[0]["parallel"]
    actions.reverse()
    imported_document["sections"] = {transfer.SECTION_REQUEST_RULES: raw}
    imported = transfer.inspect_transfer(imported_document, "agent-1")
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=current))
    target, preview = await transfer.async_materialize_restore(
        None, entry, subentry, imported
    )
    expected = deepcopy(rule)
    expected["action"]["actions"] = (
        [{"parallel": list(reversed(_actions()))}]
        if nested
        else list(reversed(_actions()))
    )
    assert target.request_rules == RequestRules.validate_backup_data(
        _rules_backup([expected])
    )
    assert preview["preserved_sensitive_field_count"] == 2


@pytest.mark.parametrize(
    "case", ["ambiguous", "changed", "added", "deleted", "duplicate_import"]
)
def test_anonymous_actions_never_guess_or_reuse_a_candidate(case):
    local = _actions()
    imported = redact_secrets(list(reversed(local)))
    if case == "ambiguous":
        local.append(
            {
                **deepcopy(local[0]),
                "data": {"destination": "a", "token": "other-secret"},
            }
        )
    elif case == "changed":
        imported[1]["data"]["destination"] = "attacker"
    elif case == "added":
        imported[1]["data"]["extra"] = "changed-context"
    elif case == "deleted":
        local.pop(0)
    else:
        imported.append(deepcopy(imported[1]))
    restored, kept, missing = transfer._restore_section_secrets(imported, local)
    assert restored[0]["data"]["token"] == "secret-b"
    assert "token" not in restored[1]["data"]
    assert kept == ("[0].data.token",)
    assert missing
    assert "secret-a" not in json.dumps(restored)


@pytest.mark.parametrize(
    "text", ["[redacted]", "The previous value was [redacted] by the service"]
)
@pytest.mark.parametrize("kind", ["portable", "full"])
async def test_new_export_literal_marker_text_round_trips_without_local_secret(
    monkeypatch, text, kind
):
    document = _document()
    current = backup.inspect_backup(document, "agent-1")
    document["agent"]["config"]["prompt"] = text
    entry, subentry = _entry_and_subentry(document["agent"]["config"])
    if kind == "full":
        exported = backup.finalize_backup_snapshot(document)["document"]
    else:
        raw = transfer._new_transfer_document(entry, subentry, "setup")
        raw["sections"] = {
            transfer.SECTION_CONFIGURATION: document["agent"]["config"],
            transfer.SECTION_REQUEST_RULES: _rules_backup(),
        }
        exported = transfer.finalize_setup_export(raw)["document"]
    imported = transfer.inspect_transfer(json.loads(json.dumps(exported)), "agent-1")
    assert imported.config["prompt"] == text
    assert not imported.redacted_sensitive_fields
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=current))
    target, preview = await transfer.async_materialize_restore(
        None, entry, subentry, imported
    )
    assert target.config["prompt"] == text
    assert preview["preserved_sensitive_field_count"] == 0
    assert preview["missing_sensitive_field_count"] == 0


def test_literal_escape_is_idempotent_and_cannot_suppress_real_secret_redaction():
    escaped = {LITERAL_TEXT_KEY: "Literal [redacted] text"}
    assert redact_secrets(escaped) == escaped
    assert restore_redacted_secrets(escaped) == "Literal [redacted] text"
    assert (
        redact_secrets({LITERAL_TEXT_KEY: "[redacted] sk-abcdefghijklmnop"}) == MARKER
    )
    assert restore_redacted_secrets({"prompt": "Legacy [redacted] text"}) == {}
