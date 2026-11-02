"""Typed Function values survive real Management mutation/JSON boundaries."""

from copy import deepcopy
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.function_yaml import (
    decode_management_function_request,
    decode_management_function_values,
    management_function_response,
)
from homeassistant.components.websocket_api.messages import message_to_json_bytes
from homeassistant.exceptions import HomeAssistantError
from tests.helpers import legacy_agent_export
from tests.test_configuration_projection_invariants import projection_rules  # noqa: F401


def _tool(value):
    return {
        "spec": {
            "name": "typed_tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "variables": {"marker": value},
            "sequence": [{"stop": "Done"}],
        },
    }


def _wire(result):
    message = {
        "id": 1,
        "type": "result",
        "success": True,
        "result": management_function_response(result),
    }
    decoded = json.loads(message_to_json_bytes(message))
    assert decoded["success"], decoded
    return decoded["result"]


VALUES = [
    date(2026, 10, 17),
    datetime(2026, 10, 17, 12, 30, tzinfo=timezone.utc),
    b"Hello\x00\xff",
    {"one", "two"},
    {date(2026, 10, 17): "date key", "ordinary": False},
    {"__eoai_management_yaml_v1__": "literal administrator data"},
    10**100,
    2**53 + 1,
    -(2**53 + 1),
    {7: "integer key", None: "null key"},
    float("inf"),
]


def _same_typed_value(actual, expected):
    assert type(actual) is type(expected)
    if isinstance(expected, dict):
        assert len(actual) == len(expected)
        for key, value in expected.items():
            matches = [
                item for item in actual if type(item) is type(key) and item == key
            ]
            assert len(matches) == 1
            _same_typed_value(actual[matches[0]], value)
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for item, value in zip(actual, expected, strict=True):
            _same_typed_value(item, value)
    elif isinstance(expected, set):
        assert {(type(item), item) for item in actual} == {
            (type(item), item) for item in expected
        }
    else:
        assert actual == expected


@pytest.mark.parametrize(
    "value",
    VALUES,
    ids=[
        "date",
        "datetime",
        "binary",
        "set",
        "typed-key",
        "literal-tag",
        "large-int",
        "unsafe-int",
        "negative-unsafe-int",
        "numeric-key",
        "infinity",
    ],
)
async def test_save_json_editor_and_full_configuration_roundtrip(
    hass, management_agent, management_message, projection_rules, value
):
    _, subentry = management_agent
    subentry.data = {"functions": "[]"}
    original = _tool(value)
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "tools",
            "save",
            yaml=yaml.safe_dump(original),
            revision=ui._agent_config_revision(subentry.data, subentry.title),
        ),
    )
    response = _wire(result)
    assert yaml.safe_load(subentry.data["functions"])[0] == original
    if type(value) is int and abs(value) > 2**53 - 1:
        # A JSON number here is rounded by browser JSON.parse/structured drafts.
        assert isinstance(
            response["functions"][0]["function"]["variables"]["marker"], dict
        )
    assert decode_management_function_values(response)["functions"][0] == original
    cloned = deepcopy(response["functions"][0])
    cloned["spec"]["name"] = "typed_clone"
    clone_yaml = await ui.async_management_command(
        hass, "admin", True, management_message("tools", "serialize", tool=cloned)
    )
    assert yaml.safe_load(clone_yaml["yaml"])["function"] == original["function"]
    before = deepcopy(subentry.data)
    editable = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "tools", "serialize", name="typed_tool", revision=response["revision"]
        ),
    )
    assert yaml.safe_load(editable["yaml"]) == original
    assert subentry.data == before
    saved = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "tools",
            "save",
            yaml=editable["yaml"],
            original_name="typed_tool",
            revision=response["revision"],
        ),
    )
    wire = _wire(saved)
    # A shared draft can explicitly submit the structured Functions collection.
    # Decode its tags before validation, including enabled/metadata-only edits.
    wire["functions"][0]["enabled"] = False
    updated = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            config={"functions": wire["functions"]},
            revision=wire["revision"],
        ),
    )
    _wire(updated)
    stored = yaml.safe_load(subentry.data["functions"])[0]
    assert stored["function"] == original["function"]
    assert stored["enabled"] is False
    with pytest.raises(HomeAssistantError, match="Configuration changed"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "tools", "serialize", name="typed_tool", revision=response["revision"]
            ),
        )


