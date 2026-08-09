"""Tests for narrow management settings validation and scope permissions."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.management_ui import (
    _scope_catalog,
    _selected_scope,
    _validate_settings,
)
from homeassistant.exceptions import HomeAssistantError


class _Auth:
    async def async_get_user(self, user_id):
        return SimpleNamespace(id=user_id, name="Conor")

    async def async_get_users(self):
        return [
            SimpleNamespace(id="zero", name="Zoe"),
            SimpleNamespace(id="current", name="Conor"),
            SimpleNamespace(id="active", name="Alice"),
        ]


def test_unknown_settings_are_rejected() -> None:
    with pytest.raises(HomeAssistantError, match="Unknown settings"):
        _validate_settings({"arbitrary_config_entry_data": True})


def test_retention_and_mapping_settings_are_strictly_validated() -> None:
    assert (
        _validate_settings(
            {
                "archive_enabled": True,
                "archive_retention_days": 30,
                "usage_request_retention_days": 7,
                "voice_device_mappings": {
                    "kitchen": "user:alice",
                    "hall": "shared",
                },
            }
        )["archive_enabled"]
        is True
    )
    with pytest.raises(HomeAssistantError, match="unsupported archive retention"):
        _validate_settings({"archive_retention_days": 31})
    with pytest.raises(HomeAssistantError, match="must map device IDs"):
        _validate_settings({"voice_device_mappings": ["kitchen"]})


def test_normal_user_cannot_select_admin_or_other_user_scope() -> None:
    assert _selected_scope("alice", False, None) == "user:alice"
    with pytest.raises(HomeAssistantError, match="not available"):
        _selected_scope("alice", False, "user:bob")
    with pytest.raises(HomeAssistantError, match="not available"):
        _selected_scope("alice", False, "shared:household")
    assert _selected_scope("admin", True, "__anonymous__") == "__anonymous__"


async def test_scope_catalog_adds_counts_and_hides_empty_legacy_scope() -> None:
    hass = SimpleNamespace(auth=_Auth())
    scopes = await _scope_catalog(
        hass,
        "current",
        True,
        {"current": 2, "active": 4, "shared:household": 1},
        {"user:active": 3},
    )

    by_id = {scope["scope_id"]: scope for scope in scopes}
    assert by_id["user:current"]["is_current_user"] is True
    assert by_id["user:current"]["memory_count"] == 2
    assert by_id["user:active"]["conversation_count"] == 3
    assert by_id["user:zero"]["memory_count"] == 0
    assert by_id["shared:household"]["memory_count"] == 1
    assert "__anonymous__" not in by_id


async def test_scope_catalog_retains_legacy_scope_only_when_it_has_data() -> None:
    hass = SimpleNamespace(auth=_Auth())
    scopes = await _scope_catalog(hass, "current", True, {"__anonymous__": 2})

    legacy = next(scope for scope in scopes if scope["scope_id"] == "__anonymous__")
    assert legacy["memory_count"] == 2
    assert legacy["scope_type"] == "anonymous_legacy"
