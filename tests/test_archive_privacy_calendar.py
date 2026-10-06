"""Archive activity and local calendar contracts."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_archive as module,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_archive import FakeArchiveStorage


@pytest.fixture(autouse=True)
def restore_time_zone(request: pytest.FixtureRequest):
    """Restore HA's global zone before its cleanup assertion runs."""
    # HA-dev's compatibility lane disables the HA plugin. When it is loaded,
    # request its autouse cleanup fixture first to preserve teardown ordering.
    if "verify_cleanup" in request.fixturenames:
        request.getfixturevalue("verify_cleanup")
    prior = module.dt_util.DEFAULT_TIME_ZONE
    yield
    module.dt_util.set_default_time_zone(prior)


async def test_private_activity_survives_multiple_timeouts_and_restart(monkeypatch):
    now = datetime.fromisoformat("2026-10-03T12:00:00+00:00")
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: now)
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
    scope = user_scope("alice", source="test")

    async def begin():
        return await archive.async_begin_session(
            "key",
            scope,
            "conversation",
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )

    original = await begin()
    await archive.async_make_private(original.session_id)
    partition_writes = storage.partition_save_count
    for index in range(18):
        now += timedelta(minutes=10)
        session = await begin()
        assert session.session_id == original.session_id
        assert session.retention_state == "private"
        assert (
            await archive.async_record_turn(
                session.session_id,
                run_id=None,
                user_text="private secret",
                assistant_text="private reply",
                successful=True,
            )
            is None
        )
        if index == 8:
            archive = ConversationArchive(storage, "agent")
            await archive.async_initialize()
    assert storage.partition_save_count == partition_writes
    assert "private secret" not in repr(storage.metadata)
    assert session.last_message_at == original.last_message_at
    resumed = await archive.async_resume_saving(
        "key",
        session.session_id,
        scope,
        shared_archive_enabled=False,
    )
    assert resumed.retention_state == "retained"
    await archive.async_make_private(resumed.session_id)
    now += timedelta(minutes=31)
    assert (await begin()).retention_state == "retained"


async def test_slow_private_turn_refreshes_activity_at_completion(monkeypatch):
    now = datetime.fromisoformat("2026-10-03T12:00:00+00:00")
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: now)
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
    scope = user_scope("alice", source="test")

    async def begin():
        return await archive.async_begin_session(
            "key",
            scope,
            "conversation",
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=1,
        )

    session = await begin()
    await archive.async_make_private(session.session_id)
    now += timedelta(seconds=10)
    await begin()
    now += timedelta(seconds=90)
    writes = storage.partition_save_count
    assert (
        await archive.async_record_turn(
            session.session_id,
            run_id=None,
            user_text="slow private secret",
            assistant_text="reply",
            successful=True,
        )
        is None
    )
    now += timedelta(seconds=10)
    assert (await begin()).session_id == session.session_id
    restarted = ConversationArchive(storage, "agent")
    await restarted.async_initialize()
    archive = restarted
    assert (await begin()).session_id == session.session_id
    assert archive.active_session("key").retention_state == "private"
    assert storage.partition_save_count == writes
    assert "slow private secret" not in repr(storage.metadata)


@pytest.mark.parametrize(
    "zone,day,hours",
    [
        ("UTC", "2026-10-03", 24),
        ("Europe/Dublin", "2026-10-03", 24),
        ("America/New_York", "2026-10-03", 24),
        ("Europe/Dublin", "2026-03-29", 23),
        ("Europe/Dublin", "2026-10-25", 25),
        ("America/New_York", "2026-11-01", 25),
    ],
)
async def test_search_and_delete_agree_at_local_midnight(
    hass, monkeypatch, zone, day, hours
):
    module.dt_util.set_default_time_zone(ZoneInfo(zone))
    start, end = module._local_date_bounds(day, day, ZoneInfo(zone))
    assert (end - start).total_seconds() == hours * 3600
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
    scope = user_scope("alice", source="test")
    selected = []
    for index, timestamp in enumerate(
        [
            start - timedelta(seconds=1),
            start,
            end - timedelta(seconds=1),
            end,
        ]
    ):
        monkeypatch.setattr(
            module.dt_util, "utcnow", lambda timestamp=timestamp: timestamp
        )
        session = await archive.async_begin_session(
            str(index),
            scope,
            str(index),
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )
        await archive.async_record_turn(
            session.session_id,
            run_id=None,
            user_text="calendar marker",
            assistant_text="reply",
            successful=True,
        )
        if index in (1, 2):
            selected.append(session.session_id)
    result = await archive.async_search(
        scope.scope_id, "calendar", start_date=day, end_date=day
    )
    assert {row["session_id"] for row in result["results"]} == set(selected)
    assert {row["date"] for row in result["results"]} == {day}
    assert await archive.async_delete_date_range(
        scope.scope_id, day, day, confirm=True
    ) == {
        "deleted_sessions": 2,
        "deleted_turns": 2,
    }


async def test_delete_uses_last_retained_turn_not_first_or_activity(hass, monkeypatch):
    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent")
    await archive.async_initialize()
    hass.config.set_time_zone("UTC")
    now = datetime.fromisoformat("2026-10-02T23:55:00+00:00")
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: now)
    scope = user_scope("alice", source="test")
    session = await archive.async_begin_session(
        "key",
        scope,
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    for minute in (0, 10):
        now += timedelta(minutes=minute)
        await archive.async_record_turn(
            session.session_id,
            run_id=None,
            user_text="calendar",
            assistant_text="reply",
            successful=True,
        )
    assert await archive.async_delete_date_range(
        scope.scope_id, "2026-10-02", "2026-10-02", confirm=True
    ) == {"deleted_sessions": 0, "deleted_turns": 0}
    assert await archive.async_delete_date_range(
        scope.scope_id, "2026-10-03", "2026-10-03", confirm=True
    ) == {"deleted_sessions": 1, "deleted_turns": 2}
