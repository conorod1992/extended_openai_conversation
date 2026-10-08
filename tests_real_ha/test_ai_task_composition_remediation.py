"""Inspect generated SDK request for valid HA root alternatives."""

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from homeassistant.components import ai_task
from tests_real_ha.test_ai_task_provider_wire import _task_entity, _text_reply, _wire


@pytest.mark.parametrize("mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
async def test_valid_root_union_does_not_submit_invalid_strict_schema(
    hass, monkeypatch, mode
):
    entry, entity_id = await _task_entity(hass, mode)
    schema = vol.Schema(vol.Any({vol.Required("x"): str}, {vol.Required("y"): int}))
    assert schema({"x": "ready"}) == {"x": "ready"}
    wire = _wire(monkeypatch, entry, [_text_reply(mode, '{"x":"ready"}')])
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="root object without anyOf"):
        await ai_task.async_generate_data(
            hass,
            task_name="Root alternatives",
            entity_id=entity_id,
            instructions="Return either x or y",
            structure=schema,
        )
    assert not wire.requests


@pytest.mark.parametrize("mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
async def test_valid_nested_intersection_does_not_submit_unsupported_allof(
    hass, monkeypatch, mode
):
    entry, entity_id = await _task_entity(hass, mode)
    schema = vol.Schema(
        {
            vol.Required("value"): vol.All(
                {vol.Required("x"): str},
                {vol.Required("x"): vol.All(str, vol.Length(min=1))},
            )
        }
    )
    payload = {"value": {"x": "ready"}}
    assert schema(payload) == payload
    wire = _wire(monkeypatch, entry, [_text_reply(mode, '{"value":{"x":"ready"}}')])
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="allOf compositions"):
        await ai_task.async_generate_data(
            hass,
            task_name="Intersected structure",
            entity_id=entity_id,
            instructions="Return a nonempty value.x",
            structure=schema,
        )
    assert not wire.requests


@pytest.mark.parametrize("mode", [API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS])
async def test_nested_alternatives_and_composition_property_names_are_valid(
    hass, monkeypatch, mode
):
    from tests.strict_provider_contract import validate_request

    entry, entity_id = await _task_entity(hass, mode)
    schema = vol.Schema(
        {vol.Required("anyOf"): str, vol.Required("allOf"): vol.Any(str, int)}
    )
    payload = {"anyOf": "literal field", "allOf": 42}
    wire = _wire(
        monkeypatch, entry, [_text_reply(mode, '{"anyOf":"literal field","allOf":42}')]
    )
    result = await ai_task.async_generate_data(
        hass,
        task_name="Valid alternatives",
        entity_id=entity_id,
        instructions="Return the structure",
        structure=schema,
    )
    assert result.data == payload
    assert len(wire.requests) == 1
    request = wire.requests[0]
    validate_request(request["path"], request["body"])
