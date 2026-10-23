"""Source-free HA diagnostics within EOAI-owned rendering tasks.

HA logs template source before an exception can reach our public-error boundary.
Filters keep those records useful without suppressing a logger or changing its
level. Context-local activation leaves unrelated HA tasks' diagnostics intact.
"""

from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
from typing import Any

_PRIVATE_RENDERING: ContextVar[bool] = ContextVar(
    "eoai_private_rendering", default=False
)
_LOGGERS = (
    "homeassistant.helpers.template",
    "homeassistant.helpers.script",
    "custom_components.extended_openai_conversation_responses.functions.script",
)


class _PrivateRenderingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _PRIVATE_RENDERING.get():
            return True
        category = "template" if record.name == _LOGGERS[0] else "script"
        # Only a recognized HA format supplies a numeric action position. Never
        # format the original message: names, arguments and exceptions are private.
        step = None
        failure = "unavailable"
        if (
            record.msg == "%s: Error executing script. %s for %s at pos %s: %s"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
            and type(record.args[3]) is int
        ):
            step = record.args[3]
            failure = {
                "Invalid data": "validation",
                "Error rendering template": "template",
                "Unauthorized": "permission",
                "Service not found": "missing_service",
                "Error": "backend",
                "Unexpected error": "unexpected",
            }.get(record.args[1], "unavailable")
        error_type = (
            type(record.exc_info[1]).__name__
            if record.exc_info and record.exc_info[1] is not None
            else failure
        )
        record.msg = (
            "EOAI private %s diagnostic; step=%s error_type=%s. "
            "Source omitted; check Function Tool actions and template argument types."
        )
        record.args = (category, step, error_type)
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


@contextmanager
def private_rendering_diagnostics() -> Iterator[None]:
    """Contain source-bearing dependency logs only during this owned execution."""
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        if not any(
            isinstance(item, _PrivateRenderingFilter) for item in logger.filters
        ):
            logger.addFilter(_PrivateRenderingFilter())
    token = _PRIVATE_RENDERING.set(True)
    try:
        yield
    finally:
        _PRIVATE_RENDERING.reset(token)


def private_template_diagnostics[**P, R](
    function: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """Retain context across awaits and HA's child Script runner tasks."""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with private_rendering_diagnostics():
            return await function(*args, **kwargs)

    return wrapped
