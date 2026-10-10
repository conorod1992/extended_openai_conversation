"""Tests for ScriptFunction using yaml definitions."""

from types import SimpleNamespace
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
        original_action = {"condition": "template", "value_template": "{{ true }}"}
        function_config = {"type": "script", "sequence": [original_action]}
        validated_sequence = [{"condition": "template", "value_template": "{{ true }}"}]

        async def validate_actions(_hass, sequence):
            assert sequence is not function_config["sequence"]
            assert sequence[0] is not original_action
            sequence[0]["value_template"] = "{{ false }}"
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
            mock_script.async_run = AsyncMock(
                return_value=SimpleNamespace(variables={})
            )
            mock_script.async_unload = AsyncMock()
            mock_script_class.return_value = mock_script

            result = await function.execute(
                hass, function_config, {}, llm_context, exposed_entities
            )

        assert result == "Success"
        mock_validate.assert_awaited_once()
        assert function_config["sequence"] == [original_action]
        assert original_action["value_template"] == "{{ true }}"
        assert mock_script_class.call_args.args[1] is validated_sequence
        mock_script.async_run.assert_awaited_once()
        mock_script.async_unload.assert_awaited_once_with()

    async def test_execute_does_not_run_script_when_action_validation_fails(
        self, hass, function, exposed_entities, llm_context
    ):
        """Invalid HA-aware actions fail before a transient Script is constructed."""
        function_config = {
            "type": "script",
            "sequence": [{"condition": "template", "value_template": "{{ true }}"}],
        }

        with (
            patch(
                "custom_components.extended_openai_conversation_responses.functions.script.async_validate_actions_config",
                new=AsyncMock(side_effect=RuntimeError("invalid condition")),
            ) as mock_validate,
            patch(
                "custom_components.extended_openai_conversation_responses.functions.script.Script"
            ) as mock_script_class,
            pytest.raises(RuntimeError, match="invalid condition"),
        ):
            await function.execute(
                hass, function_config, {}, llm_context, exposed_entities
            )

        mock_validate.assert_awaited_once()
        mock_script_class.assert_not_called()


async def test_script_function_older_cleanup_surface_and_empty_run(hass):
    from custom_components.extended_openai_conversation_responses.functions import (
        script as module,
    )
    from homeassistant.exceptions import HomeAssistantError

    older = SimpleNamespace(
        async_run=AsyncMock(
            return_value=SimpleNamespace(variables={"_function_result": False})
        ),
        async_stop=AsyncMock(),
    )
    with patch.object(module, "Script", return_value=older):
        assert (
            await ScriptFunction().execute(hass, {"sequence": []}, {}, None, [])
            is False
        )
        assert (
            await ScriptFunction().execute(hass, {"sequence": []}, {}, None, [])
            is False
        )
        assert older.async_stop.await_count == 2
        older.async_run.return_value = None
        with pytest.raises(HomeAssistantError, match="did not complete"):
            await ScriptFunction().execute(hass, {"sequence": []}, {}, None, [])
    assert older.async_stop.await_count == 3


@pytest.mark.parametrize(
    ("native_trace", "raises"),
    [
        ({}, True),
        ({"not-a-step": []}, True),
        (
            {
                "0": [
                    SimpleNamespace(
                        as_dict=MagicMock(return_value={"result": {"result": False}})
                    )
                ]
            },
            False,
        ),
        (
            {
                "0": [
                    SimpleNamespace(
                        as_dict=MagicMock(
                            return_value={
                                "result": {"result": False},
                                "error": "native abort",
                            }
                        )
                    )
                ]
            },
            True,
        ),
        (
            {
                "0": [
                    SimpleNamespace(
                        as_dict=MagicMock(
                            return_value={
                                "result": {"result": False},
                                "template_errors": ["failed template"],
                            }
                        )
                    )
                ]
            },
            True,
        ),
    ],
)
async def test_aborted_script_only_treats_false_top_level_condition_as_success(
    hass, monkeypatch, native_trace, raises
):
    from custom_components.extended_openai_conversation_responses.functions import (
        script as module,
    )
    from homeassistant.exceptions import HomeAssistantError

    sequence = [{"condition": "template", "value_template": "{{ false }}"}]
    script = SimpleNamespace(
        async_run=AsyncMock(),
        async_unload=AsyncMock(),
    )

    async def run(**_kwargs):
        module.trace.script_execution_set("aborted")
        return SimpleNamespace(variables={})

    script.async_run.side_effect = run
    monkeypatch.setattr(
        module, "async_validate_actions_config", AsyncMock(return_value=sequence)
    )
    monkeypatch.setattr(module, "Script", MagicMock(return_value=script))
    monkeypatch.setattr(module.trace, "trace_get", MagicMock(return_value=native_trace))

    if raises:
        with pytest.raises(HomeAssistantError, match="aborted before completion"):
            await ScriptFunction().execute(hass, {"sequence": sequence}, {}, None, [])
    else:
        assert (
            await ScriptFunction().execute(hass, {"sequence": sequence}, {}, None, [])
            == "Success"
        )
    script.async_unload.assert_awaited_once()


@pytest.mark.parametrize("reject", [False, True])
async def test_script_scoped_service_boundary_preserves_response_or_blocks_dispatch(
    hass, monkeypatch, reject
):
    from custom_components.extended_openai_conversation_responses import ha_actions
    from custom_components.extended_openai_conversation_responses.functions.script import (
        _AuthorizedScriptServices,
    )
    from homeassistant.core import Context
    from homeassistant.exceptions import HomeAssistantError

    function = ScriptFunction()
    function.validate_entity_ids = MagicMock()
    authorize = AsyncMock(return_value={"light.kitchen"})
    if reject:
        authorize.side_effect = HomeAssistantError("Not permitted")
    monkeypatch.setattr(ha_actions, "async_authorize_ha_action", authorize)
    hass.services.async_call = AsyncMock(return_value={"value": "response"})
    context = Context(user_id="restricted")
    boundary = _AuthorizedScriptServices(
        hass, function, [{"entity_id": "light.kitchen"}]
    )
    if reject:
        with pytest.raises(HomeAssistantError):
            await boundary.async_call(
                "light",
                "turn_on",
                {},
                True,
                context,
                {"entity_id": "light.kitchen"},
                True,
            )
        hass.services.async_call.assert_not_awaited()
    else:
        result = await boundary.async_call(
            "light", "turn_on", {}, True, context, {"entity_id": "light.kitchen"}, True
        )
        assert result == {"value": "response"}
        function.validate_entity_ids.assert_called_once_with(
            hass,
            ["light.kitchen"],
            [{"entity_id": "light.kitchen"}],
            require_available=True,
        )
        hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {},
            blocking=True,
            context=context,
            target={"entity_id": "light.kitchen"},
            return_response=True,
        )
    assert authorize.await_args.kwargs["context"] is context
