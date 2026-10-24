"""Bound Recorder SQL reads and tool serialization before materializing large results.

Only the private read-only session/connection is wrapped. No global Recorder
function, event listener, or shared session is modified.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
import json
from typing import Any, Literal, cast

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.sql import CompoundSelect, Select, visitors
from sqlalchemy.sql.lambdas import StatementLambdaElement

from homeassistant.components.recorder import history, statistics
from homeassistant.components.recorder.util import session_scope
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError

MAX_RECORDER_ROWS = 1000
# Coarse periods consume hourly inputs before HA reduces them. Permit at least
# two full leap years of inputs, while keeping a separate finite SQL work cap.
MAX_STATISTICS_AGGREGATION_ROWS = 25_000
MAX_RECORDER_RAW_BYTES = 256_000
MAX_RECORDER_CELL_CHARACTERS = 8000
MAX_RECORDER_RESULT_BYTES = 24_000


def _limit_error() -> HomeAssistantError:
    return HomeAssistantError(
        "Recorder result exceeds the row or byte safety budget; request fewer IDs, "
        "a shorter time range, or a coarser statistics period. No partial result was returned."
    )


def _statistics_work_limit_error() -> HomeAssistantError:
    return HomeAssistantError(
        "Recorder statistics aggregation exceeds the row or byte safety budget; "
        "request fewer IDs or fewer calendar periods. Day, week, month and year "
        "queries expand to whole calendar periods. No partial result was returned."
    )


class _ReadBudget:
    def __init__(
        self, max_rows: int = MAX_RECORDER_ROWS, *, aggregation: bool = False
    ) -> None:
        self.max_rows = max_rows
        self._error = _statistics_work_limit_error if aggregation else _limit_error
        self.rows = 0
        self.bytes = 0

    def consume(self, row: Any) -> None:
        self.rows += 1
        if self.rows > self.max_rows:
            raise self._error()
        values = row if isinstance(row, tuple) or hasattr(row, "_mapping") else (row,)
        for value in values:
            if isinstance(value, str):
                if len(value) > MAX_RECORDER_CELL_CHARACTERS:
                    raise self._error()
                self.bytes += len(value.encode("utf-8"))
            elif isinstance(value, bytes):
                self.bytes += len(value)
        if self.bytes > MAX_RECORDER_RAW_BYTES:
            raise self._error()

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
        remaining = self.max_rows - self.rows + 1
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
    def __init__(self, session: Any, budget: _ReadBudget | None = None) -> None:
        self._session = session
        self._budget = budget if budget is not None else _ReadBudget()

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


def bounded_statistics(
    hass: Any,
    start_time: datetime,
    end_time: datetime | None,
    statistic_ids: set[str] | None,
    period: Literal["5minute", "hour", "day", "week", "month", "year"],
    units: dict[str, str] | None,
    types: set[Any],
) -> dict[str, Any]:
    """Bound aggregate input work separately from the final returned series."""
    aggregation = period in {"day", "week", "month", "year"}
    budget = _ReadBudget(
        MAX_STATISTICS_AGGREGATION_ROWS if aggregation else MAX_RECORDER_ROWS,
        aggregation=aggregation,
    )
    with session_scope(hass=hass, read_only=True) as session:
        result = statistics._statistics_during_period_with_session(
            hass,
            cast(Session, _BoundedSession(session, budget)),
            start_time,
            end_time,
            statistic_ids,
            period,
            units,
            types,
        )
        if sum(len(rows) for rows in result.values()) > MAX_RECORDER_ROWS:
            raise _limit_error()
        return _checked_result(result)
