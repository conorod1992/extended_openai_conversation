"""Implicit reasoning receives a real summary budget without invented effort support."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
    model_capabilities,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    model_metadata,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
    context_summary_request_settings,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    validate_rule_model_request,
    validate_rule,
)
from tests.test_request_rule_routing_semantics import _rule
from tests.test_summary_request_settings import _entity, _response
from tests.test_context_management import _normal_history


@pytest.mark.parametrize("provider", ["openai", "azure"])
@pytest.mark.parametrize("identity", ["o3-pro", "o3-pro-2025-06-10"])
def test_o3_pro_reasoning_token_budget_and_provider_effort(hass, provider, identity):
    azure = provider == "azure"
    model = "my-o3-deployment" if azure else identity
    options = {
        "chat_model": model,
        "api_mode": "responses",
        "max_tokens": 8192,
        "reasoning_effort": "high",
        **({"azure_model": identity} if azure else {}),
    }
    snapshot = agent_config_snapshot(options)
    metadata = model_metadata(identity, provider=provider)
    assert metadata["uses_reasoning_tokens"] is True
    assert metadata["api"]["responses"] is True
    assert metadata["api"]["chat_completions"] is False
    assert metadata["streaming"] is False
    choices = model_capabilities(identity, provider=provider)[
        "reasoning_effort_options"
    ]
    assert choices == (["low", "medium", "high"] if azure else [])
    sent = build_provider_request_snapshot(
        snapshot, {"api_provider": provider}, tools_required=False
    ).api_kwargs
    if azure:
        assert sent["reasoning"] == {"effort": "high"}
        assert snapshot["reasoning_effort"] == "high"
        route = validate_rule(
            _rule(
                "o3",
                "o3",
                action={
                    "model": model,
                    "reasoning_effort": "high",
                    "scope": "conversation",
                },
            )
        )
        validate_rule_model_request(route, snapshot, {"api_provider": "azure"})
    else:
        assert "reasoning" not in sent
    for retry, limit in [(False, 8192), (True, 16384)]:
        settings = context_summary_request_settings(
            model, "responses", snapshot, {"api_provider": provider}, retry=retry
        )
        assert settings["max_output_tokens"] == limit
        assert ("reasoning" in settings) is azure


@pytest.mark.parametrize("provider", ["openai", "azure"])
@pytest.mark.parametrize("recovers", [False, True])
async def test_actual_summary_handoff_uses_reasoning_budget(provider, recovers):
    options = {
        "chat_model": "o3-pro",
        "api_mode": "responses",
        **(
            {"azure_model": "o3-pro", "reasoning_effort": "high"}
            if provider == "azure"
            else {}
        ),
    }
    entity, create = _entity(
        "responses", _response("responses"), options, {"api_provider": provider}
    )
    create.side_effect = [
        _response("responses", exhausted=True),
        _response("responses", exhausted=not recovers),
    ]
    log = SimpleNamespace(content=_normal_history())
    await entity._truncate_message_history(
        log,
        observed_input_tokens=1000,
        model="o3-pro",
        api_mode="responses",
        defer_summary=False,
    )
    assert [call.kwargs["max_output_tokens"] for call in create.await_args_list] == [
        8192,
        16384,
    ]
    assert (
        any("Conversation summary:" in (item.content or "") for item in log.content)
        is recovers
    )
    assert entity._usage.async_record_request.await_count == 2


@pytest.mark.parametrize("provider", ["openai", "azure"])
@pytest.mark.parametrize("saved", [None, "high"])
async def test_ai_task_advanced_form_uses_the_effective_provider(provider, saved):
    from custom_components.extended_openai_conversation_responses.config_flow import (
        ExtendedOpenAIAITaskSubentryFlowHandler,
    )

    options = {
        "chat_model": "o3-pro",
        "api_mode": "responses",
        **({"azure_model": "o3-pro"} if provider == "azure" else {}),
        **({"reasoning_effort": saved} if saved else {}),
    }
    suggested = {}

    def capture_suggestions(schema, values):
        suggested.update(values)
        return schema

    flow = SimpleNamespace(
        options=options,
        _temp_data=None,
        _is_new=False,
        _get_entry=lambda: SimpleNamespace(data={"api_provider": provider}),
        async_show_form=lambda **kwargs: kwargs,
        add_suggested_values_to_schema=capture_suggestions,
    )
    shown = await ExtendedOpenAIAITaskSubentryFlowHandler.async_step_advanced(flow)
    schema = shown["data_schema"]
    if provider == "azure":
        selector = next(
            value
            for key, value in schema.schema.items()
            if str(key) == "reasoning_effort"
        )
        assert [item["value"] for item in selector.config["options"]] == [
            "low",
            "medium",
            "high",
        ]
        assert suggested["reasoning_effort"] == (saved or "low")
    else:
        assert "reasoning_effort" not in {str(key) for key in schema.schema}