@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("entrypoint", ["document", "json", "yaml"])
async def test_export_uses_lossless_yaml_for_non_json_values(hass, value, entrypoint):
    from custom_components.extended_openai_conversation_responses import backup

    original = _tool(value)
    exported = backup.export_configuration_snapshot(
        {"functions": yaml.safe_dump([original])}
    )
    functions = exported["functions"]
    if value == {"__eoai_management_yaml_v1__": "literal administrator data"}:
        assert functions == [original]  # Ordinary JSON still stays structured.
    else:
        assert isinstance(functions, str)
        assert yaml.safe_load(functions) == [original]
    subentry = SimpleNamespace(
        title="Typed tool", data={"functions": yaml.safe_dump([original])}
    )
    document = json.loads(json.dumps(legacy_agent_export(subentry)))
    response = _wire({"document": document, "json": json.dumps(document)})
    assert response["document"] == json.loads(response["json"])
    document = {
        "document": response["document"],
        "json": response["json"],
        "yaml": yaml.safe_dump(response["document"]),
    }[entrypoint]
    restored = ui._parse_import_document(document, destination=subentry)
    assert (
        yaml.safe_load(restored["config"]["functions"])[0]["function"]
        == original["function"]
    )
    _same_typed_value(
        yaml.safe_load(restored["config"]["functions"])[0]["function"]["variables"][
            "marker"
        ],
        value,
    )
    before_runtime = agent_config.configured_function_tools_from_data(subentry.data)[0][
        "function"
    ]["variables"].async_render(hass, {})
    after_runtime = agent_config.configured_function_tools_from_data(
        restored["config"]
    )[0]["function"]["variables"].async_render(hass, {})
    _same_typed_value(after_runtime, before_runtime)


@pytest.mark.parametrize(
    "value,public",
    [(value, False) for value in VALUES if value is not VALUES[5]]
    + [
        (value, True) for value in [VALUES[5], 0.000001, True, 1, None, {}, [], "1e-06"]
    ],
)
@pytest.mark.parametrize("entrypoint", ["document", "json", "yaml"])
async def test_current_export_text_preserves_typed_runtime_values(
    hass, management_agent, management_message, value, public, entrypoint
):
    _, subentry = management_agent
    subentry.data = {"functions": yaml.safe_dump([_tool(value)])}
    runtime_before = agent_config.configured_function_tools_from_data(subentry.data)[0][
        "function"
    ]["variables"].async_render(hass, {})
    exported = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("configuration", "export", reviewed_custom=True),
    )
    document = {
        "document": exported["document"],
        "json": exported["json"],
        "yaml": yaml.safe_dump(exported["document"]),
    }[entrypoint]
    restored = ui._parse_import_document(document, destination=subentry)
    if not public:
        # Public sharing deliberately omits opaque typed YAML; legacy/private
        # codecs preserve it. Importing text must not bypass that policy.
        assert exported["document"]["config"]["functions"] == []
        assert exported["document"]["security"]["omitted"]
        assert yaml.safe_load(restored["config"]["functions"]) == []
        return
    stored = yaml.safe_load(restored["config"]["functions"])[0]
    _same_typed_value(stored["function"]["variables"]["marker"], value)
    runtime_after = agent_config.configured_function_tools_from_data(
        restored["config"]
    )[0]["function"]["variables"].async_render(hass, {})
    _same_typed_value(runtime_after, runtime_before)


async def test_text_corpus_detects_yaml_only_json_import(
    hass, management_agent, management_message, monkeypatch
):
    original = ui._parse_import_document
    monkeypatch.setattr(
        ui,
        "_parse_import_document",
        lambda value, **kwargs: original(yaml.safe_load(value), **kwargs),
    )
    with pytest.raises(AssertionError):
        await test_current_export_text_preserves_typed_runtime_values(
            hass, management_agent, management_message, 0.000001, True, "json"
        )


async def test_yaml_set_exports_redact_recognizable_credentials_and_recover(hass):
    from custom_components.extended_openai_conversation_responses import backup
    from custom_components.extended_openai_conversation_responses.agent_config import (
        AgentConfigError,
    )
    from custom_components.extended_openai_conversation_responses.secret_redaction import (
        REDACTED_SECRET_SENTINEL,
    )

    secret = "sk-12345678901234567890"
    original = _tool({secret, "ordinary"})
    subentry = SimpleNamespace(
        title="Typed tool", data={"functions": yaml.safe_dump([original])}
    )
    document = json.loads(json.dumps(legacy_agent_export(subentry)))
    assert secret not in json.dumps(document)
    exported = document["config"]["functions"]
    exported = yaml.safe_load(exported) if isinstance(exported, str) else exported
    assert exported[0]["function"]["variables"]["marker"] == REDACTED_SECRET_SENTINEL
    backup_config = backup._safe_configuration(
        backup.export_configuration_snapshot(subentry.data)
    )
    assert secret not in json.dumps(backup_config)
    restored = ui._parse_import_document(document, destination=subentry)
    assert (
        yaml.safe_load(restored["config"]["functions"])[0]["function"]
        == original["function"]
    )
    with pytest.raises(AgentConfigError, match="unavailable secrets"):
        ui._parse_import_document(document)


