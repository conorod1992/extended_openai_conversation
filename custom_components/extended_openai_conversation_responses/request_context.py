"""Ownership of metadata and speculative retrieval for one public invocation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

from .operation_settlement import async_wait_owned_cleanup


@dataclass(frozen=True, slots=True)
class RequestInvocation:
    """Task-local binding, with explicitly shared metadata and an owned task.

    Replacing this record preserves ContextVar copy semantics in child tasks.
    Nested public calls install fresh records; metadata keeps its existing shared
    dictionary semantics within one invocation.
    """

    metadata: dict[str, Any] | None = None
    temporary_memory_prefetch: asyncio.Task[Any] | None = None


_NO_INVOCATION = RequestInvocation()
_ACTIVE_INVOCATION: ContextVar[RequestInvocation] = ContextVar(
    "extended_openai_request_invocation", default=_NO_INVOCATION
)


def set_temporary_memory_prefetch(task: asyncio.Task[Any] | None) -> None:
    """Replace only this task's speculative retrieval handle."""
    _ACTIVE_INVOCATION.set(
        replace(_ACTIVE_INVOCATION.get(), temporary_memory_prefetch=task)
    )


@asynccontextmanager
async def conversation_invocation(
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[None]:
    """Restore the caller and drain only this invocation's abandoned retrieval.

    A failed speculative read must not replace the actual request outcome. Caller
    cancellation during cleanup still propagates after that owned read settles.
    """
    token = _ACTIVE_INVOCATION.set(RequestInvocation(metadata=metadata))
    primary_error: BaseException | None = None
    try:
        yield
    except BaseException as err:
        primary_error = err
        raise
    finally:
        task = _ACTIVE_INVOCATION.get().temporary_memory_prefetch
        _ACTIVE_INVOCATION.reset(token)
        if task is not None:
            if not task.done():
                task.cancel()
            cancellation = await async_wait_owned_cleanup(task)
            with suppress(BaseException):
                task.result()
            if cancellation is not None and primary_error is None:
                raise cancellation
