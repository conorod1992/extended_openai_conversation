"""Move optional context-summary maintenance off the completed-turn hot path."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from functools import wraps
from typing import Any, cast

from .const import (
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONTEXT_TRUNCATE_SUMMARIZE,
    DEFAULT_CONTEXT_THRESHOLD,
    LEGACY_CONTEXT_TRUNCATE_STRATEGY,
)
from .context_summary import ContextSummaryResult, DeferredContextSummaryManager

_INSTALLED = False
_DEFER_CONTEXT_SUMMARY: ContextVar[bool] = ContextVar(
    "extended_openai_defer_context_summary", default=False
)


def _manager(entity: Any) -> DeferredContextSummaryManager:
    manager = getattr(entity, "_deferred_context_summary_manager", None)
    if manager is None:
        manager = DeferredContextSummaryManager()
        entity._deferred_context_summary_manager = manager
    return cast(DeferredContextSummaryManager, manager)


async def _summarize_detached(
    entity: Any, older: list[Any], model: str, api_mode: str
) -> str | None:
    """Account background summary cost without mutating an already-finalized run."""
    usage = getattr(entity, "_usage", None)
    run_context = getattr(usage, "_current_run", None) if usage is not None else None
    token = run_context.set(None) if run_context is not None else None
    try:
        result = await entity._async_summarize_history(older, model, api_mode)
        return cast(str | None, result)
    finally:
        if run_context is not None and token is not None:
            run_context.reset(token)


def install_deferred_context_summary() -> None:
    """Install summary deferral once while keeping direct truncation synchronous."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import entity as entity_module

    base = entity_module.ExtendedOpenAIBaseLLMEntity
    original_handle = base._async_handle_chat_log
    original_truncate = base._truncate_message_history

    @wraps(original_handle)
    async def handle_with_pending_summary(
        self: Any, chat_log: Any, *args: Any, **kwargs: Any
    ) -> Any:
        conversation_id = str(getattr(chat_log, "conversation_id", "") or "")
        if conversation_id:
            await _manager(self).async_apply(conversation_id, chat_log.content)

        token = _DEFER_CONTEXT_SUMMARY.set(True)
        try:
            return await original_handle(self, chat_log, *args, **kwargs)
        finally:
            _DEFER_CONTEXT_SUMMARY.reset(token)

    @wraps(original_truncate)
    async def truncate_with_deferred_summary(
        self: Any,
        chat_log: Any,
        *,
        observed_input_tokens: int | None = None,
        model: str | None = None,
        api_mode: str | None = None,
    ) -> None:
        options = self.subentry.data
        strategy = options.get(
            CONF_CONTEXT_TRUNCATE_STRATEGY, LEGACY_CONTEXT_TRUNCATE_STRATEGY
        )
        conversation_id = str(getattr(chat_log, "conversation_id", "") or "")
        if (
            _DEFER_CONTEXT_SUMMARY.get()
            and strategy == CONTEXT_TRUNCATE_SUMMARIZE
            and conversation_id
            and model is not None
            and api_mode is not None
        ):
            target_tokens = int(
                options.get(CONF_CONTEXT_THRESHOLD, DEFAULT_CONTEXT_THRESHOLD)
            )
            observed = observed_input_tokens or target_tokens + 1

            def schedule(
                coroutine: Any,
            ) -> asyncio.Future[ContextSummaryResult]:
                return cast(
                    asyncio.Future[ContextSummaryResult],
                    self.entry.async_create_task(self.hass, coroutine),
                )

            if _manager(self).schedule(
                conversation_id,
                chat_log.content,
                observed_input_tokens=observed,
                target_tokens=target_tokens,
                model=model,
                api_mode=api_mode,
                summarize=lambda older, summary_model, summary_api_mode: (
                    _summarize_detached(self, older, summary_model, summary_api_mode)
                ),
                scheduler=schedule,
            ):
                return

        await original_truncate(
            self,
            chat_log,
            observed_input_tokens=observed_input_tokens,
            model=model,
            api_mode=api_mode,
        )

    base._async_handle_chat_log = handle_with_pending_summary  # type: ignore[method-assign]
    base._truncate_message_history = truncate_with_deferred_summary  # type: ignore[method-assign]
