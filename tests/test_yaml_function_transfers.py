"""Typed YAML Function values survive transfer and delayed execution identities."""

from copy import deepcopy
from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    management_ui,
    restore_recovery,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    tool_definition_fingerprint,
)
from tests.helpers import legacy_agent_export
from tests.restore_participants import participants
from tests.test_backup import _document

TOOL_YAML = """spec:
  name: dated_reminder
  description: Render the configured reminder date
  parameters:
    type: object
    properties: {}
function:
  type: script
  variables:
    reminder_date: 2026-10-17
  sequence:
    - stop: Done
"""


@pytest.fixture
async def saved_date_tool(hass, management_agent, management_message, monkeypatch):
    entry, subentry = management_agent
    rules = SimpleNamespace(
        revision=lambda: "unchanged-empty-rules",
        async_backup_data=AsyncMock(return_value={"defaults": {}, "rules": []}),
    )
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "tools",
            "save",
            yaml=TOOL_YAML,
            revision=management_ui._agent_config_revision(
                subentry.data, subentry.title
            ),
        ),
    )
    assert result["functions"][-1]["spec"]["name"] == "dated_reminder"
    assert isinstance(subentry.data["functions"], str)
    parsed = yaml.safe_load(subentry.data["functions"])
    assert parsed[-1]["function"]["variables"]["reminder_date"] == date(2026, 10, 17)
    return entry, subentry


async def test_supported_save_and_runtime_variables_accept_date(hass, saved_date_tool):
    _entry, subentry = saved_date_tool
    tool = configured_function_tools_from_data(subentry.data)[-1]
    variables = tool["function"]["variables"].async_render(hass, {})
    assert variables["reminder_date"] == date(2026, 10, 17)
    assert variables["reminder_date"].isoformat() == "2026-10-17"


@pytest.mark.parametrize("format", ["json", "yaml"])
@pytest.mark.parametrize("schema_bound", [False, True])
async def test_serialized_export_preserves_numeric_schema_and_runtime_variables(
    hass, saved_date_tool, management_message, format, schema_bound
):
    _entry, subentry = saved_date_tool
    tools = yaml.safe_load(subentry.data["functions"])
    tool = tools[-1]
    if schema_bound:
        tool["spec"]["parameters"]["properties"] = {
            "value": {"type": "number", "minimum": 0.000001}
        }
    tool["function"]["variables"] = {"factor": 0.000001, "literal": "1e-06"}
    subentry.data = {**subentry.data, "functions": yaml.safe_dump(tools)}
    exported = await management_ui.async_management_command(
        hass, "admin", True,
        management_message("configuration", "export", reviewed_custom=True),
    )
    text = exported["json"] if format == "json" else yaml.safe_dump(exported["document"])
    if format == "json" and schema_bound:
        assert '"minimum": 1e-06' in text
    parsed = management_ui._parse_import_document(text)
    runtime = configured_function_tools_from_data(parsed["config"])[-1]
    if schema_bound:
        assert runtime["spec"]["parameters"]["properties"]["value"]["minimum"] == 0.000001
    variables = runtime["function"]["variables"].async_render(hass, {})
    assert variables["factor"] == 0.000001
    assert type(variables["factor"]) is float
    assert variables["literal"] == "1e-06"


@pytest.mark.parametrize("schema_bound", [False, True])
async def test_numeric_round_trip_detects_yaml_only_json_parsing(
    hass, saved_date_tool, management_message, monkeypatch, schema_bound
):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        AgentConfigError,
    )

    original = management_ui._parse_import_document
    monkeypatch.setattr(
        management_ui,
        "_parse_import_document",
        lambda text: original(yaml.safe_load(text)),
    )
    with pytest.raises(AgentConfigError if schema_bound else AssertionError):
        await test_serialized_export_preserves_numeric_schema_and_runtime_variables(
            hass, saved_date_tool, management_message, "json", schema_bound
        )


