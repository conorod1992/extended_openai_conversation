"""Sixth residual coverage sweep: pure validators and state transitions."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda d: d.__setitem__("compatibility", {"minimum_eoai_version": 123}),
            "minimum EOAI version",
        ),
        (
            lambda d: d.__setitem__(
                "compatibility", {"minimum_eoai_version": "not-a-version"}
            ),
            "minimum EOAI version",
        ),
        (
            lambda d: d["defaults"].__setitem__("status", "current"),
            "defaults must describe unknown",
        ),
        (
            lambda d: d.__setitem__("models", []),
            "Invalid model list",
        ),
        (
            lambda d: d["models"][0].__setitem__("display_name", ""),
            "Invalid display name",
        ),
        (
            lambda d: d["models"][0].__setitem__("kind", "invalid"),
            "Invalid model kind",
        ),
    ],
)
def test_model_catalog_validation_remaining_envelope_and_model_guards(
    mutation, message
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    document = deepcopy(model_catalog.BUNDLED_CATALOG)
    mutation(document)

    with pytest.raises(ValueError, match=message):
        model_catalog.validate_catalog(document)


def test_model_catalog_rejects_missing_snapshot_parent() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    document = deepcopy(model_catalog.BUNDLED_CATALOG)
    model = deepcopy(document["models"][0])
    model["id"] = "coverage-orphan-snapshot"
    model["kind"] = "snapshot"
    model["alias_of"] = "definitely-missing-parent"
    document["models"] = [model]

    with pytest.raises(ValueError, match="Snapshot parent is missing"):
        model_catalog.validate_catalog(document)


def test_model_catalog_rejects_self_alias() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    document = deepcopy(model_catalog.BUNDLED_CATALOG)
    model = deepcopy(document["models"][0])
    model["id"] = "coverage-self-alias"
    model["kind"] = "alias"
    model["alias_of"] = "coverage-self-alias"
    document["models"] = [model]

    with pytest.raises(ValueError, match="Alias target"):
        model_catalog.validate_catalog(document)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("structured_outputs", False, "structured_outputs"),
        ("responses_web_search", False, "responses_web_search"),
        ("streaming", False, "streaming"),
    ],
)
def test_model_catalog_transition_rejects_capability_removal(
    field: str, value: bool, message: str
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    current = deepcopy(model_catalog.BUNDLED_CATALOG)
    candidate = deepcopy(model_catalog.BUNDLED_CATALOG)
    candidate["catalog_version"] = current["catalog_version"] + 1

    target = next(
        item
        for item in candidate["models"]
        if item[field] is True
    )
    target[field] = value

    with pytest.raises(ValueError, match=message):
        model_catalog.validate_catalog_transition(current, candidate)


def test_model_catalog_transition_rejects_lower_max_output_tokens() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    current = deepcopy(model_catalog.BUNDLED_CATALOG)
    candidate = deepcopy(model_catalog.BUNDLED_CATALOG)
    candidate["catalog_version"] = current["catalog_version"] + 1
    target = candidate["models"][0]
    target["limits"]["max_output_tokens"] = (
        current["models"][0]["limits"]["max_output_tokens"] - 1
    )

    with pytest.raises(ValueError, match="lower max output"):
        model_catalog.validate_catalog_transition(current, candidate)


def test_model_catalog_transition_rejects_service_tier_removal() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    current = deepcopy(model_catalog.BUNDLED_CATALOG)
    candidate = deepcopy(model_catalog.BUNDLED_CATALOG)
    candidate["catalog_version"] = current["catalog_version"] + 1
    target = next(item for item in candidate["models"] if item["service_tiers"])
    old = next(item for item in current["models"] if item["id"] == target["id"])
    target["service_tiers"] = old["service_tiers"][:-1]

    with pytest.raises(ValueError, match="service-tier"):
        model_catalog.validate_catalog_transition(current, candidate)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"schedule": {"bad": True}},
        {
            "schedule": {
                "active_from": 123,
                "active_until": None,
                "source": "home_assistant",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
        {
            "schedule": {
                "active_from": "2026-01-01T00:00:00+00:00",
                "active_until": None,
                "source": "",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        },
    ],
)
def test_guest_mode_backup_validation_rejects_invalid_shapes(value) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    with pytest.raises(ValueError):
        guest_mode.GuestModeManager.validate_backup_data(value)


def test_guest_mode_backup_validation_accepts_none_and_valid_schedule() -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    assert guest_mode.GuestModeManager.validate_backup_data({"schedule": None}) is None
    valid = {
        "schedule": {
            "active_from": "2026-10-05T18:00:00+00:00",
            "active_until": "2026-10-05T20:00:00+00:00",
            "source": "home_assistant",
            "updated_at": "2026-10-05T17:00:00+00:00",
        }
    }
    schedule = guest_mode.GuestModeManager.validate_backup_data(valid)
    assert schedule is not None
    assert schedule.source == "home_assistant"


def test_guest_mode_live_or_future_schedule_expiry_and_indefinite(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    now = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)

    manager._schedule = guest_mode.GuestModeSchedule(
        active_from=(now - timedelta(hours=1)).isoformat(),
        active_until=(now - timedelta(minutes=1)).isoformat(),
        source="home_assistant",
        updated_at=now.isoformat(),
    )
    assert manager._live_or_future_schedule(now) is None

    manager._schedule = guest_mode.GuestModeSchedule(
        active_from=(now - timedelta(hours=1)).isoformat(),
        active_until=None,
        source="home_assistant",
        updated_at=now.isoformat(),
    )
    assert manager._live_or_future_schedule(now) is manager._schedule


def test_management_effective_function_groups_filters_unavailable_members() -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    options = {
        repair.CONF_FUNCTION_GROUPS: [
            {
                "id": "g",
                "name": "Group",
                "functions": ["valid", "missing", 123],
            },
            "malformed",
        ]
    }
    valid = [{"spec": {"name": "valid"}}]

    effective, issues, raw = repair._effective_function_groups(options, valid)

    assert effective[0]["functions"] == ["valid"]
    assert issues == [
        {
            "id": "g",
            "name": "Group",
            "unavailable_functions": ["missing"],
        }
    ]
    assert raw[0]["functions"] == ["valid", "missing", 123]


def test_management_effective_function_groups_preserves_non_list_shape() -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    options = {repair.CONF_FUNCTION_GROUPS: "invalid"}

    effective, issues, raw = repair._effective_function_groups(options, [])

    assert effective == "invalid"
    assert raw == "invalid"
    assert issues == []


def test_management_has_unavailable_native_tool_detects_missing_implementation() -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    options = {
        repair.CONF_FUNCTION_TOOLS: [
            {
                "spec": {"name": "missing_native"},
                "function": {"type": "native", "name": "definitely_missing"},
            }
        ]
    }

    assert repair.has_unavailable_native_tool(options) is True


@pytest.mark.asyncio
async def test_backup_take_completed_import_consumes_session(hass, tmp_path) -> None:
    from custom_components.extended_openai_conversation_responses import backup_transfer

    path = tmp_path / "complete.json"
    path.write_bytes(b"data")
    session = backup_transfer.ImportSession(
        session_id="complete",
        entry_id="entry",
        subentry_id="agent",
        path=str(path),
        filename="backup.json",
        expected_size=4,
        kind="legacy_json",
        expires_at=10**12,
        received=4,
        next_index=1,
    )
    backup_transfer._imports(hass)[session.session_id] = session

    result = await backup_transfer._take_completed_import(
        hass, "complete", "entry", "agent"
    )

    assert result is session
    assert "complete" not in backup_transfer._imports(hass)


@pytest.mark.asyncio
async def test_backup_take_completed_import_rejects_cancelled_session(
    hass, tmp_path, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import backup, backup_transfer

    path = tmp_path / "complete.json"
    path.write_bytes(b"data")
    session = backup_transfer.ImportSession(
        session_id="complete",
        entry_id="entry",
        subentry_id="agent",
        path=str(path),
        filename="backup.json",
        expected_size=4,
        kind="legacy_json",
        expires_at=10**12,
        received=4,
        next_index=1,
    )
    backup_transfer._imports(hass)[session.session_id] = session

    original_lock = backup_transfer._registry_lock

    @pytest.fixture
    def _unused():
        yield

    class RemovingLock:
        async def __aenter__(self):
            backup_transfer._imports(hass).pop(session.session_id, None)
        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(backup_transfer, "_registry_lock", lambda _hass: RemovingLock())

    with pytest.raises(backup.BackupError, match="expired or was cancelled"):
        await backup_transfer._take_completed_import(
            hass, "complete", "entry", "agent"
        )
