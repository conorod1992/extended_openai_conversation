"""Tests for narrow management settings validation and scope permissions."""

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.management_ui import (
    _selected_scope,
    _validate_settings,
)


def test_unknown_settings_are_rejected() -> None:
    with pytest.raises(HomeAssistantError, match="Unknown settings"):
        _validate_settings({"arbitrary_config_entry_data": True})


def test_retention_and_mapping_settings_are_strictly_validated() -> None:
    assert _validate_settings(
        {
            "archive_enabled": True,
            "archive_retention_days": 30,
            "usage_request_retention_days": 7,
            "voice_device_mappings": {
                "kitchen": "user:alice",
                "hall": "shared",
            },
        }
    )["archive_enabled"] is True
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
