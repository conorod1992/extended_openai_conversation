"""Auxiliary summaries keep effective reasoning and have a bounded recovery budget."""

import asyncio
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.context_summary_performance import (
    context_summary_request,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.request import (
    context_summary_request_settings,
)
from homeassistant.components import conversation
from tests.test_context_management import _normal_history


def _response(mode, *, exhausted=False):
    return SimpleNamespace(
        status="incomplete" if exhausted else "completed",
        incomplete_details=SimpleNamespace(reason="max_output_tokens")
        if exhausted
        else None,
        output_text="The user prefers Celsius.",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="The user prefers Celsius."),
                finish_reason="length" if exhausted else "stop",
            )
        ],
        usage=SimpleNamespace(input_tokens=7, output_tokens=3, total_tokens=10),
    )


def _entity(mode, response, options=None, entry_data=None):
    create = AsyncMock(return_value=response)
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(
        data={
            "chat_model": "gpt-4.1",
            "context_truncate_strategy": "summarize",
            "context_threshold": 100,
            **(options or {}),
        }
    )
    entity.entry = SimpleNamespace(
        data=entry_data or {},
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(create=create),
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        ),
        async_create_task=lambda _hass, coro: asyncio.create_task(coro),
    )
    entity.hass = object()
    entity._usage = SimpleNamespace(async_record_request=AsyncMock())
    return entity, create


@pytest.mark.parametrize(
    "phases,expected",
    [
        (["commentary", "final_answer"], "Text 1"),
        (["commentary"], None),
        ([None], "Text 0"),
    ],
    ids=["mixed", "commentary-only", "unphased"],
)
async def test_responses_summary_persists_answer_text_only(phases, expected):
    response = _response("responses")
    response.output = [
        SimpleNamespace(
            type="message",
            role="assistant",
            status="completed",
            phase=phase,
            content=[SimpleNamespace(type="output_text", text=f"Text {index}")],
        )
        for index, phase in enumerate(phases)
    ]
    entity, _ = _entity("responses", response)
    result = await entity._async_summary_attempt(
        "Summarize", "gpt-4.1", "responses", entity.subentry.data, retry=False
    )
    # The SDK aggregate may contain all phases.
    assert result == (expected, False)
    entity._usage.async_record_request.assert_awaited_once()


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize(
    "model,effort",
    [("gpt-5", "minimal"), ("gpt-5.6", "none"), ("o3", "high"), ("gpt-4.1", None)],
)
async def test_summary_sends_effective_reasoning(mode, model, effort):
    entity, create = _entity(mode, _response(mode), {"reasoning_effort": effort})
    assert (
        await entity._async_summarize_history(_normal_history(), model, mode)
        == "The user prefers Celsius."
    )
    sent = create.await_args.kwargs
    if effort is None:
        assert "reasoning" not in sent and "reasoning_effort" not in sent
    elif mode == "responses":
        assert sent["reasoning"] == {"effort": effort}
    else:
        assert sent["reasoning_effort"] == effort
    field = "max_output_tokens" if mode == "responses" else "max_completion_tokens"
    assert sent[field] == (512 if effort in {None, "none"} else 8192)
    assert not {"tools", "temperature", "service_tier", "include"} & sent.keys()


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("deferred", [False, True])
async def test_effective_rule_options_survive_immediate_and_detached_handoff(
    mode, deferred
):
    entity, create = _entity(mode, _response(mode))
    effective = {
        **entity.subentry.data,
        "chat_model": "gpt-5",
        "reasoning_effort": "minimal",
    }
    history = _normal_history()
    history[1] = conversation.UserContent(content="I prefer Celsius. " * 20)
    log = SimpleNamespace(content=history, conversation_id="summary-settings")
    async with context_summary_request(entity, log):
        await entity._truncate_message_history(
            log,
            observed_input_tokens=1000,
            model="gpt-5",
            api_mode=mode,
            defer_summary=deferred,
            request_options=effective,
        )
        effective["reasoning_effort"] = "high"
    if deferred:
        await entity._deferred_context_summary_manager.async_apply(
            log.conversation_id, log.content
        )
    sent = create.await_args.kwargs
    assert sent["model"] == "gpt-5"
    assert (sent.get("reasoning") or {}).get(
        "effort", sent.get("reasoning_effort")
    ) == "minimal"
    assert any(
        "Conversation summary:" in (item.content or "") and "Celsius" in item.content
        for item in log.content
    )


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("recovers", [False, True])
async def test_only_one_token_exhaustion_retry_is_accounted(mode, recovers):
    entity, create = _entity(
        mode, _response(mode), {"chat_model": "gpt-5", "reasoning_effort": "minimal"}
    )
    create.side_effect = [
        _response(mode, exhausted=True),
        _response(mode, exhausted=not recovers),
    ]
    result = await entity._async_summarize_history(_normal_history(), "gpt-5", mode)
    assert result == ("The user prefers Celsius." if recovers else None)
    assert create.await_count == 2
    field = "max_output_tokens" if mode == "responses" else "max_completion_tokens"
    assert [call.kwargs[field] for call in create.await_args_list] == [8192, 16384]
    records = entity._usage.async_record_request.await_args_list
    assert [call.kwargs["successful"] for call in records] == [False, recovers]
    assert sum(call.kwargs["usage"].total_tokens for call in records) == 20