async def test_yaml_set_export_keeps_nonsecret_literal_redaction_text(hass):
    original = _tool({"ordinary", "literal [redacted] prose"})
    subentry = SimpleNamespace(
        title="Typed tool", data={"functions": yaml.safe_dump([original])}
    )
    document = json.loads(json.dumps(legacy_agent_export(subentry)))
    restored = ui._parse_import_document(document)
    assert (
        yaml.safe_load(restored["config"]["functions"])[0]["function"]
        == original["function"]
    )


@pytest.mark.parametrize("saved", [None, "", [], "null", "[]"])
@pytest.mark.parametrize("edit", [None, "", [], "null", "[]"])
async def test_explicit_empty_management_edit_does_not_adopt_legacy_defaults(
    hass, management_agent, management_message, projection_rules, saved, edit
):
    _, subentry = management_agent
    subentry.data = {"functions": saved}
    effective_before = agent_config.parse_configured_function_tools(subentry.data)
    assert bool(effective_before) == (saved is None or saved == "" or saved == [])
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            config={"functions": edit},
            revision=ui._agent_config_revision(subentry.data, subentry.title),
        ),
    )
    assert result["config"]["functions"] == []
    assert agent_config.parse_configured_function_tools(subentry.data) == []


async def test_invalid_wire_yaml_is_rejected_before_mutation(
    hass, management_agent, management_message
):
    _, subentry = management_agent
    before = deepcopy(subentry.data)
    with pytest.raises(HomeAssistantError, match="Invalid typed YAML"):
        await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "configuration",
                "save",
                config={
                    "functions": [_tool({"__eoai_management_yaml_v1__": "[broken"})]
                },
                revision=ui._agent_config_revision(subentry.data, subentry.title),
            ),
        )
    assert subentry.data == before


def test_wire_tags_do_not_change_unrelated_management_fields():
    value = {"metadata": {"__eoai_management_yaml_v1__": "literal administrator data"}}
    assert management_function_response(value) == value
    assert decode_management_function_values(value) == value


@pytest.mark.parametrize(
    "section",
    ["tools", "configuration", "function_repair", "request_rules", [], {}, None, 42],
)
def test_function_wire_decoder_preserves_arbitrary_arguments(section):
    payload = {
        "spec": "ordinary payload",
        "function": {"__eoai_management_yaml_v1__": "[broken"},
    }
    message = {"section": section, "action": "test", "arguments": payload}
    assert decode_management_function_request(message) == message


@pytest.mark.parametrize("value", [0, 2**53 - 1, -(2**53 - 1)])
def test_safe_integer_functions_keep_structured_json(value):
    tool = _tool(value)
    assert management_function_response(tool) == tool


async def test_rule_payload_resembling_function_is_not_wire_decoded(
    hass, management_agent, management_message, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import request_rules
    from tests.test_request_rules_management import MemoryStore, local_rule

    rules = request_rules.RequestRules(MemoryStore())
    await rules.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    literal = {
        "spec": "event payload",
        "function": {"__eoai_management_yaml_v1__": "2026-10-17"},
    }
    rule = local_rule()
    rule["action"]["actions"] = [{"event": "review_probe", "event_data": literal}]
    message = management_message(
        "request_rules", "create", rule=rule, revision=rules.revision()
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=True),
        send_result=Mock(),
        send_error=Mock(),
    )
    await ui.websocket_management.__wrapped__(hass, connection, {"id": 1, **message})
    connection.send_error.assert_not_called()
    result = connection.send_result.call_args.args[1]
    assert result["rule"]["action"]["actions"][0]["event_data"] == literal
    assert rules.snapshot()["rules"][0]["action"]["actions"][0]["event_data"] == literal


async def test_saved_yaml_lookup_isolates_invalid_siblings(
    hass, management_agent, management_message
):
    _, subentry = management_agent
    tool = _tool(date(2026, 10, 17))
    broken = {"spec": {"name": "broken"}, "function": {"type": "unknown"}}
    subentry.data = {"functions": yaml.safe_dump([tool, broken])}
    before = deepcopy(subentry.data)
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "tools",
            "serialize",
            name="typed_tool",
            revision=ui._agent_config_revision(subentry.data, subentry.title),
        ),
    )
    assert yaml.safe_load(result["yaml"]) == tool
    assert subentry.data == before


@pytest.mark.parametrize("empty", [None, ""])
@pytest.mark.parametrize("validated", [False, True])
async def test_decoded_fast_snapshot_does_not_reapply_persisted_fallback(
    empty, validated
):
    from custom_components.extended_openai_conversation_responses.management_loading_performance import (
        _snapshot_normalized_configuration,
    )

    assert (
        _snapshot_normalized_configuration({"functions": empty}, validated=validated)[
            "functions"
        ]
        == []
    )
