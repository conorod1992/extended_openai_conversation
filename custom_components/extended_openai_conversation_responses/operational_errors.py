"""Small, payload-free diagnostics for handled operational failures."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import errno
import logging
from pathlib import Path
import traceback

_FUNCTION_TOOL_NAME: ContextVar[str] = ContextVar(
    "eoai_diagnostic_tool", default="unknown"
)


@contextmanager
def function_tool_log_context(name: str) -> Iterator[None]:
    """Carry only a tool identifier into executor-based extraction logs."""
    token = _FUNCTION_TOOL_NAME.set(name)
    try:
        yield
    finally:
        _FUNCTION_TOOL_NAME.reset(token)


def current_function_tool_name() -> str:
    """Return the configured tool name, never its arguments."""
    return _FUNCTION_TOOL_NAME.get()


def storage_failure_reason(number: int | None) -> str:
    """Describe an OS failure without a private filename or stored value."""
    return {
        errno.ENOSPC: "storage is full; free disk space",
        errno.EACCES: "permission denied; check Home Assistant storage permissions",
        errno.EPERM: "permission denied; check Home Assistant storage permissions",
        errno.EROFS: "filesystem is read-only; restore writable Home Assistant storage",
    }.get(number, "storage operation failed")


def log_handled_failure(
    logger: logging.Logger,
    operation: str,
    error: BaseException,
    *,
    level: int = logging.WARNING,
) -> None:
    """Log categories and DEBUG stack locations, never exception payloads.

    Exception text, source lines and locals can contain credentials, paths,
    templates or tool results. Keep the original call sites and causal categories
    instead of handing the exception to logging's unrestricted formatter.
    Callers supply only known operation names and safe identifiers.
    """
    number = error.errno if isinstance(error, OSError) else None
    logger.log(
        level,
        "%s; error_type=%s errno=%s%s",
        operation,
        type(error).__name__,
        number,
        f" ({storage_failure_reason(number)})" if number is not None else "",
    )
    if not logger.isEnabledFor(logging.DEBUG):
        return
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        frames = traceback.extract_tb(current.__traceback__, limit=16)
        logger.debug(
            "%s; cause_type=%s traceback=%s",
            operation,
            type(current).__name__,
            " -> ".join(
                f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                for frame in frames
            )
            or "<no stack>",
        )
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
