"""Retained-data ownership regressions for deleted Home Assistant users."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.management_projections import (
    async_scope_catalog_projection,
)


class _Auth:
    def __init__(self, users):
        self._users = list(users)

    async def async_get_users(self):
        return list(self._users)

    async def async_get_user(self, user_id):
        return next((user for user in self._users if user.id == user_id), None)


async def test_admin_scope_catalog_surfaces_retained_deleted_users() -> None:
    hass = SimpleNamespace(
        auth=_Auth([SimpleNamespace(id="current", name="Current user")])
    )

    scopes = await async_scope_catalog_projection(
        hass,
        "current",
        True,
        memory_counts={"current": 1, "deleted-memory": 2},
        conversation_counts={"user:deleted-archive": 3},
        temporary_memory_counts={"user:deleted-temp": 4},
    )

    by_id = {scope["scope_id"]: scope for scope in scopes}
    assert by_id["user:current"]["orphaned"] is False
    assert by_id["user:deleted-memory"] == {
        "scope_id": "user:deleted-memory",
        "scope_type": "user",
        "display_name": "Deleted or unavailable user (deleted-memory)",
        "is_current_user": False,
        "orphaned": True,
        "memory_count": 2,
        "conversation_count": 0,
        "temporary_memory_count": 0,
    }
    assert by_id["user:deleted-archive"]["orphaned"] is True
    assert by_id["user:deleted-archive"]["conversation_count"] == 3
    assert by_id["user:deleted-temp"]["orphaned"] is True
    assert by_id["user:deleted-temp"]["temporary_memory_count"] == 4


async def test_new_user_destination_must_exist() -> None:
    hass = SimpleNamespace(
        auth=_Auth([SimpleNamespace(id="current", name="Current user")])
    )

    await management_ui._require_existing_user_destination(hass, "user:current")
    await management_ui._require_existing_user_destination(hass, "shared:household")

    with pytest.raises(HomeAssistantError, match="no longer exists"):
        await management_ui._require_existing_user_destination(
            hass, "user:deleted-user"
        )
