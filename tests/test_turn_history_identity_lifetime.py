"""History identity boundaries retain originals through replacement and recovery."""

import asyncio
from contextlib import suppress
import gc
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import weakref

from openai import OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses import (
    ai_task as task_module,
    conversation as agent_module,
    local_intents,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.model_tool_results import (
    async_prepare_tool_results,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    record_dispatch,
)
from homeassistant.components import ai_task, conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _entity,
    _result,
    _retain_calls,
    _tool,
)


class _TrackedUser(conversation.UserContent):
    """Allow a weak reference without relying on Core's slots layout."""


class _TrackedAssistant(conversation.AssistantContent):
    """An original assistant item that preparation/pruning can replace."""


class _TrackedResult(conversation.ToolResultContent):
    """An original result replaced by wire projection."""


@pytest.mark.parametrize("path", ["conversation", "ai_task"])
@pytest.mark.parametrize("failure", ["provider", "cancel", "unexpected"])
async def test_originals_live_through_pruning_and_failure_then_retry_once(
    hass, monkeypatch, path, failure
):
    entity, log = _entity(hass), _chat_log(hass)
    log.llm_api = None
    log.content.extend(
        [
            _TrackedUser(content="original history"),
            _TrackedAssistant(
                agent_id=entity.entity_id, tool_calls=[_call("old", "effect")]
            ),
            _TrackedResult(
                agent_id=entity.entity_id,
                tool_call_id="old",
                tool_name="effect",
                tool_result={"result": "old"},
            ),
        ]
    )
    refs = [weakref.ref(item) for item in log.content[-3:]]
    if path == "ai_task":
        log.llm_api = SimpleNamespace(
            api_prompt="caller",
            tools=[],
            api=SimpleNamespace(name="Caller", id="caller"),
            llm_context=None,
        )

        async def provide(**_kwargs):
            log.content[0] = conversation.SystemContent(content="Core replacement")
            await async_prepare_tool_results(log.content)
            assert all(ref() is not None for ref in refs)

        log.async_provide_llm_data = provide
    tool, effects, serial = _tool("effect"), [], count()
    mode = failure

    async def execute(config, call, *_args):
        record_dispatch(entity, call, config)
        effects.append(call.id)
        return _result(entity, call)

    entity._execute_function_tool.side_effect = execute

    async def provider(current, **_kwargs):
        # Replace the prompt, prune the original history, then allocate new calls.
        # Weak references test the lifetime directly, never allocator reuse.
        await async_prepare_tool_results(current.content)
        current.content[:] = [conversation.SystemContent(content="replacement")]
        gc.collect()
        if len(effects) == 0:
            assert all(ref() is not None for ref in refs)
        if mode != "acknowledge":
            call = _call(f"call-{next(serial)}", "effect")
            _retain_calls(current, [call])
            await async_execute_tool_exchange(
                entity, current, [call], [tool], FunctionCallBudget(10), None, []
            )
        if mode == "provider":
            raise OpenAIError("lost continuation")
        if mode == "cancel":
            raise asyncio.CancelledError
        if mode == "unexpected":
            raise RuntimeError("lost continuation")
        current.content.append(
            conversation.AssistantContent(agent_id=entity.entity_id, content="Done")
        )
        return False

    entity._async_handle_chat_log = provider
    entity.subentry = SimpleNamespace(data={})
    entity._get_exposed_entities = MagicMock(return_value=[])
    entity._async_retrieve_memories = AsyncMock(return_value=[])
    entity._async_retrieve_temporary_memories = AsyncMock(return_value=[])
    entity._build_system_prompt = MagicMock(return_value="system")
    entity._get_function_tools = MagicMock(return_value=[tool])
    entity._fire_conversation_finished = MagicMock()
    monkeypatch.setattr(
        agent_module.ExtendedOpenAIAgentEntity,
        "_async_revalidate_retrieved_memories",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        agent_module.ExtendedOpenAIAgentEntity,
        "_revalidate_temporary_memory_expiry",
        MagicMock(return_value=[]),
    )
    module = agent_module if path == "conversation" else task_module
    remember = module.remember_unacknowledged_calls
    recoveries = []

    def checked_remember(*args):
        assert all(ref() is not None for ref in refs)
        recoveries.append(True)
        return remember(*args)

    monkeypatch.setattr(module, "remember_unacknowledged_calls", checked_remember)

    async def run():
        if path == "ai_task":
            return await task_module.ExtendedOpenAITaskEntity._async_generate_data(
                entity,
                ai_task.GenDataTask(name="probe", instructions="act", structure=None),
                log,
            )
        user = SimpleNamespace(
            text="act",
            language="en",
            conversation_id=log.conversation_id,
            as_llm_context=lambda _: None,
        )
        return await agent_module.ExtendedOpenAIAgentEntity._async_generate_message(
            entity, user, log, deferred_speech=[]
        )

    # Avoid retaining exception tracebacks, which also keep the coroutine alive.
    with suppress(HomeAssistantError, asyncio.CancelledError, RuntimeError):
        await run()
    assert effects == ["call-0"]
    assert recoveries
    gc.collect()
    assert all(ref() is None for ref in refs)
    monkeypatch.setattr(module, "remember_unacknowledged_calls", remember)
    # The earlier originals have now been released. Later Core calls operate on
    # the current history and must not assert the first turn's weak references.
    if path == "ai_task":
        log.async_provide_llm_data = AsyncMock()
    mode = "retry"
    try:
        await run()
    except HomeAssistantError as err:
        assert "not acknowledged" in str(err)
    assert effects == ["call-0"]
    mode = "acknowledge"
    await run()
    mode = "repeat"
    await run()
    assert len(effects) == 2


