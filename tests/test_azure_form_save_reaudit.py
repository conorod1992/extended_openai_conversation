"""Selecting an Azure effort must survive the actual AI Task form save."""

from types import SimpleNamespace
import pytest
from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIAITaskSubentryFlowHandler,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)


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
