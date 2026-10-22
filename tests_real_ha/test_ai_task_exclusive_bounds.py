"""Public structured AI Tasks use numeric bounds on the real SDK HTTP wire."""

from jsonschema import Draft202012Validator
import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
)
from homeassistant.components import ai_task
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_ai_task_provider_wire import (
    MODES,
    _task_entity,
    _text_reply,
    _wire,
)


@pytest.mark.parametrize("mode", MODES)
async def test_public_ai_task_exclusive_bounds_and_authoritative_validation(
    hass, monkeypatch, mode
):
    entry, entity_id = await _task_entity(hass, mode)
    structure = vol.Schema(
        {
            vol.Required("duration"): vol.All(
                float, vol.Range(min=0, max=1, min_included=False, max_included=False)
            )
        }
    )
    wire = _wire(
        monkeypatch,
        entry,
        [
            _text_reply(mode, '{"duration":0.5}'),
            _text_reply(mode, '{"duration":0.0}'),
            _text_reply(mode, '{"duration":1.0}'),
        ],
    )

    async def generate():
        return await ai_task.async_generate_data(
            hass,
            task_name="Exclusive bounds",
            entity_id=entity_id,
            instructions="Return a duration",
            structure=structure,
        )

    assert (await generate()).data == {"duration": 0.5}
    body = wire.requests[0]["body"]
    emitted = (
        body["text"]["format"]
        if mode == API_MODE_RESPONSES
        else body["response_format"]["json_schema"]
    )["schema"]
    Draft202012Validator.check_schema(emitted)
    assert emitted["properties"]["duration"]["exclusiveMinimum"] == 0
    assert emitted["properties"]["duration"]["exclusiveMaximum"] == 1
    assert not isinstance(emitted["properties"]["duration"]["exclusiveMinimum"], bool)
    for invalid in (0.0, 1.0):
        with pytest.raises(HomeAssistantError, match="requested structure"):
            await generate()
        with pytest.raises(vol.Invalid):
            structure({"duration": invalid})
    assert len(wire.requests) == 3
