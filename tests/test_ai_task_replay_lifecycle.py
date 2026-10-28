"""AI Task acknowledgement owns receipts created by the shared tool executor."""

from __future__ import annotations

import asyncio
from itertools import count
from types import SimpleNamespace

from openai import OpenAIError
import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses import (
    execution_outcomes as outcomes,
    tool_replay_guard as guard,
)
from custom_components.extended_openai_conversation_responses.ai_task import (
    ExtendedOpenAITaskEntity,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    propagate_function_execution_errors,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    replay_safety,
)
from homeassistant.components import ai_task, conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import frame, llm

_SIDECARS = (
    "_dispatched_tool_inputs",
    "_dispatched_tool_signatures",
    "_dispatched_tool_conversations",
    "_read_only_tool_inputs",
    "_execution_tool_outcomes",
    "_execution_effect_receipts",
)


class _CounterTool(llm.Tool):
    name = "counter"
    integration = "extended_openai_conversation_responses"
    description = "Increment Home Assistant state once."
    parameters = vol.Schema({})

    def __init__(self):
        self.calls = []
        self.fail_after_effect = False

    async def async_call(self, hass, tool_input, llm_context):
        self.calls.append((tool_input, llm_context))
        previous = hass.states.get("sensor.ai_task_effects")
        value = int(previous.state) + 1 if previous is not None else 1
        hass.states.async_set("sensor.ai_task_effects", value)
        if self.fail_after_effect:
            raise HomeAssistantError("handler failed after increment")
        payload = {"count": value}
        result_type = getattr(llm, "ToolResult", None)
        return result_type(data=payload) if result_type is not None else payload


class _TaskDriver:
    """Replace provider traffic, retaining actual AI Task and HA tool lifecycles."""

    def __init__(self, hass):
        self.hass = hass
        self.tool = _CounterTool()
        self.context = llm.LLMContext(
            platform="ai_task",
            context=None,
            language=None,
            assistant="ai_task",
            device_id=None,
        )
        self.instance = llm.APIInstance(
            api=SimpleNamespace(hass=hass, id="task-probe", name="Task Probe"),
            api_prompt="Caller-owned prompt",
            llm_context=self.context,
            tools=[self.tool],
        )
        self.entity = SimpleNamespace(hass=hass, entity_id="ai_task.receipt_probe")
        self.entity._execute_function_tool = self._execute
        self.entity._async_handle_chat_log = self._provider
        self.mode = "success"
        self.text = "Acknowledged"
        self.dispatched = asyncio.Event()
        self.serial = count()

    async def _execute(self, *args):
        return await ExtendedOpenAIBaseLLMEntity._execute_function_tool(
            self.entity, *args
        )

    def log(self, conversation_id):
        log = conversation.ChatLog(self.hass, conversation_id)
        log.llm_api = self.instance

        async def provide(*args, **kwargs):
            return None

        # Core has already provided caller data before entering this entity method.
        log.async_provide_llm_data = provide
        return log

    async def _provider(self, log, *, function_tools, **kwargs):
        if self.mode != "no_tools":
            tool = function_tools[0]
            call = llm.ToolInput(
                tool_name=tool["spec"]["name"],
                tool_args={"invalid": True} if self.mode == "reject" else {},
                id=f"call-{next(self.serial)}",
            )
            log.content.append(
                conversation.AssistantContent(
                    agent_id=self.entity.entity_id, tool_calls=[call]
                )
            )
            await async_execute_tool_exchange(
                self.entity,
                log,
                [call],
                function_tools,
                FunctionCallBudget(10),
                self.context,
                [],
            )
            self.dispatched.set()
        if self.mode == "provider_error":
            raise OpenAIError("provider continuation was lost")
        if self.mode == "cancel":
            await asyncio.Event().wait()
        log.content.append(
            conversation.AssistantContent(
                agent_id=self.entity.entity_id, content=self.text
            )
        )

    async def run(self, log, *, structure=None):
        return await ExtendedOpenAITaskEntity._async_generate_data(
            self.entity,
            ai_task.GenDataTask(
                name="Receipt probe",
                instructions="Use the caller API",
                structure=structure,
            ),
            log,
        )


