"""Regression coverage for budgeting and conversation accounting boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.extended_openai_conversation_responses import (
    conversation as pipeline,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONTEXT_TRUNCATE_KEEP_RECENT,
    CONTEXT_TRUNCATE_SUMMARIZE,
)
from custom_components.extended_openai_conversation_responses.context import (
    history_as_summary_text,
    partition_history,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_responses_param,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_context_management import _normal_history
from tests.test_conversation_orchestration import _pipeline_fixture, _rule_runtime
from tests.test_conversation_provider_loop_contracts import (
    _final_stream,
    _function_outputs,
    _provider_fixture,
    _result,
    _tool,
    _tool_stream,
)
from tests.test_responses_api import _function_call
from tests.test_usage import _manager


def test_summary_excludes_encrypted_reasoning_but_replay_preserves_it():
    payload = {
        "type": "reasoning",
        "id": "rs_1",
        "encrypted_content": "opaque-secret",
        "summary": [],
    }
    native = SimpleNamespace(type="reasoning", model_dump=lambda **_: dict(payload))
    item = conversation.AssistantContent(agent_id="agent", native=native)
    assert "opaque-secret" not in history_as_summary_text([item])
    assert (
        _convert_content_to_responses_param([item])[0]["encrypted_content"]
        == "opaque-secret"
    )
    assert payload["encrypted_content"] == "opaque-secret"


async def test_recovered_summary_failure_records_cost_without_failing_run():
    usage = await _manager()
    entity = object.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("offline"))
            )
        ),
        data={},
    )
    entity.subentry = SimpleNamespace(
        data={
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_SUMMARIZE,
            CONF_CONTEXT_THRESHOLD: 100,
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
        }
    )
    entity._usage = usage
    log = SimpleNamespace(content=_normal_history())
    async with usage.async_run() as run:
        await entity._truncate_message_history(
            log,
            observed_input_tokens=1000,
            model="gpt-5.6-luna",
            api_mode=API_MODE_RESPONSES,
        )
        await usage.async_record_request(successful=True, request_stage="initial")
    assert run.successful is True
    assert run.request_count == 2
    assert usage.totals.conversation_count == 1
    assert len(partition_history(log.content).turns) == 1


async def test_rule_failure_is_accounted_before_provider_dispatch(monkeypatch):
    entity, _input, _log, _policy, process = _pipeline_fixture(monkeypatch)
    usage = entity._usage = await _manager()
    entity._request_rules = object()
    entity._request_rule_runtime = _rule_runtime()
    observed = []

    async def reject(*_args, **_kwargs):
        observed.append(usage.current_run())
        await asyncio.sleep(0.02)
        raise HomeAssistantError("invalid rule")

    monkeypatch.setattr(pipeline, "async_evaluate_rule", reject)
    result = await process()
    assert "cannot be used" in result.response.speech["plain"]["speech"]
    assert len(observed) == 1 and observed[0] is not None
    assert observed[0].successful is False
    assert observed[0].duration_ms >= 15
    assert usage.totals.conversation_count == 1
    entity._async_handle_message_with_ha_tools.assert_not_awaited()
    entity._continuity.async_record_success.assert_not_awaited()


async def test_large_tool_result_rebudgets_before_followup(hass, monkeypatch):
    call = _function_call("lookup", "{}", "call-1")
    entity, client, continuity, _logs, invoke = _provider_fixture(
        hass,
        monkeypatch,
        [_tool_stream(call), _final_stream("done")],
        tools=[_tool("lookup")],
        options={
            CONF_CONTEXT_THRESHOLD: 3000,
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_KEEP_RECENT,
        },
    )
    continuity.async_resolve.return_value.history = [
        conversation.SystemContent(content="system"),
        conversation.UserContent(content="old-marker " * 500),
        conversation.AssistantContent(agent_id="agent", content="old answer"),
    ]

    async def execute(_tool, tool_input, _context, _entities):
        return _result(entity, tool_input, "large result " * 800)

    entity._execute_function_tool.side_effect = execute
    result = await invoke()
    assert result.response.speech["plain"]["speech"] == "done"
    initial, followup = client.responses.create.await_args_list
    assert "old-marker" in str(initial.kwargs["input"])
    assert "old-marker" not in str(followup.kwargs["input"])
    assert [item["call_id"] for item in _function_outputs(followup)] == ["call-1"]
    assert any(
        item.get("type") == "function_call" and item["call_id"] == "call-1"
        for item in followup.kwargs["input"]
    )
