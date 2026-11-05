"""HA owns wall-time conversion and editor projections, even with stale clients."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.management_datetime import (
    resolve_local_time,
    resolve_management_dates,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from tests.test_guest_mode import _manager
from tests.test_temporary_memory import Storage


@pytest.mark.parametrize("reject", [False, True])
def test_ha_rules_resolve_wall_time_not_client_offset(hass, reject):
    hass.config.time_zone = "Africa/Casablanca"
    assert (
        resolve_local_time(hass, "2026-11-04T12:00", reject_ambiguous=reject)
        == "2026-11-04T12:00:00+00:00"
    )


def test_dst_and_invalid_inputs(hass):
    hass.config.time_zone = "Europe/Dublin"
    for invalid in (
        "2026-03-29T01:30",
        "2026-02-30T12:00",
        "2026-11-04T12:00Z",
        None,
        {},
    ):
        with pytest.raises(HomeAssistantError):
            resolve_local_time(hass, invalid, reject_ambiguous=False)
    with pytest.raises(HomeAssistantError, match="occurs twice"):
        resolve_local_time(hass, "2026-10-25T01:30", reject_ambiguous=True)
    assert (
        resolve_local_time(hass, "2026-10-25T01:30", reject_ambiguous=False)
        == "2026-10-25T00:30:00+00:00"
    )
    legacy = dict(
        section="memories",
        action="temporary_update",
        expires_at="2026-10-25T01:30:00+00:00",
    )
    assert resolve_management_dates(hass, legacy) is legacy


@pytest.mark.parametrize(
    "local", [{"unknown": "x"}, {"expires_at": "different"}, {"expires_at": 123}, []]
)
def test_reject_invalid_local_fields(hass, local):
    with pytest.raises(HomeAssistantError):
        resolve_management_dates(
            hass,
            dict(
                section="memories",
                action="temporary_add",
                expires_at="2026-11-04T12:00",
                local_times=local,
            ),
        )


async def test_temporary_save_list_and_unchanged_edit_use_ha_rules(
    hass, monkeypatch, management_message
):
    hass.config.time_zone = "Africa/Casablanca"
    monkeypatch.setattr(dt_util, "utcnow", lambda: datetime(2026, 11, 3, tzinfo=UTC))
    store = Storage()
    manager = TemporaryMemory(store)
    await manager.async_initialize()
    monkeypatch.setattr(
        ui, "async_get_temporary_memory", AsyncMock(return_value=manager)
    )
    monkeypatch.setattr(ui, "_require_existing_user_destination", AsyncMock())

    async def call(action, **fields):
        return await ui.async_management_command(
            hass,
            "alice",
            False,
            management_message("memories", action, scope_id="user:alice", **fields),
        )

    saved = await call(
        "temporary_add",
        content="Visitors tomorrow",
        expires_at="2026-11-04T12:00",
        local_times={"expires_at": "2026-11-04T12:00"},
    )
    record = saved["memory"]
    assert record["expires_at"] == "2026-11-04T12:00:00+00:00"
    assert record["expires_at_local"] == "2026-11-04T12:00:00"
    listed = await call("temporary_list")
    assert listed["memories"][0]["expires_at_local"] == record["expires_at_local"]
    edited = await call(
        "temporary_update",
        memory_id=record["memory_id"],
        content="Updated visitors",
        expires_at=record["expires_at"],
    )
    assert edited["memory"]["expires_at"] == record["expires_at"]
    assert store.data["records"][0]["expires_at"] == record["expires_at"]


async def test_guest_schedule_stores_and_projects_ha_time(
    hass, monkeypatch, management_message
):
    hass.config.time_zone = "Africa/Casablanca"
    monkeypatch.setattr(dt_util, "utcnow", lambda: datetime(2026, 11, 3, tzinfo=UTC))
    manager = _manager(hass)
    monkeypatch.setattr(ui, "async_get_guest_mode", AsyncMock(return_value=manager))
    result = await ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "guest_mode",
            "update",
            active_from="2026-11-04T12:00",
            active_until="2026-11-04T13:00",
            local_times={
                "active_from": "2026-11-04T12:00",
                "active_until": "2026-11-04T13:00",
            },
        ),
    )
    assert result["status"]["active_from"] == "2026-11-04T12:00:00+00:00"
    assert result["status"]["active_from_local"] == "2026-11-04T12:00"
    assert result["status"]["active_until_local"] == "2026-11-04T13:00"
