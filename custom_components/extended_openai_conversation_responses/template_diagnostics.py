"""Source-free HA diagnostics within EOAI-owned rendering tasks.

HA logs template source before an exception can reach our public-error boundary.
Project at record creation: parent logger filters do not cover child loggers.
Context-local activation leaves unrelated HA tasks' diagnostics intact, without
changing logger levels or suppressing records. Existing record factories chain.
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
        if not _PRIVATE_RENDERING.get() or getattr(
            record, "_eoai_private_template_projected", False
        ):
            return True
        family = next(
            (
                name
                for name in _LOGGERS
                if record.name == name or record.name.startswith(name + ".")
            ),
            None,
        )
        if family is None:
            return True
        record._eoai_private_template_projected = True
        category = "template" if family == _LOGGERS[0] else "script"
        # Dynamic Script logger suffixes can be administrator-authored names.
        record.name = family
        # Only a recognized HA format supplies a numeric action position. Never
        # format the original message: names, arguments and exceptions are private.
        step = None
        failure = "unavailable"
        if (
            record.msg == "%s: Error executing script. %s for %s at pos %s: %s"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
            and isinstance(record.args[1], str)
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


def _install_record_projection() -> None:
    """One context-aware creation hook covers all handlers and future children."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_eoai_private_template_factory", False):
        return
    projector = _PrivateRenderingFilter()

    @wraps(previous)
    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        projector.filter(record)
        return record

    factory.__dict__["_eoai_private_template_factory"] = True
    logging.setLogRecordFactory(factory)


@contextmanager
def private_rendering_diagnostics() -> Iterator[None]:
    """Contain source-bearing dependency logs only during this owned execution."""
    _install_record_projection()
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
