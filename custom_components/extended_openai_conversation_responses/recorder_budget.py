"""Bound Recorder SQL reads and tool serialization before materializing large results.

Only the private read-only session/connection is wrapped. No global Recorder
function, event listener, or shared session is modified.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
from typing import Any, cast

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.sql import CompoundSelect, Select, visitors
from sqlalchemy.sql.lambdas import StatementLambdaElement

from homeassistant.components.recorder import history, statistics
from homeassistant.components.recorder.util import session_scope
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError

MAX_RECORDER_ROWS = 1000
MAX_RECORDER_RAW_BYTES = 256_000
MAX_RECORDER_CELL_CHARACTERS = 8000
MAX_RECORDER_RESULT_BYTES = 24_000


def _limit_error() -> HomeAssistantError:
    return HomeAssistantError(
        "Recorder result exceeds the row or byte safety budget; request fewer IDs, "
        "a shorter time range, or a coarser statistics period. No partial result was returned."
    )


class _ReadBudget:
    def __init__(self) -> None:
        self.rows = 0
        self.bytes = 0

    def consume(self, row: Any) -> None:
        self.rows += 1
        if self.rows > MAX_RECORDER_ROWS:
            raise _limit_error()
        values = row if isinstance(row, tuple) or hasattr(row, "_mapping") else (row,)
        for value in values:
            if isinstance(value, str):
                if len(value) > MAX_RECORDER_CELL_CHARACTERS:
                    raise _limit_error()
                self.bytes += len(value.encode("utf-8"))
            elif isinstance(value, bytes):
                self.bytes += len(value)
        if self.bytes > MAX_RECORDER_RAW_BYTES:
            raise _limit_error()

    def statement(self, statement: Any) -> Any:
        # Resolve a cached HA lambda without mutating its shared statement.
        if isinstance(statement, StatementLambdaElement):
            statement = statement._resolved
        if not getattr(statement, "is_select", False):
            raise HomeAssistantError("Recorder tool reads require a SELECT statement")

        def bounded_column(element: Any, **kw: Any) -> Any:
            column = element
            table = getattr(column, "table", None)
            if getattr(table, "name", None) in {
                "states",
                "state_attributes",
            } and getattr(column, "name", None) in {
                "state",
                "attributes",
                "shared_attrs",
            }:
                # Bound DB-driver allocations, including an individual large JSON
                # attribute cell. The sentinel character makes overflow detectable
                # before HA attempts to parse a truncated JSON value.
                return func.substr(column, 1, MAX_RECORDER_CELL_CHARACTERS + 1).label(
                    column.name
                )
            return None

        bounded = cast(
            Select[Any] | CompoundSelect,
            visitors.replacement_traverse(statement, {}, bounded_column),
        )
        remaining = MAX_RECORDER_ROWS - self.rows + 1
        previous = getattr(getattr(statement, "_limit_clause", None), "value", None)
        if isinstance(previous, int):
            remaining = min(remaining, previous)
        return bounded.limit(remaining)


class _BoundedResult:
    def __init__(self, result: Any, budget: _ReadBudget) -> None:
        self._result = result
        self._budget = budget

    def __iter__(self) -> Iterator[Any]:
        try:
            for row in self._result:
                self._budget.consume(row)
                yield row
        finally:
            self._result.close()

    def all(self) -> list[Any]:
        return list(self)

    def fetchall(self) -> list[Any]:
        return self.all()

    def yield_per(self, count: int) -> _BoundedResult:
        self._result = self._result.yield_per(min(count, 64))
        return self

    def scalars(self, *args: Any, **kwargs: Any) -> _BoundedResult:
        return _BoundedResult(self._result.scalars(*args, **kwargs), self._budget)

    def first(self) -> Any:
        return next(iter(self), None)


class _BoundedConnection:
    def __init__(self, connection: Any, budget: _ReadBudget) -> None:
        self._connection = connection
        self._budget = budget

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _BoundedResult:
        return _BoundedResult(
            self._connection.execute(
                self._budget.statement(statement), *args, **kwargs
            ),
            self._budget,
        )


class _BoundedSession:
    def __init__(self, session: Any) -> None:
        self._session = session
        self._budget = _ReadBudget()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def connection(self, *args: Any, **kwargs: Any) -> _BoundedConnection:
        return _BoundedConnection(
            self._session.connection(*args, **kwargs), self._budget
        )

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _BoundedResult:
        return _BoundedResult(
            self._session.execute(self._budget.statement(statement), *args, **kwargs),
            self._budget,
        )


def _checked_result[T](value: T) -> T:
    size = 0
    for part in json.JSONEncoder(
        ensure_ascii=False, separators=(",", ":"), default=str
    ).iterencode(value):
        size += len(part.encode("utf-8"))
        if size > MAX_RECORDER_RESULT_BYTES:
            raise _limit_error()
    return value


def bounded_history(hass: Any, *args: Any) -> list[list[dict[str, Any]]]:
    """Read, convert and validate History entirely in Recorder's executor."""
    with session_scope(hass=hass, read_only=True) as session:
        result = history.get_significant_states_with_session(
            hass, cast(Session, _BoundedSession(session)), *args
        )
        rows = [
            [item.as_dict() if isinstance(item, State) else item for item in values]
            for values in result.values()
        ]
        return _checked_result(rows)


def bounded_statistics(hass: Any, *args: Any) -> dict[str, Any]:
    """Preserve HA aggregation/units while bounding its underlying raw reads."""
    with session_scope(hass=hass, read_only=True) as session:
        result = statistics._statistics_during_period_with_session(
            hass, cast(Session, _BoundedSession(session)), *args
        )
        return _checked_result(result)
