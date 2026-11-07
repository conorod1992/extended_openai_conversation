"""Selecting an Azure effort must survive the actual AI Task form save."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIAITaskSubentryFlowHandler,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("bound", [False, True])
async def test_ai_task_form_saves_the_selected_azure_effort(hass, bound):
    options = {
        "chat_model": "o3-pro",
        "api_mode": "responses",
        "max_tokens": 8192,
        "functions": [],
        **({"azure_model": "o3-pro"} if bound else {}),
    }
    entry = SimpleNamespace(data={"api_provider": "azure"})
    flow = SimpleNamespace(
        options=options,
        _temp_data=None,
        _is_new=False,
        _get_entry=lambda: entry,
        _get_reconfigure_subentry=lambda: SimpleNamespace(data=options),
        async_show_form=lambda **kwargs: kwargs,
        add_suggested_values_to_schema=lambda schema, values: schema,
        async_update_and_abort=lambda *args, **kwargs: kwargs,
    )
    saved = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
        flow, {"reasoning_effort": "high"}
    )
    assert saved["data"]["reasoning_effort"] == "high"
    assert build_provider_request_snapshot(
        saved["data"], entry.data, tools_required=False
    ).api_kwargs["reasoning"] == {"effort": "high"}


def _reconfigure_task(options, *, provider="azure"):
    """Exercise actual AI Task handlers with HA's omission-shaped form inputs."""
    entry = SimpleNamespace(
        data={"api_provider": provider},
        state=ConfigEntryState.LOADED,
    )
    flow = SimpleNamespace(
        options=dict(options),
        _temp_data=None,
        _is_new=False,
        _get_entry=lambda: entry,
        _get_reconfigure_subentry=lambda: SimpleNamespace(data=options),
        async_show_form=lambda **kwargs: kwargs,
        add_suggested_values_to_schema=lambda schema, _values: schema,
        async_update_and_abort=lambda *args, **kwargs: kwargs,
    )
    flow.async_step_advanced = lambda: (
        ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(flow)
    )
    return flow, entry


@pytest.mark.parametrize("advanced", [False, True])
async def test_native_ai_task_clears_azure_binding_before_model_resolution(
    hass, advanced
):
    original = {
        "chat_model": "reasoning-deployment",
        "azure_model": "o3-pro",
        "api_mode": "auto",
        "reasoning_effort": "high",
        "max_tokens": 500,
        "legacy_untouched": {"value": "preserve"},
    }
    flow, entry = _reconfigure_task(original)
    first = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(flow)
    assert "clear_azure_model" in {
        str(key) for key in first["data_schema"].schema
    }
    # Home Assistant drops blank strings, but it submits a checked boolean.
    submitted = {
        "chat_model": "gpt-4o",
        "clear_azure_model": True,
        "advanced_options": advanced,
    }
    result = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
        flow, submitted
    )
    if advanced:
        assert result["step_id"] == "advanced"
        result = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            flow, {}
        )
    saved = result["data"]
    assert "azure_model" not in saved
    assert not any(key.startswith("clear_") for key in saved)
    assert saved["legacy_untouched"] == original["legacy_untouched"]
    assert flow.options == original
    snapshot = build_provider_request_snapshot(
        saved, entry.data, tools_required=False
    )
    assert snapshot.api_kwargs["model"] == "gpt-4o"
    assert snapshot.api_kwargs["stream"] is True
    assert "reasoning" not in snapshot.api_kwargs


async def test_native_ai_task_explicit_sampling_resets_and_zero_values(hass):
    original = {
        "chat_model": "gpt-4o",
        "api_mode": "auto",
        "temperature": 0.2,
        "top_p": 0.8,
        "max_tokens": 500,
    }
    flow, entry = _reconfigure_task(original)
    await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
        flow, {"advanced_options": True}
    )
    form = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(flow)
    keys = {str(key) for key in form["data_schema"].schema}
    assert {"clear_temperature", "clear_top_p"} <= keys
    result = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
        flow, {"clear_temperature": True, "clear_top_p": True}
    )
    saved = result["data"]
    assert "temperature" not in saved and "top_p" not in saved
    assert not any(key.startswith("clear_") for key in saved)
    request = build_provider_request_snapshot(
        saved, entry.data, tools_required=False
    )
    assert "temperature" not in request.api_kwargs
    assert "top_p" not in request.api_kwargs

    # Unchecked reset controls mean preserve; zero is an explicit numeric edit.
    flow, _ = _reconfigure_task(original)
    flow._temp_data = {"advanced_options": True}
    result = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
        flow, {"clear_temperature": False, "clear_top_p": False}
    )
    assert result["data"]["temperature"] == 0.2
    assert result["data"]["top_p"] == 0.8
    result = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
        flow,
        {
            "clear_temperature": False,
            "clear_top_p": False,
            "temperature": 0.0,
            "top_p": 0.0,
        },
    )
    assert result["data"]["temperature"] == 0.0
    assert result["data"]["top_p"] == 0.0


async def test_native_ai_task_failed_clear_is_reversible(hass):
    original = {
        "chat_model": "reasoning-deployment",
        "azure_model": "o3-pro",
        "api_mode": "auto",
        "max_tokens": 500,
    }
    flow, _ = _reconfigure_task(original)
    presented = []
    flow.add_suggested_values_to_schema = (
        lambda schema, values: presented.append(dict(values)) or schema
    )
    with patch(
        "custom_components.extended_openai_conversation_responses.request."
        "build_provider_request_snapshot",
        side_effect=HomeAssistantError("simulated invalid request"),
    ):
        failed = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
            flow, {"chat_model": "gpt-4o", "clear_azure_model": True}
        )
    assert failed["errors"]["base"] == "invalid_request"
    assert flow.options == original
    assert presented[-1]["azure_model"] == "o3-pro"
    assert presented[-1]["clear_azure_model"] is True

    # A subsequent unchecked control must recover the original saved binding.
    accepted = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_init(
        flow, {"chat_model": "gpt-4o", "clear_azure_model": False}
    )
    assert accepted["data"]["azure_model"] == "o3-pro"


async def test_native_ai_task_sampling_reset_retry_keeps_visible_previous_value(hass):
    original = {
        "chat_model": "gpt-4o",
        "api_mode": "auto",
        "temperature": 0.2,
        "top_p": 0.8,
        "max_tokens": 500,
    }
    flow, _ = _reconfigure_task(original)
    flow._temp_data = {"advanced_options": True}
    presented = []
    flow.add_suggested_values_to_schema = (
        lambda schema, values: presented.append(dict(values)) or schema
    )
    with patch(
        "custom_components.extended_openai_conversation_responses.request."
        "build_provider_request_snapshot",
        side_effect=HomeAssistantError("simulated invalid request"),
    ):
        failed = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
            flow, {"clear_temperature": True}
        )
    assert failed["errors"]["base"] == "invalid_request"
    assert flow.options == original
    assert presented[-1]["temperature"] == 0.2
    assert presented[-1]["clear_temperature"] is True

    accepted = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(
        flow, {"clear_temperature": False}
    )
    assert accepted["data"]["temperature"] == 0.2
    assert accepted["data"]["top_p"] == 0.8
    assert not any(key.startswith("clear_") for key in accepted["data"])
