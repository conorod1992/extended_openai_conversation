"""Real Home Assistant acceptance tests for bounded management history."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ArchiveSession,
    ArchiveTurn,
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    WS_COMMAND,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageRun,
    UsageTotals,
    _empty_day,
    async_get_usage,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_management_backend_acceptance import (
    ADMIN_ID,
    _admin_client,
    _conversation_subentry,
    _entry,
    _management_call,
    _setup_entry,
)

ADMIN_SCOPE_ID = f"user:{ADMIN_ID}"


def _session(
    subentry_id: str,
    session_id: str,
    last_message_at: str,
    *,
    turn_count: int = 0,
) -> ArchiveSession:
    return ArchiveSession(
        session_id=session_id,
        home_assistant_conversation_id=f"ha-{session_id}",
        agent_subentry_id=subentry_id,
        scope_id=ADMIN_SCOPE_ID,
        scope_type="user",
        scope_source="user",
        source_device_id=None,
        started_at=last_message_at,
        last_message_at=last_message_at,
        title=f"Session {session_id}",
        turn_count=turn_count,
        retention_state="retained",
    )


async def _seed_archive(
    hass: HomeAssistant,
    entry: Any,
    sessions: list[ArchiveSession],
    turns: list[ArchiveTurn] | None = None,
) -> None:
    subentry = _conversation_subentry(entry)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    await archive.async_replace_backup(sessions, turns or [])


async def _raw_management_call(
    client: Any,
    *,
    entry: Any,
    section: str,
    action: str,
    **payload: Any,
) -> dict[str, Any]:
    subentry = _conversation_subentry(entry)
    await client.send_json_auto_id(
        {
            "type": WS_COMMAND,
            "section": section,
            "action": action,
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            **payload,
        }
    )
    return await client.receive_json()


@pytest.mark.asyncio
async def test_history_command_empty_returns_stable_empty_payload(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Empty History")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    conversations = await _management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
        limit=5,
        offset=0,
    )
    assert conversations == {
        "sessions": [],
        "offset": 0,
        "limit": 5,
        "returned": 0,
        "has_more": False,
        "next_offset": None,
        "total": 0,
    }

    usage = await _management_call(
        client,
        entry=entry,
        section="usage",
        action="runs",
        limit=5,
        offset=0,
    )
    assert usage == {
        "runs": [],
        "offset": 0,
        "limit": 5,
        "returned": 0,
        "has_more": False,
        "next_offset": None,
    }


@pytest.mark.asyncio
async def test_history_command_orders_entries_by_current_contract(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("History Ordering")
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    await _seed_archive(
        hass,
        entry,
        [
            _session(subentry.subentry_id, "middle", "2026-09-10T12:00:00+00:00"),
            _session(subentry.subentry_id, "old", "2026-09-09T12:00:00+00:00"),
            _session(subentry.subentry_id, "new-b", "2026-09-11T12:00:00+00:00"),
            _session(subentry.subentry_id, "new-a", "2026-09-11T12:00:00+00:00"),
        ],
    )
    client = await _admin_client(hass, hass_ws_client)

    result = await _management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
        limit=10,
        offset=0,
    )

    assert [item["session_id"] for item in result["sessions"]] == [
        "new-b",
        "new-a",
        "middle",
        "old",
    ]
    assert result["total"] == 4


@pytest.mark.asyncio
async def test_history_command_pagination_has_no_overlap_or_gap(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("History Paging")
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    expected = ["s5", "s4", "s3", "s2", "s1"]
    await _seed_archive(
        hass,
        entry,
        [
            _session(
                subentry.subentry_id,
                f"s{index}",
                f"2026-09-{index + 6:02d}T12:00:00+00:00",
            )
            for index in range(1, 6)
        ],
    )
    client = await _admin_client(hass, hass_ws_client)

    first = await _management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
        limit=2,
        offset=0,
    )
    second = await _management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
        limit=2,
        offset=first["next_offset"],
    )
    third = await _management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
        limit=2,
        offset=second["next_offset"],
    )

    actual = [
        item["session_id"]
        for page in (first, second, third)
        for item in page["sessions"]
    ]
    assert actual == expected
    assert len(actual) == len(set(actual))
    assert first["has_more"] is True
    assert second["has_more"] is True
    assert third["has_more"] is False
    assert third["next_offset"] is None


@pytest.mark.asyncio
async def test_history_search_date_range_is_inclusive_at_both_bounds(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("History Range")
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    sessions = [
        _session(
            subentry.subentry_id,
            "range",
            "2026-09-11T20:00:00+00:00",
            turn_count=3,
        )
    ]
    turns = [
        ArchiveTurn(
            turn_id="before",
            session_id="range",
            run_id=None,
            timestamp="2026-09-09T23:59:59+00:00",
            user_text="boundary marker before",
            assistant_text="before",
            successful=True,
        ),
        ArchiveTurn(
            turn_id="start",
            session_id="range",
            run_id=None,
            timestamp="2026-09-10T00:00:00+00:00",
            user_text="boundary marker start",
            assistant_text="start",
            successful=True,
        ),
        ArchiveTurn(
            turn_id="end",
            session_id="range",
            run_id=None,
            timestamp="2026-09-11T23:59:59+01:00",
            user_text="boundary marker end",
            assistant_text="end",
            successful=True,
        ),
    ]
    await _seed_archive(hass, entry, sessions, turns)
    client = await _admin_client(hass, hass_ws_client)

    result = await _management_call(
        client,
        entry=entry,
        section="conversations",
        action="search",
        query="boundary marker",
        start_date="2026-09-10",
        end_date="2026-09-11",
        limit=10,
        offset=0,
    )

    assert {item["turn_id"] for item in result["results"]} == {"start", "end"}
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_usage_daily_range_and_runs_are_bounded_through_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Usage History")
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)

    daily: dict[str, dict[str, Any]] = {}
    for date, tokens in (
        ("2026-09-09", 9),
        ("2026-09-10", 10),
        ("2026-09-11", 11),
        ("2026-09-12", 12),
    ):
        day = _empty_day(date)
        day["total_tokens"] = tokens
        daily[date] = day
    runs = [
        UsageRun(
            run_id=f"run-{day}",
            started_at=f"2026-09-{day:02d}T12:00:00+00:00",
            completed_at=f"2026-09-{day:02d}T12:00:01+00:00",
            duration_ms=1000,
            agent_subentry_id=subentry.subentry_id,
            home_assistant_conversation_id=None,
            source_device_id=None,
            successful=successful,
        )
        for day, successful in ((9, True), (10, False), (11, True))
    ]
    await usage.async_replace_backup(UsageTotals(), daily, [], runs)
    client = await _admin_client(hass, hass_ws_client)

    days = await _management_call(
        client,
        entry=entry,
        section="usage",
        action="daily",
        start_date="2026-09-10",
        end_date="2026-09-11",
        limit=10,
        offset=0,
    )
    assert [item["date"] for item in days["days"]] == ["2026-09-10", "2026-09-11"]

    successful_runs = await _management_call(
        client,
        entry=entry,
        section="usage",
        action="runs",
        successful=True,
        limit=10,
        offset=0,
    )
    assert [item["run_id"] for item in successful_runs["runs"]] == [
        "run-11",
        "run-9",
    ]


@pytest.mark.asyncio
async def test_usage_requests_requires_run_id_through_websocket(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    entry = _entry("Usage Request Validation")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)

    response = await _raw_management_call(
        client,
        entry=entry,
        section="usage",
        action="requests",
    )

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_request"
    assert response["error"]["message"] == "run_id is required"


@pytest.mark.asyncio
async def test_history_runtime_unavailable_returns_controlled_websocket_error(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry("Unavailable History")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    getter = AsyncMock(side_effect=HomeAssistantError("archive runtime unavailable"))
    monkeypatch.setattr(management_ui, "async_get_archive", getter)

    response = await _raw_management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
    )

    assert response["success"] is False
    assert response["error"] == {
        "code": "invalid_request",
        "message": "archive runtime unavailable",
    }
    getter.assert_awaited_once()


@pytest.mark.asyncio
async def test_history_query_failure_translates_to_websocket_error_without_mutation(
    hass: HomeAssistant,
    hass_ws_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry("History Failure")
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    sessions = [
        _session(subentry.subentry_id, "preserved", "2026-09-11T12:00:00+00:00")
    ]
    await _seed_archive(hass, entry, sessions)
    archive = await async_get_archive(hass, entry.entry_id, subentry.subentry_id)
    before = await archive.async_backup_data()
    client = await _admin_client(hass, hass_ws_client)

    query = AsyncMock(side_effect=RuntimeError("history query failed"))
    monkeypatch.setattr(management_ui, "archive_list_page", query)
    response = await _raw_management_call(
        client,
        entry=entry,
        section="conversations",
        action="list",
    )

    assert response["success"] is False
    assert response["error"] == {
        "code": "invalid_request",
        "message": "history query failed",
    }
    assert await archive.async_backup_data() == before
    query.assert_awaited_once()


async def test_owned_dispatcher_survives_setup_reload_and_retains_temporary_counts(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Real startup/reload never installs a dispatcher and scoped counts survive it."""
    from datetime import timedelta

    from custom_components.extended_openai_conversation_responses.temporary_memory import (
        async_get_temporary_memory,
    )
    from homeassistant.util import dt as dt_util

    command = management_ui.async_management_command
    handlers = management_ui._MANAGEMENT_SECTION_HANDLERS
    entry = _entry("Owned Management API")
    await _setup_entry(hass, entry)
    client = await _admin_client(hass, hass_ws_client)
    subentry = _conversation_subentry(entry)
    manager = await async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    await manager.async_add(
        "conversation:temporary-count-regression",
        "A parcel is due this afternoon.",
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        owner_scope_id=ADMIN_SCOPE_ID,
    )
    for reloaded in (False, True):
        if reloaded:
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
        assert management_ui.async_management_command is command
        assert management_ui._MANAGEMENT_SECTION_HANDLERS is handlers
        assert not hasattr(command, "__wrapped__")
        catalog = await _management_call(
            client, entry=entry, section="scopes", action="catalog"
        )
        own_scope = next(
            scope for scope in catalog["scopes"] if scope["scope_id"] == ADMIN_SCOPE_ID
        )
        assert own_scope["temporary_memory_count"] == 1
