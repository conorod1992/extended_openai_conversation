"""Exercise real HA Recorder SQL/conversion against a SQLite database."""

import asyncio
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.orm import Session

from custom_components.extended_openai_conversation_responses import (
    recorder_budget as budget,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from homeassistant.components.recorder import history, statistics
from homeassistant.components.recorder.db_schema import (
    StateAttributes,
    States,
    StatesMeta,
    Statistics,
    StatisticsMeta,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.table_managers.states_meta import (
    StatesMetaManager,
)
from homeassistant.components.recorder.table_managers.statistics_meta import (
    StatisticsMetaManager,
)
from homeassistant.exceptions import HomeAssistantError

START = datetime(2026, 1, 1, tzinfo=UTC)
ENTITY = "sensor.temperature"


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'recorder.db'}")
    for table in (
        StatesMeta.__table__,
        StatisticsMeta.__table__,
        StateAttributes.__table__,
        States.__table__,
        Statistics.__table__,
        StatisticsShortTerm.__table__,
    ):
        table.create(engine)
    with engine.begin() as connection:
        connection.execute(insert(StatesMeta), {"metadata_id": 1, "entity_id": ENTITY})
        connection.execute(
            insert(StatisticsMeta),
            {
                "id": 1,
                "statistic_id": ENTITY,
                "source": "recorder",
                "has_mean": True,
                "has_sum": True,
                "mean_type": 1,
            },
        )
    instance = SimpleNamespace(
        database_engine=SimpleNamespace(
            optimizer=SimpleNamespace(slow_dependent_subquery=False)
        ),
        max_bind_vars=999,
        thread_id=-1,
        schema_version=100,
    )
    instance.states_meta_manager = StatesMetaManager(instance)
    instance.statistics_meta_manager = StatisticsMetaManager(instance)
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: None))
    monkeypatch.setattr(history, "get_instance", lambda _: instance)
    monkeypatch.setattr(statistics, "get_instance", lambda _: instance)
    monkeypatch.setattr(
        history, "_get_oldest_possible_ts", lambda *_: START.timestamp() - 100
    )

    @contextmanager
    def scope(**kwargs):
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(budget, "session_scope", scope)
    yield engine, hass
    engine.dispose()


def states(engine, count, attributes="{}", offset=1):
    with engine.begin() as connection:
        connection.execute(
            insert(StateAttributes), {"attributes_id": 1, "shared_attrs": attributes}
        )
        # Avoid building a second 500,000-record Python collection in the test.
        connection.execute(
            text("""WITH RECURSIVE seq(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM seq WHERE x < :count)
            INSERT INTO states(state_id, metadata_id, state, attributes_id, last_updated_ts, last_changed_ts)
            SELECT x, 1, CAST(x AS TEXT), 1, :start+x+:offset, :start+x+:offset FROM seq"""),
            {"count": count, "start": START.timestamp(), "offset": offset - 1},
        )


def read_history(
    hass, *, include_start=False, minimal=True, no_attributes=True, end=None
):
    return budget.bounded_history(
        hass,
        START,
        end or START + timedelta(days=30),
        [ENTITY],
        None,
        include_start,
        True,
        minimal,
        no_attributes,
    )


@pytest.mark.parametrize("include_start", [False, True])
@pytest.mark.parametrize(
    "minimal,no_attributes", [(True, True), (True, False), (False, False)]
)
def test_history_keeps_real_ha_shapes_and_start_state(
    database, include_start, minimal, no_attributes
):
    engine, hass = database
    states(engine, 3, '{"friendly_name":"Room"}', offset=0)
    expected = None
    with Session(engine) as session:
        raw = history.get_significant_states_with_session(
            hass,
            session,
            START,
            START + timedelta(days=30),
            [ENTITY],
            None,
            include_start,
            True,
            minimal,
            no_attributes,
        )
        expected = [
            [item.as_dict() if hasattr(item, "as_dict") else item for item in values]
            for values in raw.values()
        ]
    assert (
        read_history(
            hass,
            include_start=include_start,
            minimal=minimal,
            no_attributes=no_attributes,
        )
        == expected
    )
    # A cached HA lambda must retain this request's time-bound parameters.
    assert read_history(hass, end=START + timedelta(seconds=1)) == []


def test_half_million_states_are_bounded_in_sql_before_conversion(
    database, monkeypatch
):
    engine, hass = database
    states(engine, 500_000)
    limits = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            limits.append((statement, parameters))

    def no_conversion(*args, **kwargs):
        pytest.fail("HA conversion must not receive an overflowing result")

    monkeypatch.setattr(history, "_sorted_states_to_dict", no_conversion)
    with pytest.raises(HomeAssistantError, match="safety budget"):
        read_history(hass)
    assert limits and all("LIMIT" in sql for sql, _ in limits)
    assert all(values[-2] <= budget.MAX_RECORDER_ROWS + 1 for _, values in limits)


