"""Regression coverage for manual exposed-entity prompt control."""

from custom_components.extended_openai_conversation_responses.const import (
    CONF_EXPOSED_ENTITIES_ENABLED,
)
from custom_components.extended_openai_conversation_responses.exposed_attributes import (
    CONF_EXPOSED_ENTITY_ATTRIBUTES,
    enrich_exposed_entities,
)


def test_manual_prompt_mode_does_not_enrich_exposed_entities() -> None:
    """Saved attribute selections must stay inert when automatic context is off."""
    exposed = [
        {
            "entity_id": "light.kitchen",
            "name": "Kitchen Light",
            "state": "on",
            "aliases": [],
        }
    ]
    options = {
        CONF_EXPOSED_ENTITIES_ENABLED: False,
        CONF_EXPOSED_ENTITY_ATTRIBUTES: {
            "registry:stable-entry": ["brightness", "color_temp_kelvin"]
        },
    }

    result = enrich_exposed_entities(object(), options, exposed)

    assert result is exposed
    assert "attributes" not in result[0]


def test_missing_automatic_context_flag_defaults_to_inactive() -> None:
    """Legacy/direct config without the toggle must not leak selected attributes."""
    exposed = [
        {
            "entity_id": "sensor.example",
            "name": "Example Sensor",
            "state": "ready",
            "aliases": [],
        }
    ]
    options = {
        CONF_EXPOSED_ENTITY_ATTRIBUTES: {
            "registry:stable-entry": ["private_detail"]
        }
    }

    result = enrich_exposed_entities(object(), options, exposed)

    assert result is exposed
    assert "attributes" not in result[0]
