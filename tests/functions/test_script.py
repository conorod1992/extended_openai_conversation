"""Tests for ScriptFunction using yaml definitions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import Tools and test helpers
from custom_components.extended_openai_conversation_responses.functions import (
    ScriptFunction,
)
from tests.helpers import prepare_function_tool_from_yaml


class TestScriptFunctionYaml:
    """Test ScriptFunction using yaml definitions."""

    @pytest.fixture
    def function(self):
        """Create ScriptFunction instance."""
        return ScriptFunction()

    async def test_execute_script_from_yaml(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test script execution from yaml definition."""
        # Load function from yaml
        function_tool = prepare_function_tool_from_yaml("script_example.yaml")
        function_config = function_tool["function"]

        with patch(
            "custom_components.extended_openai_conversation_responses.functions.script.Script"
        ) as mock_script_class:
            # Setup mock
            mock_script = AsyncMock()
            mock_result = MagicMock()
            mock_result.variables = {"_function_result": "Movie mode activated"}
            mock_script.async_run = AsyncMock(return_value=mock_result)
            mock_script.async_unload = AsyncMock()
            mock_script_class.return_value = mock_script

            # Arguments based on yaml spec parameters (brightness_pct is optional with default 10)
            arguments = {"brightness_pct": 15}

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            assert result == "Movie mode activated"
            mock_script.async_run.assert_awaited_once()
            mock_script.async_unload.assert_awaited_once_with()

    async def test_execute_script_with_defaults(
        self, hass, function, exposed_entities, llm_context
    ):
        """Test script execution with default brightness."""
        function_tool = prepare_function_tool_from_yaml("script_example.yaml")
        function_config = function_tool["function"]

        with patch(
            "custom_components.extended_openai_conversation_responses.functions.script.Script"
        ) as mock_script_class:
            mock_script = AsyncMock()
            mock_result = MagicMock()
            mock_result.variables = {"_function_result": "Movie mode activated"}
            mock_script.async_run = AsyncMock(return_value=mock_result)
            mock_script.async_unload = AsyncMock()
            mock_script_class.return_value = mock_script

            # No arguments, should use default brightness_pct
            arguments = {}

            result = await function.execute(
                hass, function_config, arguments, llm_context, exposed_entities
            )

            assert result == "Movie mode activated"
            mock_script.async_run.assert_awaited_once()
            mock_script.async_unload.assert_awaited_once_with()

    async def test_execute_script_unloads_after_failure(
        self, hass, function, exposed_entities, llm_context
    ):
        """Transient Script runners are unloaded even when the sequence fails."""
        function_tool = prepare_function_tool_from_yaml("script_example.yaml")
        function_config = function_tool["function"]

        with patch(
            "custom_components.extended_openai_conversation_responses.functions.script.Script"
        ) as mock_script_class:
            mock_script = AsyncMock()
            mock_script.async_run = AsyncMock(side_effect=RuntimeError("script failed"))
            mock_script.async_unload = AsyncMock()
            mock_script_class.return_value = mock_script

            with pytest.raises(RuntimeError, match="script failed"):
                await function.execute(
                    hass, function_config, {}, llm_context, exposed_entities
                )

            mock_script.async_unload.assert_awaited_once_with()

    async def test_execute_validates_dynamic_actions_before_script(
        self, hass, function, exposed_entities, llm_context
    ):
        """HA-aware action validation runs before constructing the transient Script."""
        original_action = {
            "device_id": "device-id",
            "domain": "light",
            "entity_id": "entity-registry-id",
            "type": "turn_on",
        }
        function_config = {"type": "script", "sequence": [original_action]}
        validated_sequence = [
            {
                "device_id": "device-id",
                "domain": "light",
                "entity_id": "light.kitchen",
                "type": "turn_on",
            }
        ]

        async def validate_actions(_hass, sequence):
            assert sequence is not function_config["sequence"]
            assert sequence[0] is not original_action
            sequence[0]["entity_id"] = "mutated-during-validation"
            return validated_sequence

        with (
            patch(
                "custom_components.extended_openai_conversation_responses.functions.script.async_validate_actions_config",
                side_effect=validate_actions,
            ) as mock_validate,
            patch(
                "custom_components.extended_openai_conversation_responses.functions.script.Script"
            ) as mock_script_class,
        ):
            mock_script = AsyncMock()
            mock_script.async_run = AsyncMock(return_value=None)
            mock_script.async_unload = AsyncMock()
            mock_script_class.return_value = mock_script

            result = await function.execute(
                hass, function_config, {}, llm_context, exposed_entities
            )

        assert result == "Success"
        mock_validate.assert_awaited_once()
        assert function_config["sequence"] == [original_action]
        assert original_action["entity_id"] == "entity-registry-id"
        assert mock_script_class.call_args.args[1] is validated_sequence
        mock_script.async_run.assert_awaited_once()
        mock_script.async_unload.assert_awaited_once_with()

    async def test_execute_does_not_run_script_when_action_validation_fails(
        self, hass, function, exposed_entities, llm_context
    ):
        """Invalid HA-aware actions fail before a transient Script is constructed."""
        function_config = {
            "type": "script",
            "sequence": [
                {
                    "device_id": "missing-device",
                    "domain": "light",
                    "entity_id": "missing-entity",
                    "type": "turn_on",
                }
            ],
        }

        with (
            patch(
                "custom_components.extended_openai_conversation_responses.functions.script.async_validate_actions_config",
                new=AsyncMock(side_effect=RuntimeError("invalid device action")),
            ) as mock_validate,
            patch(
                "custom_components.extended_openai_conversation_responses.functions.script.Script"
            ) as mock_script_class,
        ):
            with pytest.raises(RuntimeError, match="invalid device action"):
                await function.execute(
                    hass, function_config, {}, llm_context, exposed_entities
                )

        mock_validate.assert_awaited_once()
        mock_script_class.assert_not_called()
