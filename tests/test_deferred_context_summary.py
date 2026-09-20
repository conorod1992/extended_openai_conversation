"""Tests for post-turn context summarization."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import context_summary as subject
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONTEXT_TRUNCATE_SUMMARIZE,
)
from custom_components.extended_openai_conversation_responses.context import (
    partition_history,
)
from custom_components.extended_openai_conversation_responses.context_summary import (
    DeferredContextSummaryManager,
)
from custom_components.extended_openai_conversation_responses.context_summary_performance import (
    _DEFER_CONTEXT_SUMMARY,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from homeassistant.components import conversation


def _history(turns: int = 5) -> list[conversation.Content]:
    content: list[conversation.Content] = [
        conversation.SystemContent(content="System prompt")
    ]
    for number in range(turns):
        content.extend(
            [
                conversation.UserContent(content=f"User turn {number} " * 20),
                conversation.AssistantContent(
                    agent_id="conversation.test",
                    content=f"Assistant turn {number} " * 20,
                ),
            ]
        )
    return content


async def test_manager_schedules_without_waiting_and_applies_before_followup() -> None:
    manager = DeferredContextSummaryManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(_older, _model, _api_mode):
        started.set()
        await release.wait()
        return "The user chose Celsius."

    content = _history()
    original = list(content)
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    assert content == original
    await asyncio.wait_for(started.wait(), timeout=1)

    content.append(conversation.UserContent(content="What about tomorrow?"))
    apply_task = asyncio.create_task(manager.async_apply("conversation:test", content))
    await asyncio.sleep(0)
    assert not apply_task.done()

    release.set()
    assert await asyncio.wait_for(apply_task, timeout=1)
    assert isinstance(content[0], conversation.SystemContent)
    assert isinstance(content[1], conversation.SystemContent)
    assert "Celsius" in content[1].content
    assert content[-1].content == "What about tomorrow?"


async def test_cancelled_followup_keeps_pending_summary_for_next_attempt() -> None:
    manager = DeferredContextSummaryManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(_older, _model, _api_mode):
        started.set()
        await release.wait()
        return "The user chose Celsius."

    content = _history()
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    content.append(conversation.UserContent(content="First followup"))

    apply_task = asyncio.create_task(manager.async_apply("conversation:test", content))
    await asyncio.sleep(0)
    apply_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await apply_task

    release.set()
    assert await manager.async_apply("conversation:test", content)
    assert "Celsius" in content[1].content
    assert content[-1].content == "First followup"


async def test_manager_summary_failure_uses_existing_keep_recent_fallback() -> None:
    manager = DeferredContextSummaryManager()

    async def summarize(_older, _model, _api_mode):
        return None

    content = _history()
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    content.append(conversation.UserContent(content="Newest question"))

    assert await manager.async_apply("conversation:test", content)
    assert len(partition_history(content[:-1]).turns) == 1
    assert "User turn 4" in content[-3].content
    assert content[-1].content == "Newest question"


async def test_manager_does_not_overwrite_changed_history() -> None:
    manager = DeferredContextSummaryManager()

    async def summarize(_older, _model, _api_mode):
        return "Summary"

    content = _history()
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    content[2] = conversation.AssistantContent(
        agent_id="conversation.test", content="Externally changed"
    )
    current = list(content)

    assert not await manager.async_apply("conversation:test", content)
    assert content == current


async def test_live_truncation_hook_returns_before_summary_provider_finishes() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def create_summary(**_kwargs):
        started.set()
        await release.wait()
        return SimpleNamespace(output_text="The user chose Celsius.", usage=None)

    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=create_summary))
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(
        runtime_data=client,
        data={},
        async_create_task=lambda _hass, coroutine: asyncio.create_task(coroutine),
    )
    entity.subentry = SimpleNamespace(
        data={
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_SUMMARIZE,
            CONF_CONTEXT_THRESHOLD: 100,
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
        }
    )
    entity._usage = None
    entity.__dict__["hass"] = SimpleNamespace()
    chat_log = SimpleNamespace(conversation_id="conversation:test", content=_history())
    original = list(chat_log.content)

    token = _DEFER_CONTEXT_SUMMARY.set(True)
    try:
        await asyncio.wait_for(
            entity._truncate_message_history(
                chat_log,
                observed_input_tokens=1000,
                model="gpt-5.6-luna",
                api_mode=API_MODE_RESPONSES,
            ),
            timeout=1,
        )
    finally:
        _DEFER_CONTEXT_SUMMARY.reset(token)

    assert chat_log.content == original
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    assert await DeferredContextSummaryManager.async_apply(
        entity._deferred_context_summary_manager,
        "conversation:test",
        chat_log.content,
    )
    assert "Celsius" in chat_log.content[1].content


def _pending(
    task: asyncio.Future[subject.ContextSummaryResult],
    *,
    length: int = 1,
    signature: str = "signature",
    fallback: list[object] | None = None,
    created_at: float = 0.0,
) -> subject.PendingContextSummary:
    return subject.PendingContextSummary(
        snapshot_length=length,
        snapshot_signature=signature,
        fallback_content=list(fallback or []),  # type: ignore[arg-type]
        task=task,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_schedule_rejects_invalid_duplicate_full_and_unneeded_work(monkeypatch) -> None:
    """Scheduling stays bounded and avoids summaries that cannot help."""
    manager = subject.DeferredContextSummaryManager()
    loop = asyncio.get_running_loop()

    assert manager.schedule(
        "",
        [],
        observed_input_tokens=100,
        target_tokens=50,
        model="model",
        api_mode="responses",
        summarize=lambda *_args: asyncio.sleep(0),  # type: ignore[arg-type]
        scheduler=lambda coro: loop.create_task(coro),
    ) is False

    blocker: asyncio.Future[subject.ContextSummaryResult] = loop.create_future()
    manager._pending["duplicate"] = _pending(blocker)
    assert manager.schedule(
        "duplicate",
        [],
        observed_input_tokens=100,
        target_tokens=50,
        model="model",
        api_mode="responses",
        summarize=lambda *_args: asyncio.sleep(0),  # type: ignore[arg-type]
        scheduler=lambda coro: loop.create_task(coro),
    ) is False

    manager._pending.clear()
    for index in range(subject.MAX_PENDING_CONTEXT_SUMMARIES):
        manager._pending[str(index)] = _pending(blocker)
    assert manager.schedule(
        "overflow",
        [],
        observed_input_tokens=100,
        target_tokens=50,
        model="model",
        api_mode="responses",
        summarize=lambda *_args: asyncio.sleep(0),  # type: ignore[arg-type]
        scheduler=lambda coro: loop.create_task(coro),
    ) is False

    manager._pending.clear()
    monkeypatch.setattr(subject, "select_summary_history", lambda *_args: None)
    assert manager.schedule(
        "not-needed",
        [object()],  # type: ignore[list-item]
        observed_input_tokens=10,
        target_tokens=100,
        model="model",
        api_mode="responses",
        summarize=lambda *_args: asyncio.sleep(0),  # type: ignore[arg-type]
        scheduler=lambda coro: loop.create_task(coro),
    ) is False


@pytest.mark.asyncio
async def test_async_apply_uses_fallback_when_worker_was_cancelled(monkeypatch) -> None:
    """A cancelled maintenance task degrades to the already-computed safe fallback."""
    manager = subject.DeferredContextSummaryManager()
    task: asyncio.Future[subject.ContextSummaryResult] = asyncio.get_running_loop().create_future()
    task.cancel()
    fallback = [object()]
    manager._pending["chat"] = _pending(task, fallback=fallback)
    monkeypatch.setattr(subject, "_history_signature", lambda _items: "signature")
    content: list[object] = [object()]

    assert await manager.async_apply("chat", content) is True  # type: ignore[arg-type]
    assert content == fallback
    assert "chat" not in manager._pending


@pytest.mark.asyncio
async def test_async_apply_rejects_shortened_or_changed_prefix(monkeypatch) -> None:
    """A deferred result never overwrites conversation history that changed meanwhile."""
    manager = subject.DeferredContextSummaryManager()
    loop = asyncio.get_running_loop()

    short_task = loop.create_future()
    short_task.set_result(subject.ContextSummaryResult([object()], summarized=True))  # type: ignore[list-item]
    manager._pending["short"] = _pending(short_task, length=2)
    short_content: list[object] = [object()]
    assert await manager.async_apply("short", short_content) is False  # type: ignore[arg-type]
    assert "short" not in manager._pending

    changed_task = loop.create_future()
    changed_task.set_result(subject.ContextSummaryResult([object()], summarized=True))  # type: ignore[list-item]
    manager._pending["changed"] = _pending(changed_task, length=1, signature="original")
    monkeypatch.setattr(subject, "_history_signature", lambda _items: "different")
    changed_content: list[object] = [object()]
    assert await manager.async_apply("changed", changed_content) is False  # type: ignore[arg-type]
    assert "changed" not in manager._pending


@pytest.mark.asyncio
async def test_async_apply_preserves_current_system_and_new_suffix(monkeypatch) -> None:
    """Compaction keeps the live system prompt once and preserves post-snapshot turns."""
    manager = subject.DeferredContextSummaryManager()
    loop = asyncio.get_running_loop()
    current_system = conversation.SystemContent(content="current")
    old_system = conversation.SystemContent(content="old")
    summary = conversation.SystemContent(content="Conversation summary:\nsummary")
    suffix = object()
    task = loop.create_future()
    task.set_result(subject.ContextSummaryResult([old_system, summary], summarized=True))
    manager._pending["chat"] = _pending(task, length=1)
    monkeypatch.setattr(subject, "_history_signature", lambda _items: "signature")
    content = [current_system, suffix]

    assert await manager.async_apply("chat", content) is True
    assert content == [current_system, summary, suffix]


@pytest.mark.asyncio
async def test_prune_completed_removes_oldest_finished_work_only() -> None:
    """Capacity recovery discards completed abandoned work without cancelling live tasks."""
    manager = subject.DeferredContextSummaryManager()
    loop = asyncio.get_running_loop()
    live = loop.create_future()
    done_old = loop.create_future()
    done_old.set_result(subject.ContextSummaryResult([], summarized=False))
    done_new = loop.create_future()
    done_new.set_result(subject.ContextSummaryResult([], summarized=False))

    manager._pending["old"] = _pending(done_old, created_at=1)
    manager._pending["new"] = _pending(done_new, created_at=2)
    for index in range(subject.MAX_PENDING_CONTEXT_SUMMARIES - 2):
        manager._pending[f"live-{index}"] = _pending(live, created_at=3 + index)

    manager._prune_completed()

    assert len(manager._pending) == subject.MAX_PENDING_CONTEXT_SUMMARIES - 1
    assert "old" not in manager._pending
    assert "new" in manager._pending
    assert live.cancelled() is False

    manager._prune_completed()
    assert len(manager._pending) == subject.MAX_PENDING_CONTEXT_SUMMARIES - 1


@pytest.mark.asyncio
async def test_build_result_falls_back_on_summarizer_failure(monkeypatch, caplog) -> None:
    """Provider summarization failure returns the safe recent-turn fallback."""
    fallback = [object()]
    older = [object()]
    monkeypatch.setattr(
        subject,
        "partition_history",
        lambda _items: SimpleNamespace(prefix=[], turns=[]),
    )

    async def fail(*_args):
        raise RuntimeError("provider failed")

    result = await subject.DeferredContextSummaryManager._async_build_result(
        [object()],  # type: ignore[list-item]
        (older, []),  # type: ignore[arg-type]
        model="model",
        api_mode="responses",
        summarize=fail,
        fallback=fallback,  # type: ignore[arg-type]
    )

    assert result.summarized is False
    assert result.content == fallback
    assert "Deferred conversation summarization failed" in caplog.text


@pytest.mark.asyncio
async def test_build_result_propagates_cancellation(monkeypatch) -> None:
    """Task cancellation remains cancellation rather than being mistaken for provider failure."""
    monkeypatch.setattr(
        subject,
        "partition_history",
        lambda _items: SimpleNamespace(prefix=[], turns=[]),
    )

    async def cancel(*_args):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await subject.DeferredContextSummaryManager._async_build_result(
            [],
            ([object()], []),  # type: ignore[arg-type]
            model="model",
            api_mode="responses",
            summarize=cancel,
            fallback=[],
        )


def test_history_signature_tolerates_all_supported_native_serializers(monkeypatch) -> None:
    """Fingerprinting remains stable even when SDK-native objects serialize badly."""

    class Assistant:
        def __init__(self, native):
            self.native = native

    class ModelDump:
        type = "model"
        id = "1"

        def model_dump(self, *, exclude_none):
            assert exclude_none is True
            return {"ok": 1}

    class BrokenModelDump:
        type = "broken-model"
        id = "2"

        def model_dump(self, *, exclude_none):
            raise ValueError("bad model dump")

        def __repr__(self):
            return "broken-model-repr"

    class ToDict:
        type = "dict"
        id = "3"

        def to_dict(self):
            return {"ok": 2}

    class BrokenToDict:
        type = "broken-dict"
        id = "4"

        def to_dict(self):
            raise ValueError("bad to_dict")

        def __repr__(self):
            return "broken-dict-repr"

    class ReprOnly:
        type = "repr"
        id = "5"

        def __repr__(self):
            return "repr-only"

    monkeypatch.setattr(subject.conversation, "AssistantContent", Assistant)
    monkeypatch.setattr(subject, "history_as_summary_text", lambda _items: "transcript")
    items = [
        Assistant(None),
        Assistant(ModelDump()),
        Assistant(BrokenModelDump()),
        Assistant(ToDict()),
        Assistant(BrokenToDict()),
        Assistant({"raw": True}),
        Assistant(ReprOnly()),
        object(),
    ]

    first = subject._history_signature(items)  # type: ignore[arg-type]
    second = subject._history_signature(items)  # type: ignore[arg-type]

    assert first == second
    assert len(first) == 64
