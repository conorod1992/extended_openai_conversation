"""Source-free HA diagnostics within EOAI-owned backend tasks.

HA can log template source or complete Scrape responses before our boundaries.
Project at record creation: parent logger filters do not cover child loggers.
Context-local activation leaves unrelated HA tasks' diagnostics intact, without
changing logger levels or suppressing records. Existing record factories chain.
"""

from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
from typing import Any, cast

_PRIVATE_RENDERING: ContextVar[bool] = ContextVar(
    "eoai_private_rendering", default=False
)
_SCRAPE_LOGGER = "homeassistant.components.scrape.coordinator"
_LOGGERS = (
    "homeassistant.helpers.template",
    "homeassistant.helpers.script",
    "custom_components.extended_openai_conversation_responses.functions.script",
    _SCRAPE_LOGGER,
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
        if family == _SCRAPE_LOGGER:
            _project_scrape_record(record)
            return True
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
            else getattr(record, "_eoai_error_type", failure)
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


def _project_scrape_record(record: logging.LogRecord) -> None:
    """Keep coordinator health information without formatting response content."""
    record.name = _SCRAPE_LOGGER
    args = record.args
    if (
        record.msg == "Finished fetching %s data in %.3f seconds (success: %s)"
        and isinstance(args, tuple)
        and len(args) == 3
        and type(args[1]) in (int, float)
        # Bound before formatting: even exact integers can overflow %.3f's
        # float conversion. Comparisons also reject NaN and infinities safely.
        and 0 <= cast(float, args[1]) <= 31_536_000
        and type(args[2]) is bool
    ):
        record.msg = "EOAI private scrape refresh; duration=%.3f seconds success=%s"
        record.args = (args[1], args[2])
    else:
        error_type = (
            type(record.exc_info[1]).__name__
            if record.exc_info and record.exc_info[1] is not None
            else "unavailable"
        )
        if (
            record.msg
            in (
                "Error fetching %s data: %s",
                "Error requesting %s data: %s",
                "Authentication failed while fetching %s data: %s",
                "Config entry setup failed while fetching %s data: %s",
            )
            and isinstance(args, tuple)
            and len(args) == 2
            and isinstance(args[1], BaseException)
        ):
            error_type = type(args[1]).__name__
        elif record.msg == "Timeout fetching %s data":
            error_type = "timeout"
        elif record.msg == "Fetching %s data recovered":
            error_type = "recovered"
        record.msg = (
            "EOAI private scrape diagnostic; error_type=%s. "
            "Response omitted; check Function Tool endpoint and extraction settings."
        )
        record.args = (error_type,)
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None


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
