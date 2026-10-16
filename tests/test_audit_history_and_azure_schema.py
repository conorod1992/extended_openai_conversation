"""Public task schemas and retained Responses groups survive provider changes."""

from copy import deepcopy
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.ai_task import (
    ExtendedOpenAITaskEntity,
    parse_ai_task_structured_response,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _adjust_schema,
    _convert_content_to_param,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
)
from custom_components.extended_openai_conversation_responses.non_streaming import (
    completed_responses_events,
)
from homeassistant.components import ai_task, conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm


def call(name):
    return SimpleNamespace(
        type="function_call", id=f"fc_{name}", call_id=name, name="test", arguments="{}"
    )


def message(text):
    return SimpleNamespace(
        type="message",
        id=f"msg_{text}",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text=text, annotations=[])],
    )


@pytest.mark.parametrize(
    "output",
    [
        [call("A"), call("B")],
        [call("A"), message("Checking."), call("B")],
        [call("A"), message("Checking.")],
        [
            message("First."),
            call("A"),
            message("Second."),
            call("B"),
            message("Third."),
        ],
        [
            call("A"),
            SimpleNamespace(type="reasoning", id="reasoning", summary=[]),
            message("Checking."),
            call("B"),
        ],
    ],
)
@pytest.mark.parametrize("shorten", [False, True])
async def test_responses_output_group_converts_to_one_chat_call_batch(
    hass, output, shorten
):
    """Use HA's real consumer, then verify protocol order and text preservation."""
    log = conversation.ChatLog(hass, "history", content=[])
    response = SimpleNamespace(output=output, status="completed", usage=None)
    stream = ExtendedOpenAIBaseLLMEntity._transform_responses_stream_events(
        SimpleNamespace(), log, completed_responses_events(response)
    )
    async for _ in log.async_add_delta_content_stream("agent", stream):
        pass
    calls = [item for item in output if item.type == "function_call"]
    for item in calls:
        log.content.append(
            make_tool_result_content(
                agent_id="agent",
                tool_call_id=item.call_id,
                tool_name="test",
                tool_result={"ok": True},
            )
        )
    # A completed exchange is a hard boundary even without an intervening user.
    log.content.extend(
        [
            conversation.AssistantContent(
                agent_id="agent",
                tool_calls=[
                    llm.ToolInput(id="C", tool_name="test", tool_args={}, external=True)
                ],
            ),
            make_tool_result_content(
                agent_id="agent",
                tool_call_id="C",
                tool_name="test",
                tool_result={"ok": True},
            ),
            conversation.UserContent(content="Next request"),
            conversation.AssistantContent(agent_id="agent", content="Next answer"),
        ]
    )
    converted = _convert_content_to_param(log.content, shorten)
    assert len(converted[0]["tool_calls"]) == len(calls)
    texts = [item.content[0].text for item in output if item.type == "message"]
    assert converted[0].get("content", "") == "\n".join(texts)
    assert [item["role"] for item in converted] == [
        "assistant",
        *(["tool"] * len(calls)),
        "assistant",
        "tool",
        "user",
        "assistant",
    ]
    assert [item["id"] for item in converted[0]["tool_calls"]] == [
        item["tool_call_id"] for item in converted[1 : 1 + len(calls)]
    ]
    assert len(converted[1 + len(calls)]["tool_calls"]) == 1


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("provider", ["azure", "openai"])
async def test_public_bounded_number_task_uses_provider_schema_and_caller_validation(
    hass, mode, provider
):
    """Capture the actual task request, then validate against the original selector."""

    class CapturedRequest(Exception):
        pass

    create = AsyncMock(side_effect=CapturedRequest)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        responses=SimpleNamespace(create=create),
    )
    entry = SimpleNamespace(
        entry_id="entry", data={"api_provider": provider}, runtime_data=client
    )
    subentry = SimpleNamespace(
        subentry_id="task",
        title="Task",
        data={
            "chat_model": "deployment" if provider == "azure" else "gpt-4.1",
            "api_mode": mode,
            **({"azure_model": "gpt-4.1"} if provider == "azure" else {}),
        },
    )
    entity = ExtendedOpenAITaskEntity(entry, subentry)
    entity.hass = hass
    entity.entity_id = "ai_task.audit"
    fields = {
        "score": {"required": True, "selector": {"number": {"min": 0, "max": 10}}}
    }
    services = (
        ai_task
        if hasattr(ai_task, "STRUCTURE_FIELD_SCHEMA")
        else importlib.import_module("homeassistant.components.ai_task.services")
    )
    fields = {
        key: services.STRUCTURE_FIELD_SCHEMA(value) for key, value in fields.items()
    }
    structure = services._validate_structure_fields(fields)
    log = conversation.ChatLog(
        hass,
        "task",
        content=[
            conversation.SystemContent(content="Return a score"),
            conversation.UserContent(content="Choose five"),
        ],
    )
    with pytest.raises(CapturedRequest):
        await entity._async_generate_data(
            SimpleNamespace(name="score", structure=structure), log
        )
    request = create.call_args.kwargs
    output = (
        request["text"]["format"]
        if mode == "responses"
        else request["response_format"]["json_schema"]
    )
    assert output["strict"] is True
    expected = {"type": "number"}
    if provider != "azure":
        expected.update(minimum=0.0, maximum=10.0)
    assert output["schema"]["properties"]["score"] == expected
    assert parse_ai_task_structured_response('{"score": 5}', structure) == {"score": 5}
    with pytest.raises(HomeAssistantError, match="requested structure"):
        parse_ai_task_structured_response('{"score": 11}', structure)


def test_azure_constraints_are_removed_recursively_without_mutating_caller():
    caller = {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "anyOf": [
                        {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 10,
                            "multipleOf": 2,
                        },
                        {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 10,
                            "pattern": "x",
                        },
                    ]
                },
            }
        },
        "$defs": {"number": {"type": "number", "minimum": 1}},
    }
    original = deepcopy(caller)
    provider = deepcopy(caller)
    _adjust_schema(provider, root=True, azure=True)
    values = provider["properties"]["values"]
    assert "minItems" not in values and "maxItems" not in values
    assert values["items"]["anyOf"] == [{"type": "number"}, {"type": "string"}]
    assert provider["$defs"]["number"] == {"type": "number"}
    assert caller == original
