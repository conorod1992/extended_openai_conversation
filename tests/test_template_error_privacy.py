"""Backend source must never become model-visible template error feedback."""

import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
    bind_tool_recovery_state,
)
from custom_components.extended_openai_conversation_responses.public_errors import (
    SanitizedTemplateError,
)
from homeassistant.helpers import llm
from homeassistant.helpers.template import Template


@pytest.mark.parametrize("backend", ["template", "payload", "resource", "header"])
@pytest.mark.parametrize("strict", [False, True])
async def test_backend_template_failure_has_no_source(hass, backend, strict):
    secret = "unfamiliar_FABRICATED_9b2d7a"
    source = '{"password":"' + secret + '", "value": {{ bad | int }}}'
    config = {"type": "template", "value_template": Template(source, hass)}
    if backend != "template":
        config = {"type": "rest", "resource": "https://example.invalid/api"}
        if backend == "resource":
            config.pop("resource")
            config["resource_template"] = Template(
                "https://example.invalid/?api_key=" + secret + "&n={{ bad | int }}",
                hass,
            )
        elif backend == "header":
            config["headers"] = {"X-Custom": Template(source, hass)}
        else:
            config["payload_template"] = Template(source, hass)
    entity = SimpleNamespace(
        hass=hass,
        entity_id="conversation.privacy",
        should_run_in_background=lambda _: False,
    )
    tool = {"spec": {"name": "probe"}, "function": config}
    call = llm.ToolInput(
        id="privacy",
        tool_name="probe",
        tool_args={"bad": "not-a-number"},
        external=True,
    )
    with bind_tool_recovery_state(ToolRecoveryState(enabled=strict)):
        if strict:
            with pytest.raises(SanitizedTemplateError) as caught:
                await ExtendedOpenAIBaseLLMEntity._execute_function_tool.__wrapped__(
                    entity, tool, call, None, []
                )
            public = str(caught.value)
        else:
            result = (
                await ExtendedOpenAIBaseLLMEntity._execute_function_tool.__wrapped__(
                    entity, tool, call, None, []
                )
            )
            public = json.dumps(
                [
                    _convert_content_to_param([result]),
                    _convert_content_to_responses_param([result]),
                ]
            )
    assert secret not in public
    assert "not-a-number" not in public
    assert "{{" not in public
    assert "Template rendering failed" in public
