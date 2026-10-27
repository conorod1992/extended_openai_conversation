"""Streaming Recorder metadata must preserve bounded row consumption."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from custom_components.extended_openai_conversation_responses import recorder_budget
from homeassistant.components.recorder.db_schema import StateAttributes, States
from homeassistant.exceptions import HomeAssistantError
from tests.test_recorder_budget import database as database, states


def test_column_metadata_does_not_consume_or_bypass_row_budget(database):
    engine, _hass = database
    states(engine, 2)
    work = recorder_budget._ReadBudget(max_rows=1)
    with Session(engine) as session:
        cursor = session.execute(
            select(States.state, States.last_updated_ts).order_by(States.state_id)
        )
        result = recorder_budget._BoundedResult(cursor, work)
        keys = result.keys()
        assert type(keys) is type(cursor.keys())
        assert list(keys) == ["state", "last_updated_ts"]
        assert (work.rows, work.bytes) == (0, 0)
        rows = iter(result)
        assert next(rows).state == "1"
        assert (work.rows, work.bytes) == (1, 1)
        assert list(result.keys()) == ["state", "last_updated_ts"]
        assert (work.rows, work.bytes) == (1, 1)
        with pytest.raises(HomeAssistantError, match="safety budget"):
            next(rows)
        assert cursor.closed
        assert list(result.keys()) == ["state", "last_updated_ts"]
        assert (work.rows, work.bytes) == (2, 1)


def test_column_metadata_does_not_bypass_cell_budget_or_cursor_cleanup(database):
    engine, _hass = database
    states(
        engine, 1, attributes="x" * (recorder_budget.MAX_RECORDER_CELL_CHARACTERS + 1)
    )
    work = recorder_budget._ReadBudget()
    with Session(engine) as session:
        cursor = session.execute(select(StateAttributes.shared_attrs))
        result = recorder_budget._BoundedResult(cursor, work)
        assert list(result.keys()) == ["shared_attrs"]
        assert (work.rows, work.bytes) == (0, 0)
        with pytest.raises(HomeAssistantError, match="safety budget"):
            result.all()
        assert cursor.closed
        assert list(result.keys()) == ["shared_attrs"]