async def test_delayed_tool_fingerprint_preserves_yaml_date_type(saved_date_tool):
    _entry, subentry = saved_date_tool
    tool = configured_function_tools_from_data(subentry.data)[-1]
    fingerprint = tool_definition_fingerprint(tool)
    assert fingerprint == tool_definition_fingerprint(deepcopy(tool))
    quoted = deepcopy(tool)
    quoted["function"]["variables"]["reminder_date"] = "2026-10-17"
    assert fingerprint != tool_definition_fingerprint(quoted)


@pytest.mark.timeout(120)
def test_delayed_fingerprint_is_stable_across_restart_hash_seeds():
    """Identical YAML sets fingerprint identically regardless of iteration order."""
    from random import Random

    # Mixed YAML scalar types stop PyYAML from sorting the set's mapping keys;
    # Python's randomized hash-table iteration then changes the old identity.
    # Vary insertion order in-process instead of importing Home Assistant in
    # subprocesses: this deterministically models distinct restart hash layouts.
    members = ["alpha", "bravo", "charlie", 0, 8, 16, 24, 32, 40, 48, 56, 64]
    rng = Random(419)
    previous_encodings: set[str] = set()
    fingerprints: set[str] = set()
    for _ in range(40):
        shuffled = members.copy()
        rng.shuffle(shuffled)
        mixed = set()
        for item in shuffled:
            mixed.add(item)
        tool = {
            "spec": {"name": "mixed_yaml_set", "parameters": {"type": "object", "properties": {}}},
            "function": {
                "type": "script",
                "variables": {"mixed": mixed},
                "sequence": [{"stop": "Done"}],
            },
        }
        previous_encodings.add(yaml.safe_dump(tool, sort_keys=True, allow_unicode=True))
        fingerprints.add(tool_definition_fingerprint(tool))
    assert len(previous_encodings) > 1  # Exposes the original nondeterminism.
    assert len(fingerprints) == 1  # The replay identity must now remain stable.


def test_typed_fingerprint_without_sets_keeps_existing_identity():
    """Previously queued date-only definitions must retain their fingerprints."""
    import hashlib

    tool = {
        "spec": {"name": "dated_reminder"},
        "function": {"type": "script", "variables": {"date": date(2026, 10, 17)}},
    }
    historical = "yaml:" + yaml.safe_dump(
        {"spec": tool["spec"], "function": tool["function"]},
        sort_keys=True,
        allow_unicode=True,
    )
    assert (
        tool_definition_fingerprint(tool)
        == hashlib.sha256(historical.encode()).hexdigest()
    )


@pytest.mark.parametrize("mode", ["setup", "full", "custom_configuration"])
async def test_exports_accept_yaml_date(hass, saved_date_tool, tmp_path, mode):
    entry, subentry = saved_date_tool
    config = backup.export_configuration_snapshot(subentry.data)
    assert yaml.safe_load(config["functions"])[-1]["function"]["variables"][
        "reminder_date"
    ] == date(2026, 10, 17)
    if mode == "full":
        document = _document()
        document["agent"]["config"] = config
        backup_transfer._build_archive_file(document, str(tmp_path))
    else:
        document = transfer._new_transfer_document(
            entry, subentry, "setup" if mode == "setup" else "custom"
        )
        document["sections"]["configuration"] = config
        if mode == "setup":
            document["sections"]["request_rules"] = {"defaults": {}, "rules": []}
            transfer.finalize_setup_export(document)
        else:
            backup_transfer._build_archive_file(document, str(tmp_path))


