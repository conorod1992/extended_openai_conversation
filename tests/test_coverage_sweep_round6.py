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



def test_template_exposed_entities_helper_delegates(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import template

    expected = [{"entity_id": "light.kitchen"}]
    getter = Mock(return_value=expected)
    monkeypatch.setattr(template, "get_exposed_entities", getter)
    manager = template.ExtendedOpenAITemplateManager(hass)

    assert manager._get_exposed_entities() is expected
    getter.assert_called_once_with(hass)


@pytest.mark.asyncio
async def test_ha_llm_discovery_handles_missing_core_component(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import ha_llm_tools

    monkeypatch.setattr(
        ha_llm_tools.importlib,
        "import_module",
        Mock(side_effect=ImportError("component unavailable")),
    )
    monkeypatch.setattr(ha_llm_tools.llm, "async_get_apis", Mock(return_value=[]))
    context = SimpleNamespace()

    snapshot = await ha_llm_tools.async_discover(hass, context, references=None)

    assert snapshot.tools == {}
    assert snapshot.unavailable_sources == []


@pytest.mark.asyncio
async def test_ha_llm_discovery_empty_reference_list_short_circuits(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import ha_llm_tools

    importer = Mock()
    monkeypatch.setattr(ha_llm_tools.importlib, "import_module", importer)

    snapshot = await ha_llm_tools.async_discover(hass, SimpleNamespace(), references=[])

    assert snapshot.tools == {}
    importer.assert_not_called()


@pytest.mark.asyncio
async def test_web_bounded_response_read_caches_materialized_body() -> None:
    from custom_components.extended_openai_conversation_responses.functions import web
    from tests.test_remote_response_bounds import _FakeResponse

    response = _FakeResponse(b"cached")
    bounded = web._BoundedResponse(response, 16)

    first = await bounded.read()
    second = await bounded.read()

    assert first == b"cached"
    assert second is first
    assert response.content.requested == [17]


def test_debug_conversation_trace_disabled_is_noop(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import debug

    manager = SimpleNamespace(enabled=False)
    monkeypatch.setattr(debug, "get_debug_manager", Mock(return_value=manager))
    agent = SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )
    user_input = SimpleNamespace(conversation_id="conversation")

    with debug.conversation_debug_trace(agent, user_input) as trace:
        assert trace is None


def test_debug_conversation_trace_finishes_failure_and_restores_context(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import debug

    trace = SimpleNamespace(error_type=None, result=None)
    manager = SimpleNamespace(
        enabled=True,
        begin=Mock(return_value=trace),
        finish=Mock(),
    )
    monkeypatch.setattr(debug, "get_debug_manager", Mock(return_value=manager))
    agent = SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )
    user_input = SimpleNamespace(conversation_id="conversation")

    with pytest.raises(RuntimeError, match="boom"):
        with debug.conversation_debug_trace(agent, user_input) as active:
            assert active is trace
            raise RuntimeError("boom")

    manager.finish.assert_called_once()
    assert manager.finish.call_args.kwargs["successful"] is False
    assert isinstance(manager.finish.call_args.kwargs["error"], RuntimeError)
    assert debug.current_debug_trace() is None



@pytest.mark.asyncio
async def test_temporary_memory_reconcile_counts_invalid_and_normalized_owners(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    original = temporary_memory.TemporaryMemoryRecord(
        memory_id="one",
        scope_id="conversation:one",
        content="one",
        category="general",
        source="manual",
        expires_at="2099-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        owner_scope_id=None,
    )
    normalized = temporary_memory.TemporaryMemoryRecord(
        memory_id=original.memory_id,
        scope_id=original.scope_id,
        content=original.content,
        category=original.category,
        source=original.source,
        expires_at=original.expires_at,
        created_at=original.created_at,
        updated_at=original.updated_at,
        owner_scope_id="user:alice",
    )
    invalid = temporary_memory.TemporaryMemoryRecord(
        memory_id="two",
        scope_id="conversation:two",
        content="two",
        category="general",
        source="manual",
        expires_at="2099-01-01T00:00:00+00:00",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        owner_scope_id=None,
    )
    store = SimpleNamespace(
        async_load=AsyncMock(return_value={"records": [{"id": 1}, {"id": 2}]})
    )
    manager = temporary_memory.TemporaryMemory(store)

    monkeypatch.setattr(
        temporary_memory,
        "_record_from_storage",
        Mock(side_effect=[original, invalid]),
    )
    monkeypatch.setattr(temporary_memory, "_parse_expiry", Mock(return_value=object()))
    monkeypatch.setattr(
        temporary_memory,
        "_normalize_record_owner",
        Mock(side_effect=[normalized, None]),
    )

    await manager._async_reconcile_failed_save()

    assert list(manager._records) == ["one"]
    assert manager._records["one"].owner_scope_id == "user:alice"
    assert manager.invalid_owners_pruned == 1
    assert manager._normalization_pending is True
    assert manager.initialized is True


@pytest.mark.asyncio
async def test_guest_mode_restrict_rejects_non_widening_invalid_interval(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    now = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="later than active_from"):
        await manager.async_restrict(
            active_from=now.isoformat(),
            active_until=(now - timedelta(minutes=1)).isoformat(),
            now=now,
        )


@pytest.mark.asyncio
async def test_guest_mode_restrict_widens_existing_schedule(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    now = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)
    manager._schedule = guest_mode.GuestModeSchedule(
        active_from=(now - timedelta(hours=1)).isoformat(),
        active_until=(now + timedelta(hours=1)).isoformat(),
        source="home_assistant",
        updated_at=now.isoformat(),
    )

    async def commit(schedule):
        manager._schedule = schedule

    monkeypatch.setattr(manager, "_async_commit_schedule", commit)

    status = await manager.async_restrict(
        active_from=(now - timedelta(hours=2)).isoformat(),
        active_until=(now + timedelta(hours=2)).isoformat(),
        now=now,
    )

    assert status["currently_active"] is True
    assert manager._schedule is not None
    assert manager._schedule.active_from == (now - timedelta(hours=2)).isoformat()
    assert manager._schedule.active_until == (now + timedelta(hours=2)).isoformat()


@pytest.mark.asyncio
async def test_guest_mode_restrict_can_make_existing_schedule_indefinite(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    now = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)
    manager._schedule = guest_mode.GuestModeSchedule(
        active_from=(now - timedelta(hours=1)).isoformat(),
        active_until=(now + timedelta(hours=1)).isoformat(),
        source="home_assistant",
        updated_at=now.isoformat(),
    )

    async def commit(schedule):
        manager._schedule = schedule

    monkeypatch.setattr(manager, "_async_commit_schedule", commit)

    status = await manager.async_restrict(make_indefinite=True, now=now)

    assert status["indefinite"] is True
    assert manager._schedule.active_until is None
