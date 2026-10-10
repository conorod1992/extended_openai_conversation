"""Execute the published fixed-query examples against real SQLite history."""

from datetime import datetime
from pathlib import Path
import re
import sqlite3

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    SqliteFunction,
)


def examples():
    text = (
        Path(__file__).parents[1] / "examples/function/sqlite/README.md"
    ).read_text()
    return {
        tool["spec"]["name"]: tool
        for block in re.findall(r"```yaml\n(.*?)```", text, re.S)
        for tool in yaml.safe_load(block)
    }


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE states_meta (metadata_id INTEGER, entity_id TEXT); CREATE TABLE states (state_id INTEGER, metadata_id INTEGER, old_state_id INTEGER, state TEXT, last_updated_ts REAL);"
        )
        conn.executemany(
            "INSERT INTO states_meta VALUES (?, ?)",
            [(1, "light.public"), (2, "light.private")],
        )

        def ts(hour):
            return datetime(2026, 1, 1, hour).timestamp()

        conn.executemany(
            "INSERT INTO states VALUES (?, ?, ?, ?, ?)",
            [
                (1, 1, None, "on", ts(0)),
                (2, 1, 1, "off", ts(3)),
                (3, 2, None, "private", ts(0)),
                (4, 2, 3, "secret", ts(1)),
            ],
        )
    return path


async def run(hass, history, step, arguments):
    hass.config.legacy_templates = False
    function = SqliteFunction()
    config = {**step, "db_url": str(history)}
    return await function.execute(
        hass, config, arguments, None, [{"entity_id": "light.public"}]
    )


async def test_reserved_exposure_context_cannot_be_overridden(hass, history):
    result = await run(
        hass,
        history,
        {
            "query": "SELECT entity_id FROM states_meta WHERE entity_id = :entity",
            "parameters": {"entity": "{{ exposed_entities[0].entity_id }}"},
        },
        {"exposed_entities": [{"entity_id": "light.private"}]},
    )
    assert result == [{"entity_id": "light.public"}]


async def test_bound_values_cannot_inject_sql(hass, history):
    result = await run(
        hass,
        history,
        {
            "query": "SELECT entity_id FROM states_meta WHERE entity_id = :entity",
            "parameters": {"entity": "{{ entity_id }}"},
        },
        {"entity_id": "light.public' OR 1=1 --"},
    )
    assert result == []


@pytest.mark.parametrize(
    "value", ["2026-01-01 00:00:00' OR 1=1 --", "2026-1-1 00:00:00", "invalid", None]
)
async def test_history_examples_reject_invalid_dates(hass, history, value):
    args = {
        "entity_id": "light.public",
        "start_datetime": value,
        "end_datetime": "2026-01-01 02:00:00",
        "order": "asc",
        "page": 1,
        "limit": 10,
    }
    for step in examples()["get_states_between"]["function"]["sequence"][:2]:
        with pytest.raises(HomeAssistantError):
            await run(hass, history, step, args)


@pytest.mark.parametrize(("state", "expected"), [("on", 3600), ("off", 0)])
async def test_duration_seeds_state_before_window(hass, history, state, expected):
    step = examples()["get_total_time_of_entity_state"]["function"]["sequence"][0]
    result = await run(
        hass,
        history,
        step,
        {
            "entity_id": "light.public",
            "state": state,
            "start_datetime": "2026-01-01 01:00:00",
            "end_datetime": "2026-01-01 02:00:00",
        },
    )
    assert result == [{"total_time_in_sec": expected}]


async def test_duration_missing_initial_history_is_unknown(hass, history):
    step = examples()["get_total_time_of_entity_state"]["function"]["sequence"][0]
    result = await run(
        hass,
        history,
        step,
        {
            "entity_id": "light.public",
            "state": "on",
            "start_datetime": "2025-12-31 22:00:00",
            "end_datetime": "2025-12-31 23:00:00",
        },
    )
    assert result == [{"total_time_in_sec": None}]


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("02:00:00", "04:00:00", 3600),
        ("03:00:00", "04:00:00", 0),
        ("02:00:00", "03:00:00", 3600),
        ("03:00:00", "03:00:00", None),
    ],
)
async def test_duration_half_open_boundaries(hass, history, start, end, expected):
    step = examples()["get_total_time_of_entity_state"]["function"]["sequence"][0]
    result = await run(
        hass,
        history,
        step,
        {
            "entity_id": "light.public",
            "state": "on",
            "start_datetime": "2026-01-01 " + start,
            "end_datetime": "2026-01-01 " + end,
        },
    )
    assert result == [{"total_time_in_sec": expected}]


async def test_both_history_queries_enforce_exposure_and_bind_state(hass, history):
    args = {
        "entity_id": "light.public",
        "state": "off' OR 1=1 --",
        "start_datetime": "2026-01-01 00:00:00",
        "end_datetime": "2026-01-01 04:00:00",
        "order": "asc",
        "page": 1,
        "limit": 10,
    }
    steps = examples()["get_states_between"]["function"]["sequence"][:2]
    assert await run(hass, history, steps[0], args) == []
    assert await run(hass, history, steps[1], args) == {"count": 0}
    for step in steps:
        with pytest.raises(HomeAssistantError):
            await run(
                hass,
                history,
                step,
                {**args, "entity_id": "light.private", "is_exposed": True},
            )


@pytest.mark.parametrize(
    ("key", "value"),
    [("order", "asc; SELECT 1"), ("state_operator", "= 'on' OR 1=1 --")],
)
async def test_sql_syntax_choices_are_whitelisted(hass, history, key, value):
    step = examples()["get_states_between"]["function"]["sequence"][0]
    args = {
        "entity_id": "light.public",
        "start_datetime": "2026-01-01 00:00:00",
        "end_datetime": "2026-01-01 04:00:00",
        "order": "asc",
        "page": 1,
        "limit": 10,
    }
    with pytest.raises(HomeAssistantError):
        await run(hass, history, step, {**args, key: value})


@pytest.mark.parametrize("value", [[1], {"nested": 1}])
async def test_bound_containers_rejected(hass, history, value):
    with pytest.raises(HomeAssistantError, match="scalar"):
        await run(
            hass,
            history,
            {"query": "SELECT :value", "parameters": {"value": "{{ value }}"}},
            {"value": value},
        )