async def test_unknown_compatible_provider_keeps_legacy_token_parameter():
    mode = "chat_completions"
    entity, create = _entity(
        mode,
        _response(mode),
        entry_data={"api_provider": "custom", "base_url": "https://example.invalid/v1"},
    )
    assert await entity._async_summarize_history(
        _normal_history(), "my-custom-model", mode
    )
    assert create.await_args.kwargs["max_tokens"] == 512
    assert "max_completion_tokens" not in create.await_args.kwargs


def test_azure_summary_uses_underlying_model_capabilities():
    settings = context_summary_request_settings(
        "deployment-name",
        "responses",
        {"azure_model": "gpt-5", "reasoning_effort": "minimal"},
        {"api_provider": "azure"},
    )
    assert settings == {"max_output_tokens": 8192, "reasoning": {"effort": "minimal"}}


def test_summary_uses_recommended_effort_and_rejects_unsupported_values():
    assert context_summary_request_settings("gpt-5", "responses", {}, {})[
        "reasoning"
    ] == {"effort": "minimal"}
    with pytest.raises(ValueError, match="Invalid reasoning_effort"):
        context_summary_request_settings(
            "gpt-5", "responses", {"reasoning_effort": "none"}, {}
        )


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
async def test_model_ceiling_prevents_a_pointless_retry(monkeypatch, mode):
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses import request

    capabilities = deepcopy(request.get_model_capabilities("gpt-5"))
    capabilities["limits"]["max_output_tokens"] = 1024
    monkeypatch.setattr(
        request, "request_model_capabilities", lambda *_, **_kwargs: capabilities
    )
    entity, create = _entity(
        mode, _response(mode, exhausted=True), {"reasoning_effort": "minimal"}
    )
    assert (
        await entity._async_summarize_history(_normal_history(), "gpt-5", mode) is None
    )
    assert create.await_count == 1
    assert entity._usage.async_record_request.await_count == 1


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_transport_failure_and_cancellation_do_not_retry(mode, cancelled):
    entity, create = _entity(mode, _response(mode), {"reasoning_effort": "minimal"})
    create.side_effect = (
        asyncio.CancelledError()
        if cancelled
        else ConnectionError("provider unavailable")
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await entity._async_summarize_history(_normal_history(), "gpt-5", mode)
    else:
        assert (
            await entity._async_summarize_history(_normal_history(), "gpt-5", mode)
            is None
        )
    assert create.await_count == 1
    assert entity._usage.async_record_request.await_args.kwargs["successful"] is False


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("deferred", [False, True])
async def test_live_provider_loop_hands_effective_options_to_compaction(
    hass, mode, deferred
):
    from tests import (
        test_provider_tool_protocol as chat,
        test_tool_exchange_protocol as responses,
    )

    fixture = responses if mode == "responses" else chat
    stream = fixture._final_stream("Current reply")
    if deferred:
        if mode == "responses":
            stream.events[-1].response.usage.input_tokens = 5000
        else:
            stream._chunks[-1].usage = SimpleNamespace(
                prompt_tokens=5000, completion_tokens=5, total_tokens=5005
            )
    entity = fixture._entity(hass, [stream])
    entity.entry.async_create_task = lambda _hass, coroutine: asyncio.create_task(
        coroutine
    )
    entity._async_summarize_history = AsyncMock(return_value="Celsius retained")
    effective = {
        **entity.subentry.data,
        "chat_model": "gpt-5",
        "reasoning_effort": "minimal",
        "context_truncate_strategy": "summarize",
        "context_threshold": 1000 if deferred else 100,
    }
    log = fixture._chat_log(hass)
    log.content[1:1] = _normal_history(3)[1:]
    await entity._async_handle_chat_log(log, [], [], request_options=effective)
    if deferred:
        await entity._deferred_context_summary_manager.async_apply(
            log.conversation_id, log.content
        )
    call = entity._async_summarize_history.await_args
    assert call.args[1:] == ("gpt-5", mode)
    assert call.kwargs["request_options"]["reasoning_effort"] == "minimal"
    assert entity.subentry.data["chat_model"] != "gpt-5"


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("deferred", [False, True])
async def test_parent_summary_retries_on_captured_provider(
    hass, monkeypatch, mode, deferred
):
    from custom_components.extended_openai_conversation_responses import request
    from tests import (
        test_provider_tool_protocol as chat,
        test_tool_exchange_protocol as responses,
    )

    fixture = responses if mode == "responses" else chat
    stream = fixture._final_stream("Current reply")
    if deferred:
        if mode == "responses":
            stream.events[-1].response.usage.input_tokens = 5000
        else:
            stream._chunks[-1].usage = SimpleNamespace(
                prompt_tokens=5000, completion_tokens=5, total_tokens=5005
            )
    entity = fixture._entity(hass, [])
    entity.entry.data = {
        "api_provider": "azure",
        "base_url": "https://azure.invalid/openai/v1",
    }
    entity.entry.async_create_task = lambda _hass, coroutine: asyncio.create_task(
        coroutine
    )
    original_catalogue = request.get_model_capabilities
    current_catalogue = original_catalogue
    monkeypatch.setattr(
        request, "get_model_capabilities", lambda *a, **kw: current_catalogue(*a, **kw)
    )
    first_summary = asyncio.Event()
    release = asyncio.Event()
    summary_calls = []

    async def send(**kwargs):
        if kwargs.get("stream"):
            return stream
        summary_calls.append(kwargs)
        if len(summary_calls) == 1:
            first_summary.set()
            await release.wait()
            return _response(mode, exhausted=True)
        return _response(mode)

    old_client = entity.entry.runtime_data
    if mode == "responses":
        old_client.responses.create = send
    else:
        old_client.chat.completions.create = send
    new_create = AsyncMock(return_value=_response(mode))
    next_client = SimpleNamespace(
        responses=SimpleNamespace(create=new_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=new_create)),
    )
    account = ContextVar("summary_test_run", default="parent")
    accounting_contexts = []

    async def record(**kwargs):
        if kwargs["request_stage"] == "context_summary":
            accounting_contexts.append(account.get())

    old_usage = SimpleNamespace(
        _current_run=account,
        current_run=account.get,
        async_record_request=AsyncMock(side_effect=record),
    )
    next_usage = SimpleNamespace(async_record_request=AsyncMock())
    entity._usage = old_usage
    options = {
        **entity.subentry.data,
        "chat_model": "primary-deployment",
        "azure_model": "gpt-5",
        "reasoning_effort": "minimal",
        "context_truncate_strategy": "summarize",
        "context_threshold": 1000 if deferred else 100,
    }
    log = fixture._chat_log(hass)
    log.content[1:1] = _normal_history(3)[1:]
    task = asyncio.create_task(
        entity._async_handle_chat_log(log, [], [], request_options=options)
    )
    try:
        async with asyncio.timeout(5):
            await first_summary.wait()
            entity.entry.runtime_data = next_client
            entity.entry.data = {"api_provider": "openai"}
            entity._usage = next_usage

            def replacement(*args, **kwargs):
                capabilities = deepcopy(original_catalogue(*args, **kwargs))
                capabilities["limits"]["max_output_tokens"] = 1024
                return capabilities

            current_catalogue = replacement
            options["reasoning_effort"] = "high"
            release.set()
            await task
            if deferred:
                await entity._deferred_context_summary_manager.async_apply(
                    log.conversation_id, log.content
                )
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    new_create.assert_not_awaited()
    next_usage.async_record_request.assert_not_awaited()
    field = "max_output_tokens" if mode == "responses" else "max_completion_tokens"
    assert [call[field] for call in summary_calls] == [8192, 16384]
    assert [call["model"] for call in summary_calls] == ["primary-deployment"] * 2
    assert [
        ((call.get("reasoning") or {}).get("effort", call.get("reasoning_effort")))
        for call in summary_calls
    ] == ["minimal"] * 2
    records = [
        c.kwargs
        for c in old_usage.async_record_request.await_args_list
        if c.kwargs["request_stage"] == "context_summary"
    ]
    assert [(r["provider"], r["successful"]) for r in records] == [
        ("azure", False),
        ("azure", True),
    ]
    assert accounting_contexts == [None if deferred else "parent"] * 2
    assert any(
        "The user prefers Celsius." in (item.content or "") for item in log.content
    )
