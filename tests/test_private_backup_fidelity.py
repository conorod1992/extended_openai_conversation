"""Private v6/v3 data survives without source secrets or marker recovery."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import zipfile

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    transfer,
)
from custom_components.extended_openai_conversation_responses.private_backup import (
    PRIVATE_BACKUP_VERSION,
    PRIVATE_TRANSFER_VERSION,
    DecodedPrivateDocument,
    decode_document,
    decode_field,
    encode_document,
    encode_field,
)
from custom_components.extended_openai_conversation_responses.provider_credentials import (
    restore_private_provider_connection,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_backup import _document


def private_document():
    document = _document()
    document["version"] = PRIVATE_BACKUP_VERSION
    document["provider_connection"] = {
        "api_key": "opaque-provider-42",
        "base_url": "https://example.invalid/?sig=opaque",
    }
    config = document["agent"]["config"]
    config["prompt"] = "Literal [redacted] is data"
    config["future_field"] = {"__extended_openai_redacted_secret__": True}
    tools = deepcopy(config["functions"])
    tools[0]["function"]["future"] = {
        "password": "opaque-custom-42\x85literal",
        "binary": b"sk-FABRICATED123456789012345",
        "date": date(2026, 10, 23),
    }
    config["functions"] = yaml.safe_dump(tools)
    return document


@pytest.mark.parametrize("archive", [False, True])
def test_private_artifacts_preserve_source_exactly(hass, tmp_path, archive):
    document = private_document()
    before = deepcopy(document)
    if archive:
        result = backup_transfer._build_archive_file(document, str(tmp_path))
        with zipfile.ZipFile(result["path"]) as zipped:
            artifact = json.loads(zipped.read(backup_transfer.PAYLOAD_NAME))
        Path(result["path"]).unlink()
    else:
        artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    assert decode_document(artifact) == document
    prepared = transfer.inspect_transfer(artifact, "fresh-target")
    assert prepared.private is True
    assert prepared.config == document["agent"]["config"]
    assert prepared.raw_configuration is None
    assert prepared.redacted_sensitive_fields == ()
    assert prepared.provider_connection == document["provider_connection"]
    assert prepared.summary()["provider_connection_available"] is True
    assert "opaque-provider" not in json.dumps(prepared.summary())
    assert document == before
    assert (
        backup.inspect_backup(artifact, "fresh-target").config
        == document["agent"]["config"]
    )


def test_private_custom_transfer_uses_separate_version(hass):
    full = private_document()
    custom = {
        "format": transfer.TRANSFER_FORMAT,
        "version": PRIVATE_TRANSFER_VERSION,
        "mode": "custom",
        "agent": {
            key: value for key, value in full["agent"].items() if key != "config"
        },
        "created_at": full["created_at"],
        "integration_version": full["integration_version"],
        "sections": {"configuration": full["agent"]["config"]},
        "provider_connection": full["provider_connection"],
    }
    prepared = transfer.inspect_transfer(encode_document(custom), "fresh-target")
    assert prepared.config == full["agent"]["config"]
    assert prepared.raw_configuration is None


@pytest.mark.parametrize(
    "value",
    [
        "literal [redacted]",
        {"__extended_openai_redacted_secret__": True},
        {42: {b"opaque"}, "date": date(2026, 10, 23)},
    ],
)
def test_private_codec_reserved_and_typed_data(value):
    assert decode_field(encode_field(value)) == value


def test_legacy_full_backup_semantics_remain_redacted():
    document = _document()
    document["agent"]["config"]["password"] = "fabricated-old-password"
    artifact = backup.finalize_backup_snapshot(document)
    assert "fabricated-old-password" not in artifact["json"]
    assert artifact["document"]["version"] == backup.BACKUP_VERSION


def test_private_memory_snapshot_and_wire_document_have_distinct_provenance(hass):
    raw = DecodedPrivateDocument(private_document())
    assert backup.inspect_backup(raw, "fresh-target").config == raw["agent"]["config"]
    assert transfer.inspect_transfer(raw, "fresh-target").private is True
    encoded = encode_document(raw)
    assert type(encoded) is dict
    assert decode_document(encoded) == raw
    assert (
        backup.inspect_backup(encoded, "fresh-target").config == raw["agent"]["config"]
    )
    with pytest.raises(backup.BackupError, match="encoding is invalid"):
        backup.inspect_backup(json.loads(json.dumps(raw)), "fresh-target")


def test_connection_restore_requires_scope_confirmation_and_unchanged_target():
    hass = MagicMock()
    entry = SimpleNamespace(entry_id="entry", data={"api_key": "current"})
    incoming = {"api_key": "fabricated-exact-provider-value"}
    with pytest.raises(HomeAssistantError, match="Confirm"):
        restore_private_provider_connection(
            hass, entry, incoming, entry.data, confirmed=False
        )
    with pytest.raises(HomeAssistantError, match="changed"):
        restore_private_provider_connection(hass, entry, incoming, {}, confirmed=True)
    hass.config_entries.async_update_entry.assert_not_called()
    restore_private_provider_connection(
        hass, entry, incoming, entry.data, confirmed=True
    )
    hass.config_entries.async_update_entry.assert_called_once_with(entry, data=incoming)


async def test_private_restore_commits_exact_configuration_without_local_secret(
    monkeypatch, hass
):
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    document = private_document()
    incoming = deepcopy(document["agent"]["config"])
    tx.subentry.data = {"functions": "[]", "function_groups": []}
    artifact = backup.finalize_backup_snapshot(document)["document"]
    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        artifact,
        sections=[transfer.SECTION_CONFIGURATION],
    )
    assert tx.subentry.data == incoming
    assert tx.journal.data is None
    assert [item["phase"] for item in tx.journal.saved] == ["applying", "committed"]
    # Recovery after the durable commit must retain the same source and
    # credentials, rather than reinterpret them through a legacy projection.
    committed = json.loads(json.dumps(tx.journal.saved[-1]))
    recovered = backup.inspect_backup(
        committed["target"], "fresh-target", private_journal=True
    )
    assert recovered.config == incoming


async def test_private_provider_restore_precedes_reload_and_preserves_exact_data(
    monkeypatch, hass
):
    from custom_components.extended_openai_conversation_responses import (
        restore_recovery,
    )
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    tx.entry.data = {"api_key": "old-provider"}
    expected = deepcopy(tx.entry.data)
    document = private_document()
    events = []

    def update(entry, *, data):
        assert tx.journal.data is None  # Agent commit is durable first.
        entry.data = deepcopy(data)
        events.append("provider")

    async def reload(*_):
        if tx.gate.owns_exclusive():
            return  # The production hook defers until the outer lease exits.
        assert tx.entry.data == document["provider_connection"]
        events.append("reload")

    tx.hass.config_entries.async_update_entry = update
    tx.hass.config_entries.async_schedule_reload = MagicMock()
    monkeypatch.setattr(restore_recovery, "async_finish_restore_reload", reload)
    result = await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        backup.finalize_backup_snapshot(document)["document"],
        sections=[transfer.SECTION_CONFIGURATION],
        provider_connection=document["provider_connection"],
        expected_provider_data=expected,
    )
    assert result["provider_connection_restored"] is True
    assert events == ["provider", "reload"]
    assert tx.subentry.data == document["agent"]["config"]


async def test_changed_provider_after_preview_cannot_commit(monkeypatch, hass):
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    tx.entry.data = {"api_key": "newer-provider"}
    with pytest.raises(backup.BackupError, match="changed after preview"):
        await transfer.async_restore_transfer(
            tx.hass,
            tx.entry,
            tx.subentry,
            tx.imported,
            provider_connection={"api_key": "incoming"},
            expected_provider_data={"api_key": "older-provider"},
        )
    assert tx.journal.saved == tx.writes == []


@pytest.mark.parametrize("preview_choice", [False, True])
async def test_provider_restore_choice_is_bound_to_preview(
    monkeypatch, hass, tmp_path, preview_choice
):
    from tests.test_remaining_backup_contracts import _import

    session = _import(hass, tmp_path)
    session.preview_restore_provider = preview_choice
    session.preview_provider_data = {"api_key": "current"}
    prepared = transfer.inspect_transfer(
        backup.finalize_backup_snapshot(private_document())["document"], "agent"
    )
    monkeypatch.setattr(
        backup_transfer,
        "_async_load_prepared_restore",
        AsyncMock(return_value=prepared),
    )
    writes = []

    async def restore(*_, precondition, **kwargs):
        await precondition()
        writes.append(True)

    monkeypatch.setattr(transfer, "async_restore_transfer", restore)
    with pytest.raises(backup.BackupError, match="choice changed"):
        await backup_transfer._restore_import(
            hass,
            SimpleNamespace(entry_id="entry"),
            SimpleNamespace(subentry_id="agent"),
            {
                "session_id": session.session_id,
                "preview_token": "preview",
                "restore_provider_connection": not preview_choice,
            },
        )
    assert writes == []
