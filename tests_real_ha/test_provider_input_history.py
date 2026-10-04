"""Retained native output and owned prompt slots across genuine HA requests."""

import base64
from contextlib import asynccontextmanager
import json

import httpx
from openai import AsyncOpenAI
from openai.types.responses import ResponseFunctionWebSearch, ResponseReasoningItem
import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    async_discover,
    new_reference_tool,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import llm
from homeassistant.helpers.chat_session import async_get_chat_session
from tests_real_ha.strict_provider import StrictProvider
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _responses_sse_text,
    _responses_sse_tool_call,
)


@asynccontextmanager
async def _transport(entry, replies):
    """Use the SDK's supported HTTP transport injection, without replacing methods."""
    wire = StrictProvider(replies)
    original = entry.runtime_data
    async with AsyncOpenAI(
        api_key="sk-test-history",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(wire.send)),
    ) as client:
        entry.runtime_data = client
        try:
            yield wire
        finally:
            entry.runtime_data = original


async def test_native_only_history_has_valid_chat_follow_up_and_intact_call_pairs(hass):
    agent = await _contract_agent(hass)
    reasoning = ResponseReasoningItem(id="rs-history", type="reasoning", summary=[])
    search = ResponseFunctionWebSearch.model_validate(
        {
            "id": "ws-history",
            "type": "web_search_call",
            "status": "completed",
            "action": {"type": "search", "query": "history"},
        }
    )
    call = llm.ToolInput(
        id="retained-call", tool_name="previous_action", tool_args={}, external=True
    )

    async def deltas():
        yield {"role": "assistant", "native": reasoning}
        yield {"role": "assistant", "native": search}
        yield {"role": "assistant", "content": "Prior textual answer"}
        yield {"role": "assistant", "tool_calls": [call]}

    with async_get_chat_session(hass) as session:
        conversation_id = session.conversation_id
        with conversation.async_get_chat_log(hass, session) as log:
            log.async_add_user_content(
                conversation.UserContent(content="Earlier question")
            )
            retained = [
                item
                async for item in log.async_add_delta_content_stream(
                    agent.entity_id, deltas()
                )
            ]
            log.async_add_assistant_content_without_tools(
                make_tool_result_content(
                    agent_id=agent.entity_id,
                    tool_call_id=call.id,
                    tool_name=call.tool_name,
                    tool_result={"success": True},
                )
            )
    native_records = [item for item in retained if item.native is not None]
    assert len(native_records) == 2
    assert all(not item.content and not item.tool_calls for item in native_records)
    async with _transport(agent.entry, [_chat_sse_text("Follow-up answer")]) as wire:
        result = await _say(hass, agent, "Current follow-up", conversation_id)
        assert _speech(result) == "Follow-up answer"
        wire.assert_complete(1)
        messages = wire.requests[0]["body"]["messages"]
        assert {"role": "assistant"} not in messages
        assert any(item.get("content") == "Prior textual answer" for item in messages)
        assert any(item.get("content") == "Current follow-up" for item in messages)
        assert (
            next(item for item in messages if item.get("tool_calls"))["tool_calls"][0][
                "id"
            ]
            == call.id
        )
        assert (
            next(item for item in messages if item.get("role") == "tool")[
                "tool_call_id"
            ]
            == call.id
        )
    # Chat serialization cannot delete native items needed by a later Responses turn.
    assert [
        item["type"] for item in _convert_content_to_responses_param(native_records)
    ] == ["reasoning", "web_search_call"]


class HistoryTool(llm.Tool):
    name = "history_echo"
    integration = DOMAIN
    description = "Echo a value"
    parameters = vol.Schema({vol.Required("value"): str})

    def __init__(self):
        self.calls = []

    async def async_call(self, hass, tool_input, llm_context):
        self.calls.append(tool_input)
        return {"echo": tool_input.tool_args["value"]}


class HistoryAPI(llm.API):
    def __init__(self, hass, tool):
        super().__init__(hass=hass, id="history-api", name="History API")
        self.tool = tool

    async def async_get_api_instance(self, llm_context):
        return llm.APIInstance(
            api=self,
            api_prompt="HA source tool instructions",
            llm_context=llm_context,
            tools=[self.tool],
        )


@pytest.mark.parametrize("prompt", ["", '{{ "" }}', "Nonempty base prompt"])
@pytest.mark.parametrize("retained_context", [False, True])
async def test_primary_prompt_transitions_preserve_users_summaries_and_attachments(
    hass, tmp_path, prompt, retained_context
):
    tool = HistoryTool()
    llm.async_register_api(hass, HistoryAPI(hass, tool))
    context = llm.LLMContext(
        platform=DOMAIN,
        context=Context(),
        language="en",
        assistant="conversation",
        device_id=None,
    )
    snapshot = await async_discover(hass, context)
    saved = new_reference_tool(
        next(
            live.reference
            for live in snapshot.tools.values()
            if live.tool.name == tool.name
        ),
        set(),
    )
    agent = await _contract_agent(
        hass,
        api_mode="responses",
        prompt=prompt,
        functions=[saved],
        max_function_calls_per_conversation=1,
        memory_enabled=False,
        temporary_memory="off",
        archive_enabled=False,
        current_datetime_enabled=False,
        exposed_entities_enabled=False,
    )
    conversation_id = None
    if retained_context:
        path = tmp_path / "history.png"
        path.write_bytes(b"Retained image bytes")
        attachment = conversation.Attachment(
            media_content_id="local-history", mime_type="image/png", path=path
        )
        with async_get_chat_session(hass) as session:
            conversation_id = session.conversation_id
            with conversation.async_get_chat_log(hass, session) as log:
                log.content.append(
                    conversation.SystemContent(content="Retained summary context")
                )
                log.async_add_user_content(
                    conversation.UserContent(
                        content="Earlier image question", attachments=[attachment]
                    )
                )
                log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id=agent.entity_id, content="Earlier image answer"
                    )
                )
    replies = [
        _responses_sse_tool_call(
            "history-call", saved["spec"]["name"], {"value": "once"}
        ),
        _responses_sse_text("Completed answer"),
    ]
    async with _transport(agent.entry, replies) as wire:
        result = await _say(hass, agent, "Current user command", conversation_id)
        assert _speech(result) == "Completed answer"
        wire.assert_complete(2)
        first, second = [request["body"]["input"] for request in wire.requests]
        first_users = [item for item in first if item.get("role") == "user"]
        assert first_users == [item for item in second if item.get("role") == "user"]
        assert first_users[-1]["content"] == "Current user command"
        assert "HA source tool instructions" in first[0]["content"]
        assert not any(
            "HA source tool instructions" in str(item.get("content", ""))
            for item in second
        )
        if prompt == "Nonempty base prompt":
            assert second[0]["content"] == prompt
        if retained_context:
            for items in (first, second):
                assert any(
                    item.get("content") == "Retained summary context" for item in items
                )
                assert base64.b64encode(b"Retained image bytes").decode() in json.dumps(
                    items
                )
    assert len(tool.calls) == 1
