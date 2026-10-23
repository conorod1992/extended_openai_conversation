"""HA-owned tool diagnostics obey the same public boundary as EOAI backends."""

import json
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    caller_api_tools,
    tool_snapshot_scope,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.public_errors import (
    SanitizedUnexpectedBackendError,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.template import Template

from .test_ha_llm_tools import Echo, TestAPI, context, result_type  # noqa: F401
from .test_model_tool_result_boundary import wire_result  # noqa: F401


@pytest.mark.parametrize(
    "failure",
    ["template", "raised", "dependency", "flagged", "typed_flagged", "success"],
)
@pytest.mark.parametrize("strict", [False, True])
async def test_ha_private_failure_on_both_wires(
    hass,
    result_type,
    wire_result,
    failure,
    strict,
):
    secret = "FABRICATED-private-backend-password-42"
    source = '{"password":"' + secret + '", "value": {{ bad | int }}}'
    data = {"message": secret, "nested": [{"opaque": secret}]}
    if failure == "typed_flagged":
        data["unsupported"] = {b"private"}

    class FailingTool(Echo):
        async def async_call(self, hass, tool_input, llm_context):
            self.calls.append(tool_input)
            if failure == "template":
                return Template(source, hass).async_render(
                    {"bad": "invalid"}, parse_result=False
                )
            if failure == "raised":
                raise HomeAssistantError("Remote backend password=" + secret)
            if failure == "dependency":
                raise ValueError("Remote backend password=" + secret)
            return result_type(data=data, error=failure != "success")

    tool = FailingTool()
    ctx = context()
    snapshot, tools = caller_api_tools(
        await TestAPI(hass, tools=[tool]).async_get_api_instance(ctx)
    )
    entity = object.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.hass = hass
    entity.entity_id = "conversation.privacy"
    hass.auth.async_get_user.return_value = SimpleNamespace(is_active=True)
    call = llm.ToolInput(
        id="privacy",
        tool_name=tools[0]["spec"]["name"],
        tool_args={"value": "hello"},
        external=True,
    )
    with tool_snapshot_scope(snapshot):
        if (strict and failure != "success") or failure == "dependency":
            log = conversation.ChatLog(hass, "privacy")
            log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id="agent", tool_calls=[call])
            )
            with pytest.raises(
                SanitizedUnexpectedBackendError
                if failure == "dependency"
                else HomeAssistantError
            ):
                await async_execute_tool_exchange(
                    entity,
                    log,
                    [call],
                    tools,
                    FunctionCallBudget(2),
                    ctx,
                    [],
                    recovery_state=ToolRecoveryState(enabled=True),
                )
            result = log.content[-1]
        else:
            result = await entity._execute_function_tool(tools[0], call, ctx, [])
    retained = tool_result_data(result)
    wire = json.dumps(wire_result(result))
    if failure == "success":
        assert secret in wire  # Ordinary business data is not rewritten.
    else:
        assert secret not in wire
        assert "{{" not in wire
        assert "Check the tool" in wire
        assert wire_result(result)["result"]["status"] == "error"
        if failure == "flagged":
            assert retained["result"]["data"] == data
    assert len(tool.calls) == 1
