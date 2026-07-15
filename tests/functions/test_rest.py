"""Tests for RestFunction using yaml definitions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import Tools and test helpers
from custom_components.extended_openai_conversation_responses.functions import (
    RestFunction,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestRestFunctionYaml:
    """Test RestFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create RestFunction instance."""
        return RestFunction()

    async def test_execute_rest_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test REST API call from yaml definition."""
        # Load function from yaml
        function_tool = prepare_function_tool_from_yaml("rest_example.yaml")
        function_config = function_tool["function"]

        with patch(
            "custom_components.extended_openai_conversation_responses.functions.web.rest.create_rest_data_from_config"
        ) as mock_create_rest:
            mock_rest_data = AsyncMock()
            mock_rest_data.async_update = AsyncMock()
            # Mock weather API response
            weather_response = """{
                "name": "Seoul",
                "weather": [{"description": "clear sky"}],
                "main": {
                    "temp": 15.5,
                    "feels_like": 14.2,
                    "humidity": 65
                },
                "wind": {"speed": 3.5}
            }"""
            mock_rest_data.data_without_xml = MagicMock(return_value=weather_response)
            mock_create_rest.return_value = mock_rest_data

            # Arguments based on yaml spec parameters
            arguments = {"city": "Seoul", "country_code": "KR"}

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            # value_template should format weather information
            assert result is not None
            assert "Seoul" in result
            mock_rest_data.async_update.assert_called_once()
