"""Tests for CompositeFunction using yaml definitions."""

import pytest

# Import Tools and test helpers
from custom_components.extended_openai_conversation_responses.functions import (
    CompositeFunction,
)
from homeassistant.core import State
from tests.helpers import prepare_function_tool_from_yaml


class TestCompositeFunctionYaml:
    """Test CompositeFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create CompositeFunction instance."""
        return CompositeFunction()

    async def test_composite_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test composite function execution from yaml definition."""
        function_tool = prepare_function_tool_from_yaml("composite_example.yaml")
        function_config = function_tool["function"]

        # Mock sensor states for the living_room
        def mock_states_get(entity_id):
            states_map = {
                "sensor.living_room_temperature": State(
                    "sensor.living_room_temperature", "22.5"
                ),
                "sensor.living_room_humidity": State(
                    "sensor.living_room_humidity", "45"
                ),
                "light.living_room": State("light.living_room", "on"),
            }
            return states_map.get(entity_id)

        hass.states.get = mock_states_get

        # Arguments based on yaml spec parameters
        arguments = {"room_name": "living_room"}

        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        # Should return formatted room status
        assert "Room: Living Room" in result
        assert "Temperature: 22.5°C" in result
        assert "Humidity: 45%" in result
        assert "Light: on" in result

    async def test_composite_with_different_rooms(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test composite function with various rooms."""
        function_tool = prepare_function_tool_from_yaml("composite_example.yaml")
        function_config = function_tool["function"]

        # Mock sensor states for bedroom
        def mock_states_get(entity_id):
            states_map = {
                "sensor.bedroom_temperature": State(
                    "sensor.bedroom_temperature", "20.0"
                ),
                "sensor.bedroom_humidity": State("sensor.bedroom_humidity", "50"),
                "light.bedroom": State("light.bedroom", "off"),
            }
            return states_map.get(entity_id)

        hass.states.get = mock_states_get

        arguments = {"room_name": "bedroom"}
        result = await function.execute(
            hass, function_config, arguments, llm_context, exposed_entities
        )

        assert "Room: Bedroom" in result
        assert "Temperature: 20.0°C" in result
        assert "Humidity: 50%" in result
        assert "Light: off" in result
