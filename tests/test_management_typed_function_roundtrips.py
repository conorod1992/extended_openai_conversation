"""Typed Function values survive real Management mutation/JSON boundaries."""

from copy import deepcopy
from datetime import date, datetime, timezone
import json

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    management_ui as ui,
)
from custom_components.extended_openai_conversation_responses.function_yaml import (
    decode_management_function_values,
    management_function_response,
)
from homeassistant.components.websocket_api.messages import message_to_json_bytes
from homeassistant.exceptions import HomeAssistantError
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
]


@pytest.mark.parametrize(
    "value",
    VALUES,
    ids=["date", "datetime", "binary", "set", "typed-key", "literal-tag"],
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
