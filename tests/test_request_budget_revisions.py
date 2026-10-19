"""Provider measurements apply only to the history revision they describe."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.components import conversation
from tests.test_responses_api import FakeStream, _event, _completed_event, _function_call
from tests.test_request_budget_preparation import _entity


async def test_final_maintenance_does_not_reapply_earlier_round_usage(hass):
    first = _completed_event()
    first.response.usage.input_tokens = 5000
    second = _completed_event()
    second.response.usage.input_tokens = 200
    create = AsyncMock(side_effect=[
        FakeStream([_event("response.output_item.added", item=_function_call("lookup", "{}")),
            _event("response.output_item.done", item=_function_call("lookup", "{}")), first]),
        FakeStream([_event("response.output_item.added", item=SimpleNamespace(type="message")),
            _event("response.output_text.delta", delta="Done"), second]),
    ])
    entity = _entity(hass, "responses", create, 1000)
    entity._execute_function_tool = AsyncMock(return_value=conversation.ToolResultContent(
        agent_id=entity.entity_id, tool_call_id="call_1", tool_name="lookup", tool_result={"result": "x" * 1700}))
    log = conversation.ChatLog(hass, "budget-revision")
    log.content[0] = conversation.SystemContent(content="System")
    for text in ["old " + "x" * 1500, "middle " + "y" * 100]:
        log.async_add_user_content(conversation.UserContent(content=text))
        log.async_add_assistant_content_without_tools(conversation.AssistantContent(agent_id=entity.entity_id, content="Answer"))
    log.async_add_user_content(conversation.UserContent(content="current " + "z" * 100))
    tools = [{"spec": {"name": "lookup", "description": "Look up", "parameters": {"type": "object", "properties": {}}},
        "function": {"type": "native", "name": "unused-in-test"}}]
    await entity._async_handle_chat_log(log, tools, [])
    assert create.await_count == 2
    sent = str(create.await_args_list[1].kwargs["input"])
    assert "old " not in sent and "middle " in sent
    assert any(getattr(item, "content", "").startswith("middle ") for item in log.content if isinstance(item, conversation.UserContent))
