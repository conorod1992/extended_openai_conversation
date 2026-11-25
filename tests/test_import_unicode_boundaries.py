"""External Unicode validation and safe admission to HA's native date parser."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock
import zipfile

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    management_ui,
    private_backup,
    record_timestamps,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    parse_configured_function_tools,
)
from tests.test_backup import _document


def test_malformed_timestamp_is_rejected_in_subprocess():
    script = """
import json
from custom_components.extended_openai_conversation_responses import backup, record_timestamps
bad = chr(0xD800)
assert record_timestamps.parse_datetime(bad) is None
try:
    backup._decode_backup_document(json.dumps({'created_at': bad}), backup.MAX_BACKUP_BYTES)
except backup.BackupError:
    pass
else:
    raise AssertionError('malformed timestamp reached backup inspection')
"""
    run = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr.decode(errors="replace")


async def test_already_decoded_transfer_and_private_yaml_validate_keys_and_values():
    with pytest.raises(backup.BackupError, match="malformed Unicode"):
        await transfer.async_inspect_decoded_transfer({chr(0xDFFF): "text"}, "agent")
    wire = {"encoding": "yaml-safe-v1", "content": r'{"\uD83D\uDE00": "value"}'}
    assert private_backup.decode_field(wire) == {"😀": "value"}
    with pytest.raises(ValueError, match="malformed Unicode"):
        private_backup.decode_field({**wire, "content": r'{key: "\uDFFF"}'})


@pytest.mark.parametrize("valid", [True, False])
def test_archive_rejects_malformed_escapes_without_rejecting_valid_pairs(
    tmp_path, valid
):
    text = r'{"created_at":"\ud83d\ude00"}' if valid else r'{"created_at":"\ud800"}'
    payload = text.encode()
    archive_path = tmp_path / "transfer.zip"
    manifest = {
        "format": backup_transfer.ARCHIVE_FORMAT,
        "version": backup_transfer.ARCHIVE_VERSION,
        "payload": backup_transfer.PAYLOAD_NAME,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(backup_transfer.PAYLOAD_NAME, payload)
        archive.writestr(backup_transfer.MANIFEST_NAME, json.dumps(manifest))
    if valid:
        assert backup_transfer._load_archive_document(str(archive_path)) == {
            "created_at": "😀"
        }
    else:
        with pytest.raises(backup.BackupError, match="not valid JSON"):
            backup_transfer._load_archive_document(str(archive_path))


def test_native_timestamp_formats_are_unchanged():
    for value in ("2026-11-21T00:12:34Z", "20261121T001234+0000", "2026-11-21 00:12"):
        assert record_timestamps.parse_datetime(
            value
        ) == record_timestamps.dt_util.parse_datetime(value)


def test_timestamp_guard_never_passes_unpaired_surrogates_to_native_parser(monkeypatch):
    native = Mock(side_effect=AssertionError("malformed timestamp reached HA"))
    monkeypatch.setattr(record_timestamps.dt_util, "parse_datetime", native)
    assert (
        record_timestamps.parse_datetime("2026-11-21T00:12:34Z" + chr(0xD800)) is None
    )
    native.assert_not_called()


async def test_raw_import_text_rejects_non_unicode_characters_before_decoding():
    malformed = '{"config": "' + chr(0xD800) + '"}'
    with pytest.raises(backup.BackupError, match="malformed Unicode"):
        await backup.async_inspect_backup(malformed, "agent")
    with pytest.raises(backup.BackupError, match="malformed Unicode"):
        await transfer.async_inspect_transfer(malformed, "agent")


async def test_decoded_transfer_rejects_circular_fields_before_inspection():
    document = {"sections": {}}
    document["sections"]["configuration"] = document
    with pytest.raises(backup.BackupError, match="circular reference"):
        await transfer.async_inspect_decoded_transfer(document, "agent")


async def test_configuration_import_checks_json_yaml_and_decoded_documents():
    document = {
        "schema": "extended_openai_conversation.agent",
        "version": management_ui.AGENT_CONFIG_EXPORT_VERSION,
        "title": "Imported",
        "config": {"functions": "[]", "prompt": chr(0xD800)},
    }
    yaml_prefix = (
        "schema: extended_openai_conversation.agent\n"
        f"version: {management_ui.AGENT_CONFIG_EXPORT_VERSION}\n"
        "title: Imported\nconfig:\n  functions: '[]'\n  prompt: "
    )
    for raw in (document, json.dumps(document), yaml_prefix + r'"\uD800"'):
        with pytest.raises(AgentConfigError, match="malformed Unicode"):
            management_ui._parse_import_document(raw)
    valid_yaml = management_ui._parse_import_document(yaml_prefix + r'"\uD83D\uDE00"')
    assert valid_yaml["config"]["prompt"] == "😀"


async def test_embedded_function_yaml_is_validated_before_import_quarantine(
    hass, monkeypatch
):
    raw_tools = (
        "- spec:\n    name: echo_template\n    description: Echo\n"
        "    parameters: {type: object, properties: {}}\n"
        '  function:\n    type: template\n    value_template: "ESCAPE"\n'
    )
    config = {
        "functions": raw_tools.replace("ESCAPE", r"\uD800"),
        "function_groups": [],
    }
    setup = {
        "schema": "extended_openai_conversation.agent",
        "version": management_ui.AGENT_CONFIG_EXPORT_VERSION,
        "title": "Imported",
        "config": config,
    }
    document = _document()
    document["agent"]["config"] = config
    with pytest.raises(AgentConfigError, match="malformed Unicode"):
        management_ui._parse_import_document(setup)

    owner_thread = threading.get_ident()
    validate_serialized = backup.validate_serialized_configuration_unicode
    calls = []

    def validate_in_worker(options):
        assert threading.get_ident() != owner_thread
        calls.append(options)
        validate_serialized(options)

    monkeypatch.setattr(
        backup, "validate_serialized_configuration_unicode", validate_in_worker
    )
    with pytest.raises(backup.BackupError, match="malformed Unicode"):
        await backup.async_inspect_backup(document, "agent")
    with pytest.raises(backup.BackupError, match="malformed Unicode"):
        await transfer.async_inspect_decoded_transfer(
            document, "agent", inspect_only=True
        )
    assert len(calls) == 2

    for malformed in (
        r'[{"function": {"type": "template", "value_template": "\uD800"}}]',
        config["functions"],
    ):
        embedded = {"functions": malformed}
        custom = {
            "format": transfer.TRANSFER_FORMAT,
            "version": transfer.TRANSFER_VERSION,
            "mode": "custom",
            "sections": {transfer.SECTION_CONFIGURATION: embedded},
        }
        for imported in ({**setup, "config": embedded}, custom):
            with pytest.raises(backup.BackupError, match="malformed Unicode"):
                await transfer.async_inspect_decoded_transfer(
                    imported, "agent", inspect_only=True
                )

    config["functions"] = raw_tools.replace("ESCAPE", r"\uD83D\uDE00")
    saved = management_ui._parse_import_document(setup)["config"]
    [tool] = parse_configured_function_tools(saved)
    assert tool["function"]["value_template"] == "😀"
