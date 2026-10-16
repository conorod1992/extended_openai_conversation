"""Every provider continuation bounds tool output without repeating execution."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import orjson

from custom_components.extended_openai_conversation_responses.ai_task import (
    ExtendedOpenAITaskEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    caller_api_tools,
    tool_snapshot_scope,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    ExecutionFailure,
    HAToolResultError,
    make_tool_result_content,
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.model_tool_results import (
    OwnedJSONPayload,
    local_function_results,
)
from custom_components.extended_openai_conversation_responses.runtime_hardening import (
    MAX_MODEL_TOOL_RESULT_CHARACTERS,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    append_unresolved_tool_results,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import llm

from .test_ha_llm_tools import Echo, TestAPI


@pytest.fixture(params=["chat_completions", "responses"])
def wire_result(request):
    def serialize(content):
        if request.param == "responses":
            item = _convert_content_to_responses_param([content])[0]
            assert item["call_id"] == content.tool_call_id
            text = item["output"]
        else:
            item = _convert_content_to_param([content])[0]
            assert item["tool_call_id"] == content.tool_call_id
            text = item["content"]
        payload = json.loads(text)
        value = payload.get("result", payload)
        size = (
            len(value) if isinstance(value, str) else len(orjson.dumps(value).decode())
        )
        assert size <= MAX_MODEL_TOOL_RESULT_CHARACTERS
        return payload

    return serialize


@pytest.mark.parametrize("size", [100, 64000])
async def test_inherited_ai_task_ha_tool_results_are_bounded_at_provider_boundary(
    hass, wire_result, size
):
    payload = {"report": "é" * size}

    class ReportTool(Echo):
        async def async_call(self, hass, tool_input, llm_context):
            self.calls.append(tool_input)
            result_type = getattr(llm, "ToolResult", None)
            return result_type(data=payload) if result_type is not None else payload

    tool = ReportTool()
    context = llm.LLMContext(
        platform="ai_task",
        context=Context(user_id="caller"),
        language=None,
        assistant="ai_task",
        device_id=None,
    )
    snapshot, tools = caller_api_tools(
        await TestAPI(hass, tools=[tool]).async_get_api_instance(context)
    )
    entity = object.__new__(ExtendedOpenAITaskEntity)
    entity.hass = hass
    entity.entity_id = "ai_task.report"
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=True))
    call = llm.ToolInput(
        id="report",
        tool_name=tools[0]["spec"]["name"],
        tool_args={"value": "hello"},
        external=True,
    )
    with tool_snapshot_scope(snapshot):
        content = await entity._execute_function_tool(tools[0], call, context, [])

    # Retained/local output remains lossless; even a local-results scope cannot
    # bypass the provider boundary. Repeated serialization never executes a tool.
    retained = tool_result_data(content)
    for _ in range(2):
        with local_function_results():
            output = wire_result(content)["result"]
        if size == 100:
            assert output == payload
        else:
            assert output["status"] == "output_overflow"
            assert output["execution_completed"] is True
            assert "do not repeat side effects" in output["message"]
        assert tool_result_data(content) is retained
        assert retained == {"result": payload}
    assert len(tool.calls) == 1
    assert tool.calls[0].id == call.id
    assert tool.calls[0].tool_args == call.tool_args


def test_repaired_ha_failure_is_bounded_without_losing_retained_error(
    hass, wire_result
):
    call = llm.ToolInput(id="failed", tool_name="report", tool_args={})
    log = conversation.ChatLog(hass, "failed-report")
    log.content.append(
        conversation.AssistantContent(agent_id="agent", tool_calls=[call])
    )
    data = {"report": "x" * 64000}
    append_unresolved_tool_results(log, "agent", [call], error=HAToolResultError(data))
    content = log.content[-1]
    retained = tool_result_data(content)
    output = wire_result(content)
    assert output["result"]["status"] == "output_overflow"
    assert output["result"]["error"] is True
    assert retained["result"]["status"] == "error"
    assert retained["result"]["data"] is data
    assert getattr(getattr(content, "result", None), "error", True)
    append_unresolved_tool_results(log, "agent", [call], error=HAToolResultError(data))
    assert log.content[-1] is content


@pytest.mark.parametrize(
    "payload",
    [
        {"result": "small", "metadata": "x" * 64000},
        OwnedJSONPayload({"report": "x" * 64000}),
    ],
)
def test_result_budget_covers_metadata_and_owned_envelopes(wire_result, payload):
    content = make_tool_result_content(
        agent_id="agent",
        tool_call_id="report",
        tool_name="report",
        tool_result=payload,
    )
    result = wire_result(content)["result"]
    if isinstance(result, str):
        result = json.loads(result)
    assert result["status"] == "output_overflow"
    assert tool_result_data(content) is payload


@pytest.mark.parametrize("kind", ["literal", "structured", "owned"])
@pytest.mark.parametrize("size,overflow", [(31000, True), (12000, False)])
def test_result_and_sibling_metadata_share_one_budget(
    wire_result, kind, size, overflow
):
    value = "é" * size if kind == "literal" else {"report": "é" * size}
    payload = OwnedJSONPayload(value) if kind == "owned" else {"result": value}
    payload["metadata"] = "y" * size
    before = dict(payload)
    content = make_tool_result_content(
        agent_id="agent",
        tool_call_id="aggregate",
        tool_name="report",
        tool_result=payload,
    )
    for _ in range(2):
        output = wire_result(content)
        if overflow:
            assert output["result"]["status"] == "output_overflow"
            assert output["result"]["execution_completed"] is True
            assert "metadata" not in output
        else:
            assert output == before
            actual = (
                json.loads(output["result"]) if kind == "owned" else output["result"]
            )
            assert actual == value
        assert tool_result_data(content) is payload
        assert dict(payload) == before


@pytest.mark.parametrize(
    "kind", ["native", "structured", "owned", "projected", "literal"]
)
def test_metadata_overflow_preserves_failure_indications_without_parsing_text(
    wire_result, kind
):
    value = ExecutionFailure(status="error", error="backend failed")
    if kind == "native":
        value = {"message": "native error flag owns failure semantics"}
    elif kind == "literal":
        value = json.dumps(value)
    elif kind == "projected":
        value = ExecutionFailure(status="output_overflow", error=True)
    payload = OwnedJSONPayload(value) if kind == "owned" else {"result": value}
    payload["metadata"] = "x" * 64000
    content = make_tool_result_content(
        agent_id="agent",
        tool_call_id="failure",
        tool_name="report",
        tool_result=payload,
        error=kind == "native",
    )
    output = wire_result(content)["result"]
    assert output["status"] == "output_overflow"
    assert output.get("error", False) is (kind != "literal")
    assert tool_result_data(content) == payload


def test_escaped_literal_and_metadata_fit_the_shared_content_budget(wire_result):
    value = '"\\\n' * 8000
    payload = {"result": value, "metadata": "y" * 7000}
    content = make_tool_result_content(
        agent_id="agent",
        tool_call_id="escaped",
        tool_name="report",
        tool_result=payload,
    )
    assert wire_result(content) == payload


def test_literal_escaping_keeps_the_existing_result_value_budget(wire_result):
    value = 'a"\\\n' * 10000
    content = make_tool_result_content(
        agent_id="agent",
        tool_call_id="literal",
        tool_name="report",
        tool_result={"result": value},
    )
    result = wire_result(content)["result"]
    assert isinstance(result, str)
    assert len(result) == MAX_MODEL_TOOL_RESULT_CHARACTERS
    assert result.startswith(value[:100])
    assert tool_result_data(content)["result"] == value


def test_complete_owned_page_survives_outer_json_escaping(wire_result):
    page = {"records": [{"text": '"\\\n' * 4000}], "has_more": False}
    content = make_tool_result_content(
        agent_id="agent",
        tool_call_id="page",
        tool_name="report",
        tool_result=OwnedJSONPayload(page),
    )
    assert json.loads(wire_result(content)["result"]) == page


@pytest.mark.parametrize("value", [False, None, 0, [], '{\n "answer": 42\n}'])
def test_small_literal_results_keep_their_types_and_content(wire_result, value):
    content = make_tool_result_content(
        agent_id="agent",
        tool_call_id="small",
        tool_name="report",
        tool_result={"result": value},
    )
    assert wire_result(content) == {"result": value}
