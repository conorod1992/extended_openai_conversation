"""Deferred context summarization without weakening next-turn continuity."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any

from homeassistant.components import conversation

from .context import (
    HistoryEstimator,
    history_as_summary_text,
    keep_recent_messages,
    partition_history,
    select_summary_history,
)
from .operational_errors import log_handled_failure

_LOGGER = logging.getLogger(__name__)

MAX_PENDING_CONTEXT_SUMMARIES = 128
SummaryCallback = Callable[
    [list[conversation.Content], str, str], Awaitable[str | None]
]
SummaryTaskScheduler = Callable[
    [Coroutine[Any, Any, "ContextSummaryResult"]],
    asyncio.Future["ContextSummaryResult"],
]


@dataclass(slots=True)
class ContextSummaryResult:
    """A compacted immutable-by-convention history snapshot."""

    content: list[conversation.Content]
    summarized: bool


@dataclass(slots=True)
class ContextSummaryPlan:
    """Detached bulk work, prepared before creating tasks or publishing state."""

    selected: tuple[list[conversation.Content], list[conversation.Content]]
    fallback: list[conversation.Content]
    signature: str


def _prepare_summary_plan(
    snapshot: list[conversation.Content],
    observed: int,
    target: int,
    estimate: HistoryEstimator | None,
) -> ContextSummaryPlan | None:
    selected = select_summary_history(snapshot, observed, target, estimate)
    if selected is None:
        return None
    fallback = list(snapshot)
    keep_recent_messages(fallback, observed, target, estimate)
    return ContextSummaryPlan(selected, fallback, _history_signature(snapshot[1:]))


@dataclass(slots=True)
class PendingContextSummary:
    """One summary generated from a specific completed conversation prefix."""

    snapshot_length: int
    snapshot_signature: str
    fallback_content: list[conversation.Content]
    task: asyncio.Future[ContextSummaryResult]
    created_at: float


class DeferredContextSummaryManager:
    """Generate summaries after a reply and apply them before the next provider call."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingContextSummary] = {}

    def schedule(
        self,
        conversation_id: str,
        content: list[conversation.Content],
        *,
        observed_input_tokens: int,
        target_tokens: int,
        model: str,
        api_mode: str,
        summarize: SummaryCallback,
        scheduler: SummaryTaskScheduler,
        estimate: HistoryEstimator | None = None,
        _prepared: ContextSummaryPlan | None = None,
    ) -> bool:
        """Schedule one bounded summary without mutating the live chat log."""
        if not conversation_id or conversation_id in self._pending:
            return False
        self._prune_completed()
        if len(self._pending) >= MAX_PENDING_CONTEXT_SUMMARIES:
            return False

        snapshot = list(content)
        plan = _prepared or _prepare_summary_plan(
            snapshot, observed_input_tokens, target_tokens, estimate
        )
        if plan is None:
            return False
        selected, fallback = plan.selected, plan.fallback
        task = scheduler(
            self._async_build_result(
                snapshot,
                selected,
                model=model,
                api_mode=api_mode,
                summarize=summarize,
                fallback=fallback,
            )
        )
        self._pending[conversation_id] = PendingContextSummary(
            snapshot_length=len(snapshot),
            snapshot_signature=plan.signature,
            fallback_content=fallback,
            task=task,
            created_at=time.monotonic(),
        )
        return True

    async def async_schedule(
        self,
        conversation_id: str,
        content: list[conversation.Content],
        *,
        observed_input_tokens: int,
        target_tokens: int,
        model: str,
        api_mode: str,
        summarize: SummaryCallback,
        scheduler: SummaryTaskScheduler,
        estimate: HistoryEstimator | None = None,
    ) -> bool:
        """Offload selection and signatures, keeping task ownership on the loop."""
        if not conversation_id or conversation_id in self._pending:
            return False
        self._prune_completed()
        if len(self._pending) >= MAX_PENDING_CONTEXT_SUMMARIES:
            return False
        snapshot = list(content)
        plan = await asyncio.to_thread(
            _prepare_summary_plan,
            snapshot,
            observed_input_tokens,
            target_tokens,
            estimate,
        )
        if plan is None:
            return False
        return self.schedule(
            conversation_id,
            snapshot,
            observed_input_tokens=observed_input_tokens,
            target_tokens=target_tokens,
            model=model,
            api_mode=api_mode,
            summarize=summarize,
            scheduler=scheduler,
            estimate=estimate,
            _prepared=plan,
        )

    async def async_apply(
        self, conversation_id: str, content: list[conversation.Content]
    ) -> bool:
        """Apply a pending result to its exact prior prefix, preserving new content."""
        pending = self._pending.get(conversation_id)
        if pending is None:
            return False

        remove_pending = False
        try:
            try:
                result = await asyncio.shield(pending.task)
            except asyncio.CancelledError:
                if not pending.task.cancelled():
                    raise
                result = ContextSummaryResult(
                    list(pending.fallback_content), summarized=False
                )
            if len(content) < pending.snapshot_length:
                remove_pending = True
                _LOGGER.warning(
                    "Deferred context summary was not applied because conversation "
                    "history became shorter while it was pending"
                )
                return False
            prefix = list(content[1 : pending.snapshot_length])
            signature = await asyncio.to_thread(_history_signature, prefix)
            # Cancellation during worker preparation must leave this completed
            # summary available for the next owned request to apply.
            remove_pending = True
            current_prefix = content[1 : pending.snapshot_length]
            if (
                signature != pending.snapshot_signature
                or len(current_prefix) != len(prefix)
                or any(
                    current is not original
                    for current, original in zip(current_prefix, prefix, strict=True)
                )
            ):
                _LOGGER.warning(
                    "Deferred context summary was not applied because conversation "
                    "history changed while it was pending"
                )
                return False

            current_system = (
                [content[0]]
                if content and isinstance(content[0], conversation.SystemContent)
                else []
            )
            compacted_tail = list(result.content)
            if (
                current_system
                and compacted_tail
                and isinstance(compacted_tail[0], conversation.SystemContent)
            ):
                compacted_tail = compacted_tail[1:]
            suffix = content[pending.snapshot_length :]
            content[:] = [*current_system, *compacted_tail, *suffix]
            return True
        finally:
            if remove_pending:
                self._pending.pop(conversation_id, None)

    def _prune_completed(self) -> None:
        """Bound abandoned completed summaries without cancelling live maintenance."""
        if len(self._pending) < MAX_PENDING_CONTEXT_SUMMARIES:
            return
        completed = sorted(
            (
                (key, pending)
                for key, pending in self._pending.items()
                if pending.task.done()
            ),
            key=lambda item: item[1].created_at,
        )
        for key, _pending in completed:
            self._pending.pop(key, None)
            if len(self._pending) < MAX_PENDING_CONTEXT_SUMMARIES:
                break

    @staticmethod
    async def _async_build_result(
        snapshot: list[conversation.Content],
        selected: tuple[list[conversation.Content], list[conversation.Content]],
        *,
        model: str,
        api_mode: str,
        summarize: SummaryCallback,
        fallback: list[conversation.Content],
    ) -> ContextSummaryResult:
        """Build exactly the summary shape used by synchronous truncation."""
        older, retained = selected
        retained_parts = partition_history(retained)
        summary_source = [*retained_parts.prefix[1:], *older]
        try:
            summary = await summarize(summary_source or older, model, api_mode)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            log_handled_failure(
                _LOGGER, "Deferred conversation summarization failed", err
            )
            summary = None

        if summary:
            _LOGGER.info(
                "Context threshold exceeded, older conversation summarized after reply"
            )
            return ContextSummaryResult(
                [
                    *retained_parts.prefix[:1],
                    conversation.SystemContent(
                        content=f"Conversation summary:\n{summary}"
                    ),
                    *(item for turn in retained_parts.turns for item in turn),
                ],
                summarized=True,
            )

        _LOGGER.warning(
            "Conversation summarization failed; keeping recent valid turns instead"
        )
        return ContextSummaryResult(list(fallback), summarized=False)


def _history_signature(items: list[conversation.Content]) -> str:
    """Fingerprint model-relevant history while tolerating SDK-native item types."""
    native_markers: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, conversation.AssistantContent) or item.native is None:
            continue
        native = item.native
        if hasattr(native, "model_dump"):
            try:
                serialized = native.model_dump(exclude_none=True)
            except Exception:
                serialized = repr(native)
        elif hasattr(native, "to_dict"):
            try:
                serialized = native.to_dict()
            except Exception:
                serialized = repr(native)
        elif isinstance(native, dict):
            serialized = native
        else:
            serialized = repr(native)
        native_markers.append(
            {
                "type": getattr(native, "type", None),
                "id": getattr(native, "id", None),
                "value": serialized,
            }
        )

    payload = json.dumps(
        {
            "transcript": history_as_summary_text(items),
            "native": native_markers,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