def test_giant_attribute_is_capped_in_sql_before_json_decoding(database, monkeypatch):
    engine, hass = database
    states(engine, 1, json.dumps({"huge": "x" * 2_000_000}))

    def no_conversion(*args, **kwargs):
        pytest.fail("oversized attribute must fail before conversion/JSON decoding")

    monkeypatch.setattr(history, "_sorted_states_to_dict", no_conversion)
    with pytest.raises(HomeAssistantError, match="safety budget"):
        read_history(hass, no_attributes=False)


def test_cumulative_raw_bytes_and_utf8_result_budget(database):
    engine, hass = database
    states(engine, 70, json.dumps({"text": "x" * 5000}))
    with pytest.raises(HomeAssistantError, match="safety budget"):
        read_history(hass, no_attributes=False)
    assert budget._checked_result({"small": ["answer"]}) == {"small": ["answer"]}
    with pytest.raises(HomeAssistantError, match="safety budget"):
        budget._checked_result({"text": "é" * (budget.MAX_RECORDER_RESULT_BYTES // 2)})


@pytest.mark.parametrize(
    "period,table",
    [
        ("hour", Statistics),
        ("day", Statistics),
        ("month", Statistics),
        ("5minute", StatisticsShortTerm),
    ],
)
@pytest.mark.parametrize("types", [{"mean", "min", "max"}, {"change"}])
def test_real_statistics_aggregation_and_change_queries(database, period, table, types):
    engine, hass = database
    with engine.begin() as connection:
        connection.execute(
            insert(table),
            [
                dict(
                    id=i + 1,
                    metadata_id=1,
                    start_ts=START.timestamp() + (i - 1) * 3600,
                    mean=10 + i,
                    min=5 + i,
                    max=15 + i,
                    mean_weight=3600,
                    state=i,
                    sum=i,
                )
                for i in range(5)
            ],
        )
    args = (START, START + timedelta(days=1), {ENTITY}, period, None, types)
    with Session(engine) as session:
        expected = statistics._statistics_during_period_with_session(
            hass, session, *args
        )
    assert budget.bounded_statistics(hass, *args) == expected


def test_statistics_cardinality_is_bounded_before_aggregation(database, monkeypatch):
    engine, hass = database
    with engine.begin() as connection:
        connection.execute(
            insert(Statistics),
            [
                dict(
                    id=i + 1,
                    metadata_id=1,
                    start_ts=START.timestamp() + i * 3600,
                    mean=i,
                    mean_weight=3600,
                )
                for i in range(3000)
            ],
        )

    def no_aggregation(*args, **kwargs):
        pytest.fail("oversized raw data must fail before aggregation")

    monkeypatch.setattr(statistics, "_sorted_statistics_to_dict", no_aggregation)
    with pytest.raises(HomeAssistantError, match="safety budget"):
        budget.bounded_statistics(
            hass, START, START + timedelta(days=300), {ENTITY}, "month", None, {"mean"}
        )


async def test_native_history_runs_query_conversion_and_budget_in_executor(
    database, monkeypatch
):
    engine, hass = database
    hass.states.get = lambda _: SimpleNamespace(state="on")
    states(engine, 50)
    gaps = []

    async def beat():
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    async def executor(func, *args):
        return await asyncio.to_thread(func, *args)

    instance = SimpleNamespace(async_add_executor_job=executor)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.native.recorder.get_instance",
        lambda _: instance,
    )
    original = budget._checked_result

    def slow_serialization(value):
        time.sleep(0.15)
        return original(value)

    monkeypatch.setattr(budget, "_checked_result", slow_serialization)
    heartbeat = asyncio.create_task(beat())
    await asyncio.sleep(0.01)
    try:
        result = await NativeFunction().get_history(
            hass,
            {},
            {
                "entity_ids": [ENTITY],
                "start_time": START.isoformat(),
                "end_time": (START + timedelta(days=1)).isoformat(),
            },
            None,
            [{"entity_id": ENTITY}],
        )
        assert result
        await asyncio.sleep(0.01)
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    assert max(gaps) < 0.1


def test_budget_is_shared_across_helper_reads_and_preserves_smaller_sql_limits(
    database,
):
    engine, _ = database
    states(engine, 1200)
    with Session(engine) as session:
        guarded = budget._BoundedSession(session)
        assert (
            len(guarded.connection().execute(select(States.state).limit(5)).fetchall())
            == 5
        )
        with pytest.raises(HomeAssistantError, match="safety budget"):
            guarded.execute(select(States.state)).yield_per(10).all()
        assert guarded._budget.rows == budget.MAX_RECORDER_ROWS + 1
    with Session(engine) as session:
        guarded = budget._BoundedSession(session)
        assert guarded.execute(select(States.state).limit(1)).scalars().first() == "1"
        with pytest.raises(HomeAssistantError, match="SELECT"):
            guarded.execute(insert(States).values(state_id=2000))


def test_bounded_serialized_history_rejects_large_output_even_with_few_rows(database):
    engine, hass = database
    states(engine, 20, json.dumps({"text": "x" * 1500}))
    with pytest.raises(HomeAssistantError, match="safety budget"):
        read_history(hass, minimal=False, no_attributes=False)