async def test_unselected_configuration_preserves_yaml_date_in_restore_rollback(
    hass, saved_date_tool
):
    entry, subentry = saved_date_tool
    base = _document()
    managers = participants(
        memory=SimpleNamespace(
            async_backup_data=AsyncMock(return_value=base["memories"])
        ),
        temporary=SimpleNamespace(
            async_backup_data=AsyncMock(return_value=base["temporary_memories"])
        ),
        knowledge=SimpleNamespace(
            async_backup_data=AsyncMock(return_value=base["knowledge"])
        ),
        archive=SimpleNamespace(
            async_recovery_data=AsyncMock(
                return_value={**base["archive"], "active": {}}
            )
        ),
        usage=SimpleNamespace(async_backup_data=AsyncMock(return_value=base["usage"])),
        guest_mode=SimpleNamespace(
            async_backup_data=AsyncMock(return_value={"schedule": None})
        ),
        request_rules=SimpleNamespace(
            async_backup_data=AsyncMock(return_value={"defaults": {}, "rules": []})
        ),
    )
    rollback = await backup._snapshot_for_restore(managers, subentry)
    # A valid memory-only custom transfer preserves the destination configuration.
    incoming = transfer._new_transfer_document(entry, subentry, "custom")
    incoming["sections"]["persistent_memory"] = {"memories": []}
    selected = frozenset({"persistent_memory"})
    imported = transfer.inspect_transfer(
        backup_transfer._redacted_document(incoming), subentry.subentry_id, sections=selected
    )
    target, _preserved, _missing = transfer._prepared_restore_from_selection(
        rollback, imported, selected
    )
    journal = restore_recovery._new_journal(
        entry.entry_id, subentry.subentry_id, target, rollback
    )
    restored = backup.inspect_backup(
        json.loads(json.dumps(journal))["rollback"],
        subentry.subentry_id,
        max_bytes=backup.MAX_BACKUP_BYTES,
        private_journal=True,
    )
    assert yaml.safe_load(restored.config["functions"])[-1]["function"]["variables"][
        "reminder_date"
    ] == date(2026, 10, 17)


async def test_yaml_date_export_redacts_and_restores_secrets(saved_date_tool):
    from custom_components.extended_openai_conversation_responses.agent_config import (
        AgentConfigError,
    )
    from custom_components.extended_openai_conversation_responses.secret_redaction import (
        REDACTED_SECRET_SENTINEL,
    )

    _entry, subentry = saved_date_tool
    tools = yaml.safe_load(subentry.data["functions"])
    tools[-1]["function"]["variables"]["password"] = "local-credential"
    subentry.data = {**subentry.data, "functions": yaml.safe_dump(tools)}
    document = json.loads(json.dumps(legacy_agent_export(subentry)))
    exported = yaml.safe_load(document["config"]["functions"])
    assert exported[-1]["function"]["variables"]["password"] == REDACTED_SECRET_SENTINEL
    assert "local-credential" not in json.dumps(document)
    restored = management_ui._parse_import_document(document, destination=subentry)
    variables = yaml.safe_load(restored["config"]["functions"])[-1]["function"][
        "variables"
    ]
    assert variables["reminder_date"] == date(2026, 10, 17)
    assert variables["password"] == "local-credential"
    with pytest.raises(AgentConfigError, match="unavailable secrets"):
        management_ui._parse_import_document(document)
    # Preview must see structured secret markers even inside the YAML transport.
    prepared = transfer.inspect_transfer(document, subentry.subentry_id)
    assert prepared.redacted_sensitive_fields

@pytest.mark.parametrize("number", [0.000001, 1e20, 1.25])
async def test_exact_json_export_import_into_new_agent_preserves_numbers(
    hass, saved_date_tool, management_message, number
):
    _, source = saved_date_tool
    tools = yaml.safe_load(source.data["functions"])
    tools[-1]["function"]["variables"] = {"number": number, "literal": "1e-06"}
    source.data = {**source.data, "functions": yaml.safe_dump(tools)}
    exported = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("configuration", "export", reviewed_custom=True),
    )
    created = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration", "import", document=exported["json"], mode="new"
        ),
    )
    assert created["status"] == "created"
    imported = hass.config_entries.async_add_subentry.call_args.args[1]
    runtime = configured_function_tools_from_data(imported.data)[-1]
    variables = runtime["function"]["variables"].async_render(hass, {})
    assert variables == {"number": number, "literal": "1e-06"}
    assert type(variables["number"]) is float
