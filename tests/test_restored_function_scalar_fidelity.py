"""Normal Function edits must preserve credentials from complete private backups."""

from copy import deepcopy
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_ui as ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.function_yaml import (
    decode_management_function_values,
)
from tests import test_selective_restore_concurrency as concurrency
from tests.test_configuration_projection_invariants import (
    projection_rules,  # noqa: F401
)
from tests.test_management_typed_function_roundtrips import _tool, _wire
from tests.test_private_backup_fidelity import private_document

SEPARATORS = [
    pytest.param("\x85", id="nel"),
    pytest.param("\u2028", id="line-separator"),
    pytest.param("\u2029", id="paragraph-separator"),
    pytest.param("\r\n", id="crlf"),
    pytest.param("\0", id="nul"),
]


@pytest.mark.parametrize("separator", SEPARATORS)
@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("mode", ["field_only", "full", "toggle", "editor"])
@pytest.mark.usefixtures("projection_rules")
async def test_restored_credentials_survive_supported_management_edits(
    monkeypatch,
    hass,
    management_agent,
    management_message,
    separator,
    typed,
    mode,
):
    entry, subentry = management_agent
    secret = "FABRICATED-before" + separator + "[redacted]after"
    value = {1: {"password": secret}} if typed else {"password": secret}
    original = _tool(value)
    # The input artifact contains exact, valid scalar values before any edit.
    source = yaml.safe_dump([original], allow_unicode=False)
    document = private_document()
    document["agent"]["config"] = {"functions": source, "temperature": 0.1}
    before = deepcopy(document["agent"]["config"])
    parent_before = deepcopy(dict(entry.data))
    monkeypatch.setattr(concurrency, "_identity", lambda: management_agent)
    tx = concurrency.RestoreHarness(monkeypatch)
    artifact = json.loads(backup.finalize_backup_snapshot(document)["json"])
    await transfer.async_restore_transfer(
        tx.hass, entry, subentry, artifact, sections=[transfer.SECTION_CONFIGURATION]
    )
    assert subentry.data == before
    agent_config_snapshot(subentry.data)  # Accepted configuration, not quarantine.
    result = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    wire = _wire(result)  # Production projection and native HA JSON serializer.
    assert subentry.data == before
    if mode == "toggle":
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "tools",
                "set_enabled",
                name="typed_tool",
                enabled=False,
                revision=wire["revision"],
            ),
        )
        assert yaml.safe_load(subentry.data["functions"])[0]["enabled"] is False
    elif mode == "editor":
        editable = await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "tools", "serialize", name="typed_tool", revision=wire["revision"]
            ),
        )
        assert yaml.safe_load(editable["yaml"])["function"] == original["function"]
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "tools",
                "save",
                yaml=editable["yaml"],
                original_name="typed_tool",
                revision=wire["revision"],
            ),
        )
    else:
        config = (
            {"temperature": 0.2}
            if mode == "field_only"
            else {**wire["config"], "temperature": 0.2}
        )
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration",
                "update" if mode == "field_only" else "save",
                config=config,
                revision=wire["revision"],
            ),
        )
    saved = yaml.safe_load(subentry.data["functions"])[0]
    assert saved["function"] == original["function"]
    assert dict(entry.data) == parent_before
    if mode == "field_only":
        assert subentry.data["functions"] == source


@pytest.mark.parametrize("separator", SEPARATORS)
def test_typed_management_wire_round_trip_preserves_every_scalar(separator):
    value = {1: {"password": "FABRICATED-before" + separator + "after"}}
    original = _tool(value)
    result = _wire({"config": {"functions": [original]}})
    decoded = decode_management_function_values(result)
    assert decoded["config"]["functions"] == [original]


@pytest.mark.parametrize("separator", SEPARATORS)
def test_native_schema_migration_preserves_custom_sibling_credentials(separator):
    from custom_components.extended_openai_conversation_responses import (
        native_function_schema_migration as migration,
    )
    from tests.test_native_function_schema_migration import _tool as stock_tool

    custom = _tool({1: {"password": "FABRICATED-before" + separator + "after"}})
    stock = stock_tool(
        "execute_service", migration._LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS
    )
    source = yaml.safe_dump([stock, custom], allow_unicode=False)
    migrated, changed = migration.migrate_legacy_stock_native_function_tools_yaml(
        source
    )
    assert changed is True
    assert yaml.safe_load(migrated)[1] == custom


async def test_runtime_quarantine_preserves_valid_sibling_credentials(hass, caplog):
    from custom_components.extended_openai_conversation_responses.function_tool_quarantine import (
        _runtime_configured_function_tools,
    )

    secret = "FABRICATED-before\x85after"
    custom = _tool({"password": secret})
    invalid = deepcopy(custom)
    invalid["spec"]["name"] = "invalid_sibling"
    invalid["function"]["type"] = "unsupported_backend"
    config = {"functions": yaml.safe_dump([custom, invalid], allow_unicode=False)}
    runtime = _runtime_configured_function_tools(config)
    assert len(runtime) == 1
    assert (
        deepcopy(runtime[0])["function"]["variables"] == custom["function"]["variables"]
    )
    assert secret not in caplog.text