@pytest.fixture
async def task_driver(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    frame.async_setup(hass)
    driver = _TaskDriver(hass)
    try:
        yield driver
    finally:
        await hass.async_stop(force=True)
        frame.async_setup(None)


def _assert_acknowledged(entity):
    for name in _SIDECARS:
        assert not getattr(entity, name, {}), name
    for name in (
        "retired_conversations",
        "schemas",
        "effects",
        "uncertain_tools",
    ):
        assert not getattr(replay_safety(entity), name, {}), name
    assert not replay_safety(entity).history_incomplete


@pytest.mark.parametrize("reused_conversation", [False, True])
async def test_successful_ai_tasks_release_receipts_before_retention_eviction(
    task_driver, monkeypatch, reused_conversation
):
    monkeypatch.setattr(guard, "_MAX_DISPATCH_RECORDS", 2)
    monkeypatch.setattr(guard, "_MAX_RETIRED_CONVERSATIONS", 2)
    monkeypatch.setattr(outcomes, "_MAX_RETAINED_EFFECT_RECEIPTS", 2)
    shared = task_driver.log("shared-task")
    for index in range(8):
        log = shared if reused_conversation else task_driver.log(f"fresh-task-{index}")
        result = await task_driver.run(log)
        assert result.data == "Acknowledged"
        _assert_acknowledged(task_driver.entity)
    assert task_driver.hass.states.get("sensor.ai_task_effects").state == "8"
    assert all(context is task_driver.context for _, context in task_driver.tool.calls)
    assert task_driver.instance.tools == [task_driver.tool]


@pytest.mark.parametrize("failure", ["provider_error", "cancel", "tool_error"])
async def test_failed_ai_task_continuations_retain_effects_until_acknowledged(
    task_driver, failure
):
    log = task_driver.log("failed-task")
    task_driver.mode = failure
    if failure == "cancel":
        pending = asyncio.create_task(task_driver.run(log))
        await asyncio.wait_for(task_driver.dispatched.wait(), timeout=5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    elif failure == "tool_error":
        task_driver.tool.fail_after_effect = True
        with (
            propagate_function_execution_errors(),
            pytest.raises(
                HomeAssistantError, match="Check the tool configuration and permissions"
            ) as caught,
        ):
            await task_driver.run(log)
        assert "handler failed after increment" not in str(caught.value)
        task_driver.tool.fail_after_effect = False
    else:
        with pytest.raises(HomeAssistantError, match="Provider request failed"):
            await task_driver.run(log)
    entity = task_driver.entity
    assert task_driver.hass.states.get("sensor.ai_task_effects").state == "1"
    assert set(entity._dispatched_tool_conversations.values()) == {log.conversation_id}
    assert replay_safety(entity).effects[log.conversation_id]
    assert set(entity._execution_tool_outcomes.values()) == {
        "uncertain" if failure == "tool_error" else "completed"
    }

    task_driver.mode = "success"
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await task_driver.run(log)
    assert len(task_driver.tool.calls) == 1

    # A different task can succeed without settling the failed conversation.
    await task_driver.run(task_driver.log("independent-task"))
    assert task_driver.hass.states.get("sensor.ai_task_effects").state == "2"
    assert replay_safety(entity).effects[log.conversation_id]
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await task_driver.run(log)
    assert len(task_driver.tool.calls) == 2

    task_driver.mode = "no_tools"
    await task_driver.run(log)
    _assert_acknowledged(entity)
    task_driver.mode = "success"
    await task_driver.run(log)
    assert task_driver.hass.states.get("sensor.ai_task_effects").state == "3"
    _assert_acknowledged(entity)


async def test_rejected_ai_task_tools_do_not_retire_effect_history(
    task_driver, monkeypatch
):
    monkeypatch.setattr(guard, "_MAX_DISPATCH_RECORDS", 2)
    monkeypatch.setattr(guard, "_MAX_RETIRED_CONVERSATIONS", 2)
    monkeypatch.setattr(outcomes, "_MAX_RETAINED_EFFECT_RECEIPTS", 2)
    log = task_driver.log("rejected-task")
    task_driver.mode = "reject"
    for _ in range(6):
        with (
            propagate_function_execution_errors(),
            pytest.raises(HomeAssistantError, match="arguments do not match"),
        ):
            await task_driver.run(log)
    assert task_driver.tool.calls == []
    assert not replay_safety(task_driver.entity).effects
    assert not replay_safety(task_driver.entity).retired_conversations
    assert not replay_safety(task_driver.entity).history_incomplete
    task_driver.mode = "success"
    await task_driver.run(log)
    assert task_driver.hass.states.get("sensor.ai_task_effects").state == "1"
    _assert_acknowledged(task_driver.entity)


@pytest.mark.parametrize("invalid_result", ["not JSON", '{"answer":"wrong type"}'])
async def test_structured_output_failure_follows_provider_acknowledgement(
    task_driver, invalid_result
):
    log = task_driver.log("structured-task")
    task_driver.text = invalid_result
    with pytest.raises(HomeAssistantError, match=r"structured|structure"):
        await task_driver.run(log, structure=vol.Schema({vol.Required("answer"): int}))
    assert task_driver.hass.states.get("sensor.ai_task_effects").state == "1"
    _assert_acknowledged(task_driver.entity)
    task_driver.text = '{"answer":2}'
    result = await task_driver.run(
        log, structure=vol.Schema({vol.Required("answer"): int})
    )
    assert result.data == {"answer": 2}
    assert task_driver.hass.states.get("sensor.ai_task_effects").state == "2"
    _assert_acknowledged(task_driver.entity)
