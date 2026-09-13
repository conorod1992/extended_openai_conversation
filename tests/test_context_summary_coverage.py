"""Focused edge-case coverage for deferred context summarization."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homeassistant.components import conversation

from custom_components.extended_openai_conversation_responses import context_summary as subject


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
