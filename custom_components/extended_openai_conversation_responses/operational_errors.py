"""Small, payload-free diagnostics for handled operational failures."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import errno
from functools import wraps
from json import JSONDecodeError
import logging
from pathlib import Path
import traceback
from typing import Any

import yaml

_LOGGER_NAMESPACE = "custom_components.extended_openai_conversation_responses"

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
    if number is None:
        return "storage operation failed"
    return {
        errno.ENOSPC: "storage is full; free disk space",
        errno.EACCES: "permission denied; check Home Assistant storage permissions",
        errno.EPERM: "permission denied; check Home Assistant storage permissions",
        errno.EROFS: "filesystem is read-only; restore writable Home Assistant storage",
    }.get(number, "storage operation failed")


@dataclass(frozen=True, slots=True)
class FailureDiagnostic:
    """An operational error snapshot with no source, exception or input owner.

    Parser messages, marks' buffers/names, JSON documents and exception prose
    are deliberately absent. Only bounded coordinates and fixed categories
    cross the logging boundary. Authorised editors may still show validation
    detail directly to the administrator who supplied the input.
    """

    category: str
    error_type: str
    line: int | None = None
    column: int | None = None
    number: int | None = None

    def context(self) -> str:
        position = (
            f" line={self.line} column={self.column}"
            if self.line is not None and self.column is not None
            else ""
        )
        storage = (
            f" ({storage_failure_reason(self.number)})"
            if self.number is not None
            else ""
        )
        return f"category={self.category} errno={self.number}{storage}{position}"

    def summary(self) -> str:
        return f"error_type={self.error_type} {self.context()}"


def failure_diagnostic(error: BaseException) -> FailureDiagnostic:
    """Classify without formatting error objects or retaining private marks."""
    category = "backend"
    line = column = number = None
    if isinstance(error, yaml.YAMLError):
        category = "yaml_parse"
        mark = getattr(error, "problem_mark", None)
        if mark is not None:
            line, column = getattr(mark, "line", None), getattr(mark, "column", None)
            line = line + 1 if type(line) is int and 0 <= line < 10**9 else None
            column = column + 1 if type(column) is int and 0 <= column < 10**9 else None
    elif isinstance(error, JSONDecodeError):
        category = "json_parse"
        line = (
            error.lineno
            if type(error.lineno) is int and 0 < error.lineno <= 10**9
            else None
        )
        column = (
            error.colno
            if type(error.colno) is int and 0 < error.colno <= 10**9
            else None
        )
    elif isinstance(error, OSError):
        category = "storage"
        number = error.errno if type(error.errno) is int else None
    return FailureDiagnostic(category, type(error).__name__, line, column, number)


def _stack_locations(error: BaseException) -> str:
    """Keep causal types and code locations, excluding lines, locals and prose."""
    locations = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        frames = traceback.extract_tb(current.__traceback__, limit=16)
        locations.append(
            type(current).__name__
            + ":"
            + (
                " -> ".join(
                    f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                    for frame in frames
                )
                or "<no stack>"
            )
        )
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return " | ".join(locations)


def _project_argument(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, BaseException):
        return failure_diagnostic(value).summary()
    if depth >= 12 and isinstance(value, (tuple, list, dict)):
        return "<nested operational argument>"
    if isinstance(value, tuple):
        return tuple(_project_argument(item, depth=depth + 1) for item in value)
    if isinstance(value, list):
        return [_project_argument(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        return {
            _project_argument(key, depth=depth + 1): _project_argument(
                item, depth=depth + 1
            )
            for key, item in value.items()
        }
    return value


def install_source_free_diagnostics() -> None:
    """Project EOAI exception records before any handler can format private input.

    This chains existing factories and covers future descendant loggers. It is
    intentionally namespace-local; scoped HA Template/Script/Scrape projection
    remains responsible for their dependency-owned records. Call sites must
    pass errors as objects, never preformat exception prose into log strings.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_eoai_operational_diagnostic_factory", False):
        return

    @wraps(previous)
    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if record.name != _LOGGER_NAMESPACE and not record.name.startswith(
            _LOGGER_NAMESPACE + "."
        ):
            return record
        record.msg = _project_argument(record.msg)
        record.args = _project_argument(record.args)
        if record.exc_info and isinstance(record.exc_info[1], BaseException):
            error = record.exc_info[1]
            record._eoai_error_type = type(error).__name__
            record.msg = (
                record.getMessage()
                + "; "
                + failure_diagnostic(error).summary()
                + "; locations="
                + _stack_locations(error)
            )
            record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return record

    factory.__dict__["_eoai_operational_diagnostic_factory"] = True
    logging.setLogRecordFactory(factory)


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
    install_source_free_diagnostics()
    diagnostic = failure_diagnostic(error)
    logger.log(
        level,
        "%s; error_type=%s %s",
        operation,
        diagnostic.error_type,
        diagnostic.context(),
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


install_source_free_diagnostics()
