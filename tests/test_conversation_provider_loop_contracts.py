"""End-to-end conversation provider-loop termination contracts."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, nullcontext
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOL_ERROR_RECOVERY,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.helpers import llm

from tests.test_responses_api import (
    FakeStream,
    _completed_event,
    _event,
    _function_call,
)


class _UsageHarness:
    """Minimal run/request accounting surface used by the real provider loop."""

    def __init__(self) -> None:
        self._run: SimpleNamespace | None = None
        self.async_record_request = AsyncMock()
        self.async_record_conversation = AsyncMock()
        self.mark_current_run_failed = MagicMock()

    def current_run(self) -> SimpleNamespace | None:
        return self._run

    @asynccontextmanager
    async def async_run(self, **_kwargs: Any):
        self._run = SimpleNamespace(run_id="run-1", successful=True)
        try:
            yield self._run
        finally:
            self._run = None


def _final_stream(text: str) -> FakeStream:
    message_item = SimpleNamespace(type="message")
    return FakeStream(
        [
            _event("response.output_item.added", item=message_item),
            _event("response.output_text.delta", delta=text),
            _event("response.output_item.done", item=message_item),
            _completed_event(),
        ]
    )


def _tool_stream(*calls: SimpleNamespace) -> FakeStream:
    return FakeStream(
        [
            event
            for call in calls
            for event in (
                _event("response.output_item.added", item=call),
                _event("response.output_item.done", item=call),
            )
        ]
        + [_completed_event()]
    )


def _tool(
    name: str,
    *,
    operation: str = "search",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "spec": {
            "name": name,
            "description": f"Test tool {name}",
            "parameters": parameters
            or {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        "function": {"type": "knowledge", "operation": operation},
    }


def _result(
    entity: ExtendedOpenAIAgentEntity,
    tool_input: llm.ToolInput,
    value: Any,
) -> conversation.ToolResultContent:
    return conversation.ToolResultContent(
        agent_id=entity.entity_id,
        tool_call_id=tool_input.id,
        tool_name=tool_input.tool_name,
        tool_result={"result": value},
    )


def _function_outputs(request: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in request.kwargs["input"]
        if item.get("type") == "function_call_output"
    ]


def _provider_fixture(
    hass,
    monkeypatch,
    streams: list[Any],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    usage: _UsageHarness | None = None,
    archive: Any = None,
    archive_session: Any = None,
):
    """Keep the public agent/process/provider loop real and stub unrelated systems."""
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=streams))
    )
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity.entry = SimpleNamespace(
        entry_id="entry",
        data={},
        runtime_data=client,
        async_start_reauth=MagicMock(),
    )
    entity.subentry = SimpleNamespace(
        subentry_id="agent",
        data={
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
            **(options or {}),
        },
    )
    entity._attr_entity_id = "conversation.agent"
    continuity = SimpleNamespace(
        async_resolve=AsyncMock(
            return_value=SimpleNamespace(
                conversation_id="conversation-1",
                key="device:kitchen",
                claim_token="claim-token",
                history=(),
            )
        ),
        async_release=AsyncMock(),
        async_record_success=AsyncMock(),
    )
    entity._continuity = continuity
    entity._function_groups_runtime = None
    entity._request_rules = None
    entity._request_rule_runtime = None
    entity._usage = usage
    entity._archive = archive
    entity._memory = None
    entity._temporary_memory = None
    entity._knowledge = None
    entity._guest_mode = None
    entity._async_begin_archive_session = AsyncMock(return_value=archive_session)
    entity._resolve_live_guest_policy = MagicMock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    entity._configured_function_tools_from_data = MagicMock(return_value=[])
    entity._get_exposed_entities = MagicMock(return_value=[])
    entity._get_function_tools = MagicMock(return_value=list(tools or []))
    entity._async_retrieve_memories = AsyncMock(return_value=[])
    entity._async_retrieve_temporary_memories = AsyncMock(return_value=[])
    entity._build_system_prompt = MagicMock(return_value="system")
    entity._execute_function_tool = AsyncMock()

    logs: list[conversation.ChatLog] = []

    monkeypatch.setattr(
        conversation_module,
        "resolve_data_scope",
        lambda *_args, **_kwargs: user_scope(
            "alice", source="test", device_id="kitchen"
        ),
    )
    monkeypatch.setattr(
        conversation_module,
        "async_try_handle_local_intent",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        conversation_module,
        "async_get_chat_session",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace()),
    )

    def new_log(_hass, _session, user_input):
        chat_log = conversation.ChatLog(hass, user_input.conversation_id)
        chat_log.content[0] = conversation.SystemContent(content="system")
        chat_log.async_add_user_content(
            conversation.UserContent(content=user_input.text)
        )
        logs.append(chat_log)
        return nullcontext(chat_log)

    monkeypatch.setattr(conversation_module, "async_get_chat_log", new_log)

    request = SimpleNamespace(
        text="hello",
        language="en",
        conversation_id=None,
        context=Context(user_id="alice"),
        device_id="kitchen",
        satellite_id=None,
    )
    request.as_llm_context = lambda _domain: SimpleNamespace(
        context=request.context,
        device_id=request.device_id,
    )

    async def invoke():
        return await entity.async_process(request)

    return entity, client, continuity, logs, invoke


async def test_conversation_final_text_without_tools(hass, monkeypatch) -> None:
    entity, client, continuity, logs, invoke = _provider_fixture(
        hass, monkeypatch, [_final_stream("Exact final answer.")]
    )

    result = await invoke()

    assert result.response.speech["plain"]["speech"] == "Exact final answer."
    assert result.conversation_id == "conversation-1"
    assert client.responses.create.await_count == 1
    entity._execute_function_tool.assert_not_awaited()
    continuity.async_record_success.assert_awaited_once()
    continuity.async_release.assert_awaited_once_with(
        "device:kitchen", "claim-token"
    )
    assert logs[0].content[-1].content == "Exact final answer."


async def test_conversation_single_tool_call_then_final_text(hass, monkeypatch) -> None:
    lookup = _tool(
        "lookup",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    call = _function_call("lookup", '{"query":"kitchen"}', "call-1")
    entity, client, continuity, _logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(call), _final_stream("The kitchen is ready.")],
        tools=[lookup],
    )

    async def execute(_function_tool, tool_input, _llm_context, _entities):
        return _result(entity, tool_input, "ready")

    entity._execute_function_tool.side_effect = execute

    result = await invoke()

    assert result.response.speech["plain"]["speech"] == "The kitchen is ready."
    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_awaited_once()
    dispatched = entity._execute_function_tool.await_args.args[1]
    assert dispatched.id == "call-1"
    assert dispatched.tool_name == "lookup"
    assert dispatched.tool_args == {"query": "kitchen"}
    outputs = _function_outputs(client.responses.create.await_args_list[1])
    assert [item["call_id"] for item in outputs] == ["call-1"]
    assert json.loads(outputs[0]["output"]) == {"result": "ready"}
    continuity.async_release.assert_awaited_once()


async def test_conversation_multiple_tool_calls_follow_current_ordering_contract(
    hass, monkeypatch
) -> None:
    first_tool = _tool("first", operation="search")
    second_tool = _tool("second", operation="list")
    first_call = _function_call("first", "{}", "call-1")
    second_call = _function_call("second", "{}", "call-2")
    entity, client, _continuity, _logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(first_call, second_call), _final_stream("Both complete.")],
        tools=[first_tool, second_tool],
    )
    both_started = asyncio.Event()
    started: list[str] = []
    completed: list[str] = []

    async def execute(_function_tool, tool_input, _llm_context, _entities):
        started.append(tool_input.id)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        if tool_input.id == "call-1":
            await asyncio.sleep(0.02)
        completed.append(tool_input.id)
        return _result(entity, tool_input, tool_input.id)

    entity._execute_function_tool.side_effect = execute

    result = await invoke()

    assert result.response.speech["plain"]["speech"] == "Both complete."
    assert set(started) == {"call-1", "call-2"}
    assert completed[0] == "call-2"
    outputs = _function_outputs(client.responses.create.await_args_list[1])
    assert [item["call_id"] for item in outputs] == ["call-1", "call-2"]
    assert entity._execute_function_tool.await_count == 2


async def test_conversation_partial_tool_failure(hass, monkeypatch) -> None:
    valid_tool = _tool(
        "valid_lookup",
        operation="search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    invalid_tool = _tool(
        "invalid_list",
        operation="list",
        parameters={
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": ["limit"],
            "additionalProperties": False,
        },
    )
    good_call = _function_call(
        "valid_lookup", '{"query":"kitchen"}', "call-good"
    )
    bad_call = _function_call("invalid_list", '{"limit":[]}', "call-bad")
    entity, client, _continuity, _logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(good_call, bad_call), _final_stream("Recovered.")],
        tools=[valid_tool, invalid_tool],
        options={CONF_FUNCTION_TOOL_ERROR_RECOVERY: True},
    )

    async def execute(_function_tool, tool_input, _llm_context, _entities):
        return _result(entity, tool_input, "ok")

    entity._execute_function_tool.side_effect = execute

    result = await invoke()

    assert result.response.speech["plain"]["speech"] == "Recovered."
    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_awaited_once()
    assert entity._execute_function_tool.await_args.args[1].id == "call-good"
    outputs = _function_outputs(client.responses.create.await_args_list[1])
    assert [item["call_id"] for item in outputs] == ["call-good", "call-bad"]
    assert json.loads(outputs[0]["output"]) == {"result": "ok"}
    recovery = json.loads(outputs[1]["output"])["result"]
    assert recovery["status"] == "error"
    assert recovery["reason"] == "correctable_tool_error"
    assert recovery["code"] == "invalid_arguments"
    assert recovery["stage"] == "pre_dispatch_validation"


async def test_conversation_malformed_tool_call(hass, monkeypatch) -> None:
    lookup = _tool(
        "lookup",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    malformed = _function_call("lookup", '{"query":', "call-malformed")
    entity, client, _continuity, _logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(malformed), _final_stream("Corrected without dispatch.")],
        tools=[lookup],
        options={CONF_FUNCTION_TOOL_ERROR_RECOVERY: True},
    )

    result = await invoke()

    assert result.response.speech["plain"]["speech"] == "Corrected without dispatch."
    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_not_awaited()
    followup = client.responses.create.await_args_list[1].kwargs["input"]
    replayed = next(
        item
        for item in followup
        if item.get("type") == "function_call"
        and item.get("call_id") == "call-malformed"
    )
    assert replayed["arguments"] == '{"query":'
    output = next(
        item
        for item in followup
        if item.get("type") == "function_call_output"
        and item.get("call_id") == "call-malformed"
    )
    recovery = json.loads(output["output"])["result"]
    assert recovery["reason"] == "correctable_tool_error"
    assert recovery["code"] == "invalid_json"
    assert recovery["stage"] == "provider_argument_decoding"


async def test_conversation_provider_failure_before_output(hass, monkeypatch) -> None:
    entity, client, continuity, logs, invoke = _provider_fixture(
        hass, monkeypatch, [TimeoutError("socket timed out")]
    )

    result = await invoke()

    assert client.responses.create.await_count == 1
    assert result.response.error_code is not None
    assert "Provider request timed out" in result.response.speech["plain"]["speech"]
    entity._execute_function_tool.assert_not_awaited()
    continuity.async_record_success.assert_not_awaited()
    continuity.async_release.assert_awaited_once_with(
        "device:kitchen", "claim-token"
    )
    assert not any(
        isinstance(item, conversation.AssistantContent) for item in logs[0].content
    )


async def test_conversation_missing_usable_output(hass, monkeypatch) -> None:
    entity, client, continuity, logs, invoke = _provider_fixture(
        hass, monkeypatch, [FakeStream([_completed_event()])]
    )

    result = await invoke()

    assert client.responses.create.await_count == 1
    assert result.response.error_code is None
    assert result.response.speech["plain"]["speech"] == ""
    entity._execute_function_tool.assert_not_awaited()
    continuity.async_record_success.assert_not_awaited()
    continuity.async_release.assert_awaited_once()
    assert isinstance(logs[0].content[-1], conversation.UserContent)


async def test_conversation_tool_budget_exhausted(hass, monkeypatch) -> None:
    lookup = _tool("lookup")
    first_call = _function_call("lookup", "{}", "call-1")
    over_budget_call = _function_call("lookup", "{}", "call-2")
    entity, client, continuity, _logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(first_call), _tool_stream(over_budget_call)],
        tools=[lookup],
        options={CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION: 1},
    )

    async def execute(_function_tool, tool_input, _llm_context, _entities):
        return _result(entity, tool_input, "once")

    entity._execute_function_tool.side_effect = execute

    result = await invoke()

    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_awaited_once()
    assert entity._execute_function_tool.await_args.args[1].id == "call-1"
    second_request = client.responses.create.await_args_list[1].kwargs
    assert "tools" not in second_request
    assert "tool_choice" not in second_request
    assert result.response.error_code is not None
    assert "function 'lookup' does not exist" in result.response.speech["plain"]["speech"]
    continuity.async_record_success.assert_not_awaited()
    continuity.async_release.assert_awaited_once()


async def test_conversation_followup_provider_failure(hass, monkeypatch) -> None:
    lookup = _tool("lookup")
    call = _function_call("lookup", "{}", "call-1")
    entity, client, continuity, logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(call), TimeoutError("follow-up timed out")],
        tools=[lookup],
    )

    async def execute(_function_tool, tool_input, _llm_context, _entities):
        return _result(entity, tool_input, "side-effect-complete")

    entity._execute_function_tool.side_effect = execute

    result = await invoke()

    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_awaited_once()
    assert result.response.error_code is not None
    assert "Provider request timed out" in result.response.speech["plain"]["speech"]
    tool_results = [
        item
        for item in logs[0].content
        if isinstance(item, conversation.ToolResultContent)
        and item.tool_call_id == "call-1"
    ]
    assert len(tool_results) == 1
    continuity.async_record_success.assert_not_awaited()
    continuity.async_release.assert_awaited_once()


async def test_conversation_persistence_failure_does_not_replay_provider_or_tool(
    hass, monkeypatch
) -> None:
    lookup = _tool("lookup")
    call = _function_call("lookup", "{}", "call-1")
    usage = _UsageHarness()
    archive_session = SimpleNamespace(session_id="archive-1")
    entity, client, continuity, logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(call), _final_stream("Persisting is best effort.")],
        tools=[lookup],
        usage=usage,
        archive_session=archive_session,
    )
    # Fault-inject at the exact persistence boundary reached after the final
    # provider result. The helper's own best-effort storage behavior is tested
    # separately below, keeping this orchestration contract independent of
    # archive-internal policy/storage details.
    entity._async_archive_turn = AsyncMock(return_value=None)

    async def execute(_function_tool, tool_input, _llm_context, _entities):
        return _result(entity, tool_input, "done")

    entity._execute_function_tool.side_effect = execute

    result = await invoke()

    assert result.response.error_code is None
    assert result.response.speech["plain"]["speech"] == "Persisting is best effort."
    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_awaited_once()
    entity._async_archive_turn.assert_awaited_once()
    persistence_call = entity._async_archive_turn.await_args
    assert persistence_call.args[0] is archive_session
    assert persistence_call.args[1] == "run-1"
    assert persistence_call.args[2].text == "hello"
    assert persistence_call.args[3] is logs[0]
    assert persistence_call.kwargs == {"successful": True}
    continuity.async_record_success.assert_awaited_once()
    continuity.async_release.assert_awaited_once()


async def test_archive_turn_write_failure_is_best_effort(hass) -> None:
    """Archive storage failure must not escape the agent persistence helper."""
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = hass
    entity._archive = SimpleNamespace(
        async_record_turn=AsyncMock(side_effect=RuntimeError("archive unavailable"))
    )
    entity._effective_guest_policy = MagicMock(
        return_value=GuestCapabilityPolicy.unrestricted()
    )
    archive_session = SimpleNamespace(session_id="archive-1")
    user_input = SimpleNamespace(text="hello")
    chat_log = SimpleNamespace(
        content=[
            conversation.AssistantContent(
                agent_id="conversation.agent",
                content="Persisting is best effort.",
            )
        ]
    )

    await entity._async_archive_turn(
        archive_session,
        "run-1",
        user_input,
        chat_log,
        successful=True,
    )

    entity._archive.async_record_turn.assert_awaited_once_with(
        "archive-1",
        run_id="run-1",
        user_text="hello",
        assistant_text="Persisting is best effort.",
        successful=True,
    )
