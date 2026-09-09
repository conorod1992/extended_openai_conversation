"""Adversarial credential restoration across portable and legacy backups."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, transfer
from custom_components.extended_openai_conversation_responses.secret_redaction import (
    REDACTED_SECRET_SENTINEL as MARKER,
    redact_secrets,
    restore_redacted_secrets,
)
from tests.test_backup import _document
from tests.test_selective_restore_concurrency import RestoreHarness
from tests.test_transfer import _entry_and_subentry


@pytest.mark.parametrize(
    "local",
    [[], [{"id": "other", "token": "wrong"}], [{"token": "wrong"}]],
)
def test_identified_item_never_uses_position(local):
    restored, kept, missing = transfer._restore_section_secrets(
        [{"id": "new", "token": MARKER}], local
    )
    assert restored == [{"id": "new"}]
    assert kept == ()
    assert missing == ("[0].token",)


@pytest.mark.parametrize("ids", [["b", "a"], ["b"], ["c", "a", "b"]])
def test_reorder_delete_and_add_do_not_transfer_credentials(ids):
    local = [{"id": item, "token": f"secret-{item}"} for item in ("a", "b")]
    imported = [{"id": item, "token": MARKER} for item in ids]
    restored, _, missing = transfer._restore_section_secrets(imported, local)
    assert restored == [
        {"id": item, **({"token": f"secret-{item}"} if item != "c" else {})}
        for item in ids
    ]
    assert bool(missing) == ("c" in ids)


@pytest.mark.parametrize("identity", [None, "", 12, "duplicate"])
def test_invalid_or_ambiguous_identity_fails_closed(identity):
    _, kept, missing = transfer._restore_section_secrets(
        [{"id": identity, "token": MARKER}],
        [{"id": identity, "token": "one"}, {"id": identity, "token": "two"}],
    )
    assert not kept
    assert missing == ("[0].token",)


def test_idless_item_cannot_borrow_identified_item_secret():
    restored, _, missing = transfer._restore_section_secrets(
        [{"token": MARKER}], [{"id": "a", "token": "wrong"}]
    )
    assert restored == [{}]
    assert missing
    # Preserve compatibility for genuinely positional, anonymous containers.
    restored, _, missing = transfer._restore_section_secrets(
        [{"token": MARKER}], [{"token": "local"}]
    )
    assert restored == [{"token": "local"}]
    assert not missing


@pytest.mark.parametrize(
    "value",
    [
        "Bearer sk-abcdefghijklmnop",
        "https://example.test/?key=sk-abcdefghijklmnop&format=json",
        "first sk-abcdefghijklmnop\nsecond sk-qrstuvwxyzabcdef end",
    ],
)
def test_embedded_secret_preserves_container_and_recovers_entire_leaf(value):
    original = {"items": [{"id": "a", "text": value, "ordinary": "keep"}]}
    exported = json.loads(json.dumps(redact_secrets(original)))
    assert exported == {"items": [{"id": "a", "text": MARKER, "ordinary": "keep"}]}
    restored, kept, missing = transfer._restore_section_secrets(exported, original)
    assert restored == original
    assert kept == ("items[0].text",)
    assert not missing
    assert redact_secrets(exported) == exported


@pytest.mark.parametrize("marker", [MARKER, "[redacted]", "Bearer [redacted] end"])
def test_old_markers_restore_whole_local_leaf_or_are_removed(marker):
    assert restore_redacted_secrets({"text": marker, "safe": "keep"}) == {
        "safe": "keep"
    }
    restored, kept, missing = transfer._restore_section_secrets(
        {"text": marker}, {"text": "Bearer local-secret end"}
    )
    assert restored == {"text": "Bearer local-secret end"}
    assert kept == ("text",)
    assert not missing
    for local in ({}, {"text": marker}, {"text": {"nested": marker}}):
        restored, kept, missing = transfer._restore_section_secrets(
            {"text": marker}, local
        )
        assert restored == {}
        assert not kept
        assert missing == ("text",)


@pytest.mark.parametrize("kind", ["portable", "full", "legacy"])
@pytest.mark.parametrize("old", [False, True])
async def test_export_inspect_materialize_round_trip(monkeypatch, kind, old):
    document = _document()
    config = document["agent"]["config"]
    config["prompt"] = "Use sk-abcdefghijklmnop only for this task."
    config["functions"][0]["spec"]["description"] = (
        "Call with sk-qrstuvwxyzabcdef safely"
    )
    second = deepcopy(config["functions"][0])
    second["spec"]["name"] = "second"
    second["spec"]["description"] = "Second sk-0123456789abcdef tool"
    config["functions"].append(second)
    current = backup.inspect_backup(document, "agent-1")
    exported_config = redact_secrets(config)
    exported_config["functions"].reverse()
    if old:
        exported_config["prompt"] = "Use [redacted] only for this task."
    if kind == "full":
        document["agent"]["config"] = exported_config
    elif kind == "legacy":
        document = {
            "schema": transfer.LEGACY_AGENT_SCHEMA,
            "version": transfer.AGENT_CONFIG_EXPORT_VERSION,
            "title": "Imported",
            "config": exported_config,
        }
    else:
        entry, subentry = _entry_and_subentry()
        document = transfer._new_transfer_document(entry, subentry, "custom")
        document["sections"] = {transfer.SECTION_CONFIGURATION: exported_config}
    imported = transfer.inspect_transfer(json.loads(json.dumps(document)), "agent-1")
    monkeypatch.setattr(transfer, "_current_snapshot", AsyncMock(return_value=current))
    entry, subentry = _entry_and_subentry()
    target, preview = await transfer.async_materialize_restore(
        None, entry, subentry, imported, sections=[transfer.SECTION_CONFIGURATION]
    )
    assert target.config["prompt"] == config["prompt"]
    assert yaml.safe_load(target.config["functions"]) == list(
        reversed(config["functions"])
    )
    assert preview["preserved_sensitive_field_count"] == 3
    assert preview["missing_sensitive_field_count"] == 0
    assert target.memories == current.memories


async def test_missing_secret_aborts_before_journal_or_writes_and_unselected_is_safe(
    monkeypatch,
):
    tx = RestoreHarness(monkeypatch)
    before = deepcopy(tx.state)
    tx.imported.available_sections = frozenset(
        {transfer.SECTION_CONFIGURATION, transfer.SECTION_KNOWLEDGE}
    )
    tx.imported.raw_configuration = {**deepcopy(tx.subentry.data), "api_key": MARKER}
    with pytest.raises(
        backup.BackupError, match="Cannot safely restore unavailable secrets"
    ):
        await transfer.async_restore_transfer(
            tx.hass, tx.entry, tx.subentry, tx.imported
        )
    assert not tx.writes
    assert tx.state == before
    assert not tx.gate._writer_active
    # Missing credentials in an unselected section must not prevent restore.
    await tx.restore()
    assert tx.state.knowledge == []


@pytest.mark.parametrize("fail_write", [False, True])
async def test_embedded_secret_round_trip_commit_and_rollback(monkeypatch, fail_write):
    tx = RestoreHarness(monkeypatch)
    tx.subentry.data["prompt"] = "Use sk-abcdefghijklmnop safely"
    before = deepcopy(tx.subentry.data)
    tx.imported.available_sections = frozenset(
        {transfer.SECTION_CONFIGURATION, transfer.SECTION_KNOWLEDGE}
    )
    tx.imported.raw_configuration = redact_secrets(deepcopy(before))
    tx.fail_knowledge_writes = int(fail_write)
    if fail_write:
        with pytest.raises(backup.BackupError):
            await transfer.async_restore_transfer(
                tx.hass, tx.entry, tx.subentry, tx.imported
            )
        assert tx.subentry.data == before
    else:
        result = await transfer.async_restore_transfer(
            tx.hass, tx.entry, tx.subentry, tx.imported
        )
        assert result["transfer"]["preserved_sensitive_field_count"] == 1
        assert tx.subentry.data["prompt"] == before["prompt"]
        assert tx.state.knowledge == []
    assert not tx.gate._writer_active