async def test_responses_downgrade_retains_originals_until_failure_bookkeeping(
    hass, monkeypatch
):
    log = _chat_log(hass)
    log.content.append(_TrackedUser(content="old"))
    ref = weakref.ref(log.content[-1])

    async def events(*_args, **_kwargs):
        log.content.clear()
        gc.collect()
        assert ref() is not None
        yield {"role": "assistant"}
        raise HomeAssistantError("failed response")

    monkeypatch.setattr(
        ExtendedOpenAIBaseLLMEntity, "_transform_responses_stream_events", events
    )
    from custom_components.extended_openai_conversation_responses import (
        entity as entity_module,
    )

    downgrade = entity_module.downgrade_native_response_output

    def checked_downgrade(*args):
        assert ref() is not None
        downgrade(*args)

    monkeypatch.setattr(
        entity_module, "downgrade_native_response_output", checked_downgrade
    )
    with suppress(HomeAssistantError):
        async for _delta in ExtendedOpenAIBaseLLMEntity._transform_responses_stream(
            _entity(hass), log, object()
        ):
            pass
    gc.collect()
    assert ref() is None


@pytest.mark.parametrize("cancel", [False, True])
async def test_local_intent_recovery_retains_pruned_originals(
    hass, monkeypatch, cancel
):
    log = _chat_log(hass)
    log.content.append(_TrackedUser(content="old"))
    ref = weakref.ref(log.content[-1])

    async def fail(*_args, **_kwargs):
        log.content.clear()
        gc.collect()
        assert ref() is not None
        if cancel:
            raise asyncio.CancelledError
        raise HomeAssistantError("failed local reply")

    monkeypatch.setattr(conversation, "async_handle_intents", fail)
    from custom_components.extended_openai_conversation_responses import tool_exchange

    retained = tool_exchange.retained_tool_calls_since

    def checked_retained(*args):
        assert ref() is not None
        return retained(*args)

    monkeypatch.setattr(tool_exchange, "retained_tool_calls_since", checked_retained)
    with suppress(HomeAssistantError, asyncio.CancelledError):
        await local_intents.async_try_handle_local_intent(
            hass,
            SimpleNamespace(context=None),
            log,
            {local_intents.CONF_LOCAL_INTENTS_ENABLED: True},
            guest_active=False,
        )
    gc.collect()
    assert ref() is None
