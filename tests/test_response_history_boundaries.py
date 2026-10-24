"""Final-answer projection and responsive complete-turn retention contracts."""

import asyncio
import math
import threading
from types import SimpleNamespace

import pytest
from homeassistant.components import conversation

from custom_components.extended_openai_conversation_responses.assistant_response import (
    final_assistant_text,
)
from custom_components.extended_openai_conversation_responses.context import (
    async_keep_recent_messages,
    keep_recent_messages,
)
from custom_components.extended_openai_conversation_responses.context_summary import (
    DeferredContextSummaryManager,
)
from tests.test_context_management import _normal_history


def assistant(text, phase=None, native_type="message"):
    return conversation.AssistantContent(
        agent_id="agent",
        content=text,
        native=SimpleNamespace(type=native_type, phase=phase),
    )


@pytest.mark.parametrize("middle", ["commentary", "final_answer", None])
def test_final_answer_joins_current_messages_and_excludes_progress(middle):
    content = [
        conversation.UserContent(content="old"),
        assistant("Old answer"),
        conversation.UserContent(content="current"),
        assistant("Progress", "commentary"),
        assistant("First", "final_answer"),
        assistant("Middle", middle),
        assistant("thinking", native_type="reasoning"),
        assistant("Last", "final_answer"),
    ]
    before = list(content)
    assert final_assistant_text(content) == "\n\n".join(
        ["First", *([] if middle == "commentary" else ["Middle"]), "Last"]
    )
    assert content == before
    assert final_assistant_text(content, separator="").endswith("Last")


@pytest.mark.parametrize("oversized_current", [False, True])
async def test_retention_uses_bounded_worker_measurements_and_keeps_maximal_suffix(
    oversized_current,
):
    content = _normal_history(128)
    loop_thread = threading.get_ident()
    calls = []

    def estimate(items):
        calls.append((threading.get_ident(), len(items)))
        return sum(len(item.content or "") for item in items)

    current = sum(len(item.content or "") for item in [content[0], *content[-2:]])
    target = current - 1 if oversized_current else current * 7
    expected = list(content)
    while len(expected) > 3 and estimate(expected) > target:
        del expected[1:3]
    calls.clear()
    assert await async_keep_recent_messages(content, 100000, target, estimate)
    assert content == expected
    assert all(thread != loop_thread for thread, _ in calls)
    assert len(calls) <= (1 if oversized_current else math.ceil(math.log2(128)) + 2)
    if oversized_current:
        assert [length for _, length in calls] == [3]


async def test_retention_does_not_overwrite_content_appended_during_worker():
    content = _normal_history(4)
    initial = list(content)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def estimate(_items):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return 1000

    task = asyncio.create_task(async_keep_recent_messages(content, 1000, 1, estimate))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        extra = conversation.UserContent(content="new arrival")
        content.append(extra)
        release.set()
        assert not await task
        assert content == [*initial, extra]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_deferred_summary_selection_and_signature_are_prepared_in_workers(
    monkeypatch,
):
    from custom_components.extended_openai_conversation_responses import (
        context_summary as module,
    )

    manager = DeferredContextSummaryManager()
    content = _normal_history(40)
    loop_thread = threading.get_ident()
    signatures = []
    original = module._history_signature

    def checked(items):
        signatures.append(threading.get_ident())
        return original(items)

    monkeypatch.setattr(module, "_history_signature", checked)

    async def summarize(*_args):
        assert threading.get_ident() == loop_thread
        return "Summary"

    def scheduler(coro):
        assert threading.get_ident() == loop_thread
        return asyncio.create_task(coro)

    assert await manager.async_schedule(
        "conversation",
        content,
        observed_input_tokens=10000,
        target_tokens=1000,
        model="gpt-5.6",
        api_mode="responses",
        summarize=summarize,
        scheduler=scheduler,
    )
    assert await manager.async_apply("conversation", content)
    assert len(signatures) == 2 and loop_thread not in signatures
    assert "Summary" in content[1].content


@pytest.mark.parametrize("reason", ["empty", "duplicate", "full"])
async def test_async_summary_capacity_checks_precede_bulk_preparation(
    monkeypatch, reason
):
    from custom_components.extended_openai_conversation_responses import (
        context_summary as module,
    )

    manager = DeferredContextSummaryManager()
    future = asyncio.get_running_loop().create_future()
    if reason == "duplicate":
        manager._pending["conversation"] = SimpleNamespace(task=future, created_at=0)
    elif reason == "full":
        manager._pending = {
            str(i): SimpleNamespace(task=future, created_at=0)
            for i in range(module.MAX_PENDING_CONTEXT_SUMMARIES)
        }

    def must_not_prepare(*_args):
        pytest.fail("ineligible summary entered bulk preparation")

    monkeypatch.setattr(module, "_prepare_summary_plan", must_not_prepare)
    assert not await manager.async_schedule(
        "" if reason == "empty" else "conversation",
        _normal_history(),
        observed_input_tokens=10000,
        target_tokens=1000,
        model="gpt-5.6",
        api_mode="responses",
        summarize=must_not_prepare,
        scheduler=must_not_prepare,
    )


async def test_cancelled_summary_signature_keeps_pending_result_for_retry(monkeypatch):
    from custom_components.extended_openai_conversation_responses import (
        context_summary as module,
    )

    manager = DeferredContextSummaryManager()
    content = _normal_history(5)
    original_content = list(content)

    async def summarize(*_args):
        return "Retained summary"

    assert manager.schedule(
        "conversation",
        content,
        observed_input_tokens=10000,
        target_tokens=1000,
        model="gpt-5.6",
        api_mode="responses",
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    await manager._pending["conversation"].task
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    fingerprint = module._history_signature

    def blocked(items):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return fingerprint(items)

    monkeypatch.setattr(module, "_history_signature", blocked)
    apply = asyncio.create_task(manager.async_apply("conversation", content))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        apply.cancel()
        with pytest.raises(asyncio.CancelledError):
            await apply
        assert "conversation" in manager._pending
        assert content == original_content
        release.set()
        assert await manager.async_apply("conversation", content)
        assert "Retained summary" in content[1].content
        assert "conversation" not in manager._pending
    finally:
        release.set()
        await asyncio.gather(apply, return_exceptions=True)


async def test_summary_does_not_overwrite_prefix_rewritten_during_worker(monkeypatch):
    from custom_components.extended_openai_conversation_responses import (
        context_summary as module,
    )

    manager = DeferredContextSummaryManager()
    content = _normal_history(4)

    async def summarize(*_args):
        return "Old summary"

    assert manager.schedule(
        "conversation",
        content,
        observed_input_tokens=10000,
        target_tokens=1000,
        model="gpt-5.6",
        api_mode="responses",
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    await manager._pending["conversation"].task
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    fingerprint = module._history_signature

    def blocked(items):
        result = fingerprint(items)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return result

    monkeypatch.setattr(module, "_history_signature", blocked)
    apply = asyncio.create_task(manager.async_apply("conversation", content))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        content[1] = conversation.UserContent(content="Rewritten user history")
        rewritten = list(content)
        release.set()
        assert not await apply
        assert content == rewritten
        assert "conversation" not in manager._pending
    finally:
        release.set()
        await asyncio.gather(apply, return_exceptions=True)
