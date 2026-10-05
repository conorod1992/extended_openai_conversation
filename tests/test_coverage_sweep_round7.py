"""Seventh residual coverage sweep: compact state machines and validators."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError


def _valid_delayed_call():
    from custom_components.extended_openai_conversation_responses import delayed_tools

    return {
        "call_id": "call",
        "entry_id": "entry",
        "subentry_id": "agent",
        "tool_name": "tool",
        "arguments": {"value": 1},
        "due_at": "2099-01-01T00:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
        "status": delayed_tools._PENDING,
        "retry_count": 0,
        "definition_fingerprint": "a" * 64,
        "user_id": "user",
        "device_id": "device",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.clear(), "not an object|invalid call_id"),
        (lambda value: value.__setitem__("call_id", ""), "invalid call_id"),
        (lambda value: value.__setitem__("arguments", []), "invalid arguments"),
        (lambda value: value.__setitem__("status", "done"), "invalid status"),
        (lambda value: value.__setitem__("retry_count", -1), "invalid retry_count"),
        (lambda value: value.__setitem__("due_at", "bad"), "invalid due_at"),
        (lambda value: value.__setitem__("created_at", "bad"), "invalid created_at"),
        (
            lambda value: value.__setitem__("definition_fingerprint", "bad"),
            "definition_fingerprint",
        ),
        (lambda value: value.__setitem__("user_id", 1), "invalid user_id"),
        (lambda value: value.__setitem__("device_id", 1), "invalid device_id"),
    ],
)
def test_delayed_tool_call_rejects_invalid_persisted_shapes(mutation, message) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    raw = _valid_delayed_call()
    mutation(raw)

    with pytest.raises(ValueError, match=message):
        delayed_tools.DelayedToolCall.from_dict(raw)


def test_delayed_tool_call_round_trip_isolated_arguments() -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    raw = _valid_delayed_call()
    record = delayed_tools.DelayedToolCall.from_dict(raw)

    assert record.as_dict() == raw
    raw["arguments"]["value"] = 2
    assert record.arguments == {"value": 1}


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        (0, 0),
        (5, 5),
        ("00:01:30", 90),
        ({"minutes": 2}, 120),
    ],
)
def test_delayed_tool_delay_normalization(value, seconds: int) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    assert delayed_tools._delay_as_timedelta(value).total_seconds() == seconds


@pytest.mark.parametrize("value", [-1, {"seconds": -1}, "not-a-delay"])
def test_delayed_tool_delay_rejects_negative_or_invalid(value) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    with pytest.raises(HomeAssistantError):
        delayed_tools._delay_as_timedelta(value)


@pytest.mark.asyncio
async def test_delayed_tool_reconcile_drops_interrupted_execution(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    pending = _valid_delayed_call()
    executing = {**_valid_delayed_call(), "call_id": "executing", "status": delayed_tools._EXECUTING}
    manager = delayed_tools.DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"calls": [pending, executing]})
    )

    await manager._async_reconcile_failed_save()

    assert set(manager._records) == {"call"}


@pytest.mark.asyncio
async def test_delayed_tool_reconcile_rejects_non_list_calls(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"calls": {}})
    )

    with pytest.raises(ValueError, match="malformed"):
        await manager._async_reconcile_failed_save()


def test_delayed_tool_invalidate_cancels_tasks_and_resets_scheduler(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    task = Mock()
    manager._tasks = {"call": task}
    manager._records = {"call": object()}
    manager._started = True
    manager._setup_complete = True

    manager._invalidate_after_unreadable_store()

    assert manager._records == {}
    assert manager._tasks == {}
    assert manager._started is False
    assert manager._setup_complete is False
    assert task in manager._invalidated_tasks
    task.cancel.assert_called_once()


def test_delayed_tool_handle_started_is_idempotent_and_arms_records(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    manager._records = {"one": object(), "two": object()}
    arm = Mock()
    monkeypatch.setattr(manager, "_arm", arm)

    manager._handle_started()
    manager._handle_started()

    assert manager._started is True
    assert [call.args[0] for call in arm.call_args_list] == ["one", "two"]


def test_delayed_tool_handle_stop_cancels_only_pending_or_missing(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    pending_task = Mock()
    executing_task = Mock()
    missing_task = Mock()
    manager._started = True
    manager._records = {
        "pending": SimpleNamespace(status=delayed_tools._PENDING),
        "executing": SimpleNamespace(status=delayed_tools._EXECUTING),
    }
    manager._tasks = {
        "pending": pending_task,
        "executing": executing_task,
        "missing": missing_task,
    }

    manager._handle_stop()

    assert manager._started is False
    pending_task.cancel.assert_called_once()
    missing_task.cancel.assert_called_once()
    executing_task.cancel.assert_not_called()


def test_intercom_announce_capability_handles_missing_and_feature_bits(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)

    assert manager._state_announce_capable(None) is False
    assert manager._state_announce_capable(
        SimpleNamespace(attributes={intercom.ATTR_SUPPORTED_FEATURES: 0})
    ) is False
    assert manager._state_announce_capable(
        SimpleNamespace(attributes={intercom.ATTR_SUPPORTED_FEATURES: intercom.ANNOUNCE_FEATURE})
    ) is True


def test_intercom_refresh_state_listener_noops_when_closed_or_unchanged(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)
    tracker = Mock()
    monkeypatch.setattr(intercom, "async_track_state_change_event", tracker)
    monkeypatch.setattr(manager, "_satellite_entity_ids", Mock(return_value=["assist_satellite.one"]))

    manager._closed = True
    manager._refresh_state_listener()
    tracker.assert_not_called()

    manager._closed = False
    manager._tracked_entities = {"assist_satellite.one"}
    manager._refresh_state_listener()
    tracker.assert_not_called()


def test_intercom_refresh_state_listener_replaces_subscription(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    real_refresh = intercom.IntercomManager._refresh_state_listener
    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)
    monkeypatch.setattr(
        manager,
        "_refresh_state_listener",
        real_refresh.__get__(manager, intercom.IntercomManager),
    )
    previous = Mock()
    replacement = Mock()
    manager._unsub_state = previous
    manager._tracked_entities = {"old"}
    monkeypatch.setattr(manager, "_satellite_entity_ids", Mock(return_value=["new"]))
    tracker = Mock(return_value=replacement)
    monkeypatch.setattr(intercom, "async_track_state_change_event", tracker)

    manager._refresh_state_listener()

    previous.assert_called_once()
    assert manager._tracked_entities == {"new"}
    assert manager._unsub_state is replacement
    tracker.assert_called_once()


@pytest.mark.asyncio
async def test_intercom_shutdown_unsubscribes_expires_and_cancels(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import intercom

    monkeypatch.setattr(intercom.IntercomManager, "_refresh_state_listener", lambda self: None)
    manager = intercom.IntercomManager(hass)
    unsub_state = Mock()
    unsub_expiry = Mock()
    manager._unsub_state = unsub_state
    manager._expiry_unsubscribers.add(unsub_expiry)

    delivery = SimpleNamespace(set=Mock())
    item = SimpleNamespace(deliveries={"assist_satellite.one": delivery})
    manager._queues = {"assist_satellite.one": deque([item])}

    task = Mock()
    task.cancel = Mock()
    manager._drain_tasks = {task}
    monkeypatch.setattr(intercom.asyncio, "gather", AsyncMock(return_value=[]))

    await manager.async_shutdown()

    unsub_state.assert_called_once()
    unsub_expiry.assert_called_once()
    delivery.set.assert_called_once_with("expired", "integration_removed")
    task.cancel.assert_called_once()
    assert manager._queues == {}
    assert manager._drain_tasks == set()
    assert manager._draining == set()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("My Assistant", "my-assistant"),
        ("  !!!  ", "conversation-agent"),
        ("Kitchen / Voice #1", "kitchen-voice-1"),
    ],
)
def test_transfer_safe_title(title: str, expected: str) -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    assert transfer._safe_title(title) == expected


def test_transfer_configuration_mapping_without_device_mapping_keeps_other_fields() -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    config = {
        transfer.CONF_VOICE_DEFAULT_USER_ID: "alice",
        "other": {"nested": True},
    }

    mapped = transfer._map_configuration_users(config, {"alice": "bob"})

    assert mapped[transfer.CONF_VOICE_DEFAULT_USER_ID] == "bob"
    assert mapped["other"] == {"nested": True}
    assert config[transfer.CONF_VOICE_DEFAULT_USER_ID] == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved",
    [
        {"last_checked": 0, "etag": 123},
        {"last_checked": 0, "etag": "x\nmalicious"},
        {
            "last_checked": 0,
            "incompatible_catalog": {"schema_version": "bad", "catalog_version": 1},
        },
    ],
)
async def test_model_catalog_manager_rejects_invalid_saved_metadata(
    hass, monkeypatch, saved
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager.store = SimpleNamespace(async_load=AsyncMock(return_value=saved))
    log = Mock()
    monkeypatch.setattr(model_catalog_manager, "log_handled_failure", log)
    activate = Mock()
    monkeypatch.setattr(model_catalog_manager, "activate_catalog", activate)

    await manager.async_load()

    assert manager.catalog is None
    assert manager.available_catalog is None
    assert manager.last_checked == 0.0
    assert manager.last_error is not None
    log.assert_called()
    activate.assert_called_once_with(None)


@pytest.mark.asyncio
async def test_model_catalog_manager_recent_check_short_circuits(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager.last_checked = 1000.0
    monkeypatch.setattr(model_catalog_manager.time, "time", Mock(return_value=1001.0))
    session = Mock()
    monkeypatch.setattr(model_catalog_manager, "async_get_clientsession", session)

    status = await manager.async_check(force=False)

    assert status == manager.status()
    session.assert_not_called()


@pytest.mark.asyncio
async def test_model_catalog_setup_is_idempotent(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    existing = object()
    hass.data[model_catalog_manager.DATA_MANAGER] = existing
    constructor = Mock()
    monkeypatch.setattr(model_catalog_manager, "ModelCatalogManager", constructor)

    await model_catalog_manager.async_setup_model_catalog(hass)

    constructor.assert_not_called()



@pytest.mark.asyncio
async def test_delayed_tool_schedule_requires_initialized_scheduler(hass) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    entity = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )

    with pytest.raises(HomeAssistantError, match="scheduler is unavailable"):
        await manager.async_schedule(
            entity,
            "tool",
            {"delay": 1},
            None,
        )


@pytest.mark.asyncio
async def test_delayed_tool_remove_agent_noop_and_cancels_matching_tasks(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    await manager.async_remove_agent("entry", "agent")

    one = delayed_tools.DelayedToolCall.from_dict(_valid_delayed_call())
    two = delayed_tools.DelayedToolCall.from_dict(
        {**_valid_delayed_call(), "call_id": "other", "entry_id": "other-entry"}
    )
    manager._records = {"call": one, "other": two}
    task = Mock()
    manager._tasks = {"call": task}
    save = AsyncMock(side_effect=lambda records: setattr(manager, "_records", records))
    monkeypatch.setattr(manager, "_async_save_records_transactionally", save)

    await manager.async_remove_agent("entry", "agent")

    assert set(manager._records) == {"other"}
    task.cancel.assert_called_once()


def test_delayed_tool_arm_skips_existing_missing_and_non_pending(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    existing = Mock()
    existing.done.return_value = False
    manager._tasks["existing"] = existing
    manager._records["existing"] = SimpleNamespace(status=delayed_tools._PENDING)

    create = Mock()
    monkeypatch.setattr(delayed_tools.asyncio, "create_task", create)

    manager._arm("existing")
    manager._arm("missing")
    manager._records["executing"] = SimpleNamespace(status=delayed_tools._EXECUTING)
    manager._arm("executing")

    create.assert_not_called()


@pytest.mark.asyncio
async def test_model_catalog_manager_apply_rejects_incompatible_saved_requests(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    candidate = deepcopy(model_catalog_manager.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    manager.available_catalog = candidate
    monkeypatch.setattr(
        model_catalog_manager,
        "validate_catalog_transition",
        Mock(),
    )
    monkeypatch.setattr(
        manager,
        "_candidate_preserves_saved_requests",
        AsyncMock(return_value=False),
    )
    log = Mock()
    monkeypatch.setattr(model_catalog_manager, "log_handled_failure", log)

    status = await manager.async_apply_update()

    assert "could not be applied" in status["last_error"]
    assert manager.catalog is None
    assert manager.available_catalog is candidate
    log.assert_called_once()


@pytest.mark.asyncio
async def test_model_catalog_manager_apply_success_activates_candidate(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    candidate = deepcopy(model_catalog_manager.BUNDLED_CATALOG)
    candidate["catalog_version"] += 1
    manager.available_catalog = candidate
    manager.etag = "etag"
    manager.last_checked = 123
    manager._save = AsyncMock()
    monkeypatch.setattr(model_catalog_manager, "validate_catalog_transition", Mock())
    monkeypatch.setattr(
        manager,
        "_candidate_preserves_saved_requests",
        AsyncMock(return_value=True),
    )
    activate = Mock()
    sync = Mock()
    monkeypatch.setattr(model_catalog_manager, "activate_catalog", activate)
    monkeypatch.setattr(model_catalog_manager, "sync_all_model_lifecycles", sync)

    status = await manager.async_apply_update()

    assert status["source"] == "downloaded"
    assert status["update_available"] is False
    assert manager.catalog is candidate
    assert manager.available_catalog is None
    activate.assert_called_once_with(candidate)
    sync.assert_called_once_with(hass)


@pytest.mark.asyncio
async def test_model_catalog_manager_reset_save_failure_keeps_current_catalog(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager.catalog = deepcopy(model_catalog_manager.BUNDLED_CATALOG)
    manager.catalog["catalog_version"] += 1
    manager._save = AsyncMock(side_effect=OSError("disk full"))
    monkeypatch.setattr(
        manager,
        "_bundled_reset_would_invalidate_saved_reasoning",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        manager,
        "_candidate_preserves_saved_requests",
        AsyncMock(return_value=True),
    )

    status = await manager.async_reset()

    assert "reset failed" in status["last_error"]
    assert status["source"] == "downloaded"


@pytest.mark.asyncio
async def test_model_catalog_manager_bundled_reset_check_is_false_without_downloaded_catalog(
    hass,
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    assert await manager._bundled_reset_would_invalidate_saved_reasoning() is False


@pytest.mark.asyncio
async def test_management_scope_projection_includes_orphaned_and_legacy_counts(hass) -> None:
    from custom_components.extended_openai_conversation_responses import management_projections

    hass.auth.async_get_users = AsyncMock(
        return_value=[SimpleNamespace(id="current", name="Current")]
    )

    result = await management_projections.async_scope_catalog_projection(
        hass,
        "current",
        True,
        memory_counts={"deleted": 2, management_projections.ANONYMOUS_USER_ID: 1},
        conversation_counts={"user:deleted": 3, management_projections.ANONYMOUS_USER_ID: 4},
        temporary_memory_counts={"user:deleted": 5},
    )

    orphan = next(item for item in result if item["scope_id"] == "user:deleted")
    assert orphan["orphaned"] is True
    assert orphan["memory_count"] == 2
    assert orphan["conversation_count"] == 3
    assert orphan["temporary_memory_count"] == 5
    legacy = next(
        item
        for item in result
        if item["scope_id"] == management_projections.ANONYMOUS_USER_ID
    )
    assert legacy["memory_count"] == 1
    assert legacy["conversation_count"] == 4


@pytest.mark.asyncio
async def test_management_scope_projection_non_admin_only_sees_self(hass) -> None:
    from custom_components.extended_openai_conversation_responses import management_projections

    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(id="user", name="User Name")
    )

    result = await management_projections.async_scope_catalog_projection(
        hass, "user", False, memory_counts={"user": 2}
    )

    assert result == [
        {
            "scope_id": "user:user",
            "scope_type": "user",
            "display_name": "User Name",
            "is_current_user": True,
            "orphaned": False,
            "memory_count": 2,
            "conversation_count": 0,
            "temporary_memory_count": 0,
        }
    ]


def test_speech_markdown_link_end_matrix() -> None:
    from custom_components.extended_openai_conversation_responses import speech

    cases = [
        ("[label", 0, False, ("incomplete", 0)),
        ("[label", 0, True, ("no", 0)),
        ("[label]", 0, False, ("incomplete", 0)),
        ("[label]x", 0, True, ("no", 0)),
        ("[label](", 0, False, ("incomplete", 0)),
        ("[label](bad url)", 0, True, ("no", 0)),
        ("[label](https://example.com)", 0, True, ("complete", 28)),
        ("[label](https://a.example/(x))", 0, True, ("complete", 30)),
    ]
    for text, start, final, expected in cases:
        assert speech.StreamingSpeechSanitizer._markdown_link_end(
            text, start, final
        ) == expected


@pytest.mark.asyncio
async def test_request_rule_preview_continuation_chain_stops_at_terminal_rule(hass) -> None:
    from custom_components.extended_openai_conversation_responses import request_rule_match_preview
    from custom_components.extended_openai_conversation_responses.request_rules import RuleMatch

    continued_rule = {
        "id": "one",
        "name": "One",
        "match_type": "equals",
        "action_type": "local_action",
        "continue_matching": True,
        "action": {
            "actions": [],
            "continue_to_ai": False,
            "success_response": "done",
            "failure_response": "failed",
        },
    }
    stopped_rule = {
        "id": "two",
        "name": "Two",
        "match_type": "equals",
        "action_type": "local_action",
        "continue_matching": False,
        "action": {
            "actions": [],
            "continue_to_ai": False,
            "success_response": "done",
            "failure_response": "failed",
        },
    }

    async def eligible(_hass, _text, skipped):
        skipped.append({"id": "skipped", "name": "Skipped", "reason": "conditions_false"})
        yield RuleMatch(continued_rule, "hello", False, 100.0)
        yield RuleMatch(stopped_rule, "hello", False, 100.0)

    rules = SimpleNamespace(
        _has_continuation=True,
        async_eligible_matches=eligible,
    )

    result = await request_rule_match_preview.async_request_rule_match_preview(
        hass, rules, "hello"
    )

    assert [item["status"] for item in result["matched_rules"]] == [
        "continued",
        "stopped",
    ]
    assert result["rule"]["id"] == "two"
    assert result["skipped_conditions"][0]["id"] == "skipped"


class _AsyncPage:
    def __init__(self, values):
        self._values = list(values)

    def __aiter__(self):
        self._iterator = iter(self._values)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
async def test_authenticated_client_skip_authentication_and_transport_error(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import helpers

    client = SimpleNamespace(models=SimpleNamespace(list=Mock()))
    constructor = Mock(return_value=client)
    monkeypatch.setattr(helpers, "AsyncOpenAI", constructor)
    monkeypatch.setattr(helpers, "get_async_client", Mock(return_value=object()))

    result = await helpers.get_authenticated_client(
        hass, "key", None, None, None, "openai", skip_authentication=True
    )
    assert result is client
    client.models.list.assert_not_called()

    hass.async_add_executor_job = AsyncMock(side_effect=TimeoutError("timeout"))
    with pytest.raises(Exception):
        await helpers.get_authenticated_client(
            hass, "key", None, None, None, "openai", skip_authentication=False
        )


@pytest.mark.asyncio
async def test_authenticated_client_materializes_coroutine_paginator(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import helpers

    async def page_coro():
        return _AsyncPage([object()])

    client = SimpleNamespace(models=SimpleNamespace(list=Mock()))
    monkeypatch.setattr(helpers, "AsyncOpenAI", Mock(return_value=client))
    monkeypatch.setattr(helpers, "get_async_client", Mock(return_value=object()))
    hass.async_add_executor_job = AsyncMock(return_value=page_coro())

    result = await helpers.get_authenticated_client(
        hass, "key", None, None, None, "openai"
    )

    assert result is client



class _CatalogContent:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def iter_chunked(self, _size):
        for chunk in self._chunks:
            yield chunk


class _CatalogResponse:
    def __init__(self, status, *, chunks=(), headers=None):
        self.status = status
        self.content = _CatalogContent(chunks)
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _CatalogSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_model_catalog_check_304_requires_existing_etag(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager._save = AsyncMock()
    session = _CatalogSession(_CatalogResponse(304))
    monkeypatch.setattr(
        model_catalog_manager, "async_get_clientsession", Mock(return_value=session)
    )

    status = await manager.async_check(force=True)

    assert status["last_error"] == "Model data check failed; the current catalogue was kept."
    manager._save.assert_awaited()


@pytest.mark.asyncio
async def test_model_catalog_check_304_with_etag_refreshes_check_time(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager.etag = "etag"
    manager._save = AsyncMock()
    monkeypatch.setattr(model_catalog_manager.time, "time", Mock(return_value=1234.0))
    session = _CatalogSession(_CatalogResponse(304))
    monkeypatch.setattr(
        model_catalog_manager, "async_get_clientsession", Mock(return_value=session)
    )

    status = await manager.async_check(force=True)

    assert status["last_checked"] == 1234.0
    assert status["last_error"] is None
    manager._save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_model_catalog_check_transient_http_failures(
    hass, monkeypatch, status_code: int
) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager._save = AsyncMock()
    session = _CatalogSession(_CatalogResponse(status_code))
    monkeypatch.setattr(
        model_catalog_manager, "async_get_clientsession", Mock(return_value=session)
    )

    status = await manager.async_check(force=True)

    assert status["last_error"] == "Model data check failed; the current catalogue was kept."


@pytest.mark.asyncio
async def test_model_catalog_check_nontransient_http_failure(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager._save = AsyncMock()
    session = _CatalogSession(_CatalogResponse(404))
    monkeypatch.setattr(
        model_catalog_manager, "async_get_clientsession", Mock(return_value=session)
    )

    status = await manager.async_check(force=True)

    assert status["last_error"] == "Model data check failed; the current catalogue was kept."


@pytest.mark.asyncio
async def test_model_catalog_check_rejects_oversized_download(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager._save = AsyncMock()
    oversized = b"x" * (model_catalog_manager.MAX_CATALOG_BYTES + 1)
    session = _CatalogSession(_CatalogResponse(200, chunks=[oversized]))
    monkeypatch.setattr(
        model_catalog_manager, "async_get_clientsession", Mock(return_value=session)
    )

    status = await manager.async_check(force=True)

    assert status["last_error"] == "Model data check failed; the current catalogue was kept."


@pytest.mark.asyncio
async def test_model_catalog_check_same_catalogue_has_no_available_update(
    hass, monkeypatch
) -> None:
    import json

    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    manager = model_catalog_manager.ModelCatalogManager(hass)
    manager._save = AsyncMock()
    raw = json.dumps(dict(model_catalog_manager.BUNDLED_CATALOG)).encode()
    # _PreparedCatalog's dict view is raw catalogue-compatible.
    session = _CatalogSession(
        _CatalogResponse(200, chunks=[raw], headers={"ETag": "new-etag"})
    )
    monkeypatch.setattr(
        model_catalog_manager, "async_get_clientsession", Mock(return_value=session)
    )

    status = await manager.async_check(force=True)

    assert status["last_error"] is None
    assert status["update_available"] is False
    assert manager.etag == "new-etag"


@pytest.mark.asyncio
async def test_model_catalog_websocket_reports_action_failures(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        send_error=Mock(),
        send_result=Mock(),
    )
    manager = SimpleNamespace(
        async_check=AsyncMock(
            return_value={
                "last_error": "check failed",
            }
        ),
        status=Mock(return_value={}),
        catalog=None,
    )
    hass.data[model_catalog_manager.DATA_MANAGER] = manager

    await model_catalog_manager.websocket_catalog(
        hass,
        connection,
        {"id": 1, "type": model_catalog_manager.WS_CATALOG, "action": "check", "model": ""},
    )

    connection.send_error.assert_called_once_with(
        1, "model_catalog_check_failed", "check failed"
    )
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_model_catalog_websocket_lookup_returns_capabilities(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog_manager

    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True),
        send_error=Mock(),
        send_result=Mock(),
    )
    manager = SimpleNamespace(
        status=Mock(return_value={"source": "bundled"}),
        catalog=None,
    )
    hass.data[model_catalog_manager.DATA_MANAGER] = manager
    metadata = {"reasoning": {"efforts": ["low"]}}
    monkeypatch.setattr(
        model_catalog_manager, "model_metadata", Mock(return_value=metadata)
    )
    monkeypatch.setattr(
        model_catalog_manager,
        "frontend_capabilities",
        Mock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        model_catalog_manager,
        "catalog_picker_models",
        Mock(return_value=[{"id": "model"}]),
    )

    await model_catalog_manager.websocket_catalog(
        hass,
        connection,
        {"id": 2, "type": model_catalog_manager.WS_CATALOG, "action": "lookup", "model": "model"},
    )

    result = connection.send_result.call_args.args[1]
    assert result["source"] == "bundled"
    assert result["model_capabilities"] == {"ok": True}
    assert result["reasoning_effort_options"] == ["low"]



def test_usage_daily_series_clamps_limit_and_date_range() -> None:
    from custom_components.extended_openai_conversation_responses import usage

    manager = usage.UsageManager.__new__(usage.UsageManager)
    manager.daily = {
        "2026-10-01": {"date": "2026-10-01", "total_tokens": 1},
        "2026-10-02": {"date": "2026-10-02", "total_tokens": 2},
        "2026-10-03": {"date": "2026-10-03", "total_tokens": 3},
    }

    assert [item["date"] for item in manager.daily_series("2026-10-02", "2026-10-03", 1)] == [
        "2026-10-02"
    ]
    assert len(manager.daily_series("2026-10-01", "2026-10-03", 0)) == 1


def test_usage_recent_runs_filters_and_clamps_paging() -> None:
    from custom_components.extended_openai_conversation_responses import usage

    manager = usage.UsageManager.__new__(usage.UsageManager)
    manager.runs = [
        usage.UsageRun(
            run_id="one",
            started_at="2026-10-01T00:00:00+00:00",
            completed_at="2026-10-01T00:01:00+00:00",
            duration_ms=1,
            agent_subentry_id="agent",
            home_assistant_conversation_id=None,
            source_device_id=None,
            successful=True,
        ),
        usage.UsageRun(
            run_id="two",
            started_at="2026-10-02T00:00:00+00:00",
            completed_at="2026-10-02T00:01:00+00:00",
            duration_ms=1,
            agent_subentry_id="agent",
            home_assistant_conversation_id=None,
            source_device_id=None,
            successful=False,
        ),
    ]

    result = manager.recent_runs(limit=9999, offset=-5, successful=False)

    assert [item["run_id"] for item in result["runs"]] == ["two"]
    assert result["offset"] == 0
    assert result["limit"] == usage.MAX_RECENT_LIMIT
    assert result["has_more"] is False


def test_usage_requests_for_run_filters_and_paginates() -> None:
    from custom_components.extended_openai_conversation_responses import usage

    manager = usage.UsageManager.__new__(usage.UsageManager)
    manager.requests = [
        usage.UsageRequest(
            request_id="one",
            run_id="run",
            timestamp="2026-10-01T00:00:00+00:00",
            agent_subentry_id="agent",
            provider="openai",
            model="model",
            api_mode="responses",
            successful=True,
            duration_ms=1,
        ),
        usage.UsageRequest(
            request_id="other",
            run_id="other-run",
            timestamp="2026-10-01T00:00:00+00:00",
            agent_subentry_id="agent",
            provider="openai",
            model="model",
            api_mode="responses",
            successful=True,
            duration_ms=1,
        ),
    ]

    result = manager.requests_for_run("run", limit=0, offset=-1)

    assert [item["request_id"] for item in result["requests"]] == ["one"]
    assert result["offset"] == 0
    assert result["limit"] == 1


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, False),
        ({"speech_processing_enabled": False, "speech_regex_replacements": [{"pattern": "x"}]}, False),
        ({"speech_processing_enabled": True, "speech_regex_replacements": []}, False),
        ({"speech_processing_enabled": True, "speech_regex_replacements": [{"pattern": "x"}]}, True),
    ],
)
def test_speech_custom_replacement_detection(config, expected) -> None:
    from custom_components.extended_openai_conversation_responses import speech

    normalized = {
        speech.CONF_SPEECH_PROCESSING_ENABLED: config.get("speech_processing_enabled", False),
        speech.CONF_SPEECH_REGEX_REPLACEMENTS: config.get("speech_regex_replacements", []),
    }
    assert speech.has_custom_speech_replacements(normalized) is expected


def test_speech_cleanup_mode_decisions() -> None:
    from custom_components.extended_openai_conversation_responses import speech

    enabled = {
        speech.CONF_SPEECH_PROCESSING_ENABLED: True,
        speech.CONF_SPEECH_REGEX_REPLACEMENTS: [],
        speech.CONF_SPEECH_STRIP_MARKDOWN: True,
        speech.CONF_SPEECH_STRIP_URLS: False,
    }
    disabled = {speech.CONF_SPEECH_PROCESSING_ENABLED: False}

    assert speech.needs_async_speech_cleanup(
        "x" * speech.COMPLETED_SPEECH_EXECUTOR_THRESHOLD, enabled
    )
    assert not speech.needs_async_speech_cleanup("short", enabled)
    assert not speech.needs_async_speech_cleanup("x" * 5000, disabled)
    assert speech.streaming_speech_processing_enabled(enabled)
    assert not speech.streaming_speech_processing_enabled(disabled)

    custom = {
        **enabled,
        speech.CONF_SPEECH_REGEX_REPLACEMENTS: [{"pattern": "x", "replacement": ""}],
    }
    assert speech.needs_async_speech_cleanup("short", custom)
    assert not speech.streaming_speech_processing_enabled(custom)


def test_speech_streaming_sanitizer_rejects_tiny_buffer() -> None:
    from custom_components.extended_openai_conversation_responses import speech

    with pytest.raises(ValueError, match="at least 32"):
        speech.StreamingSpeechSanitizer(max_buffer_chars=31)


def test_web_decode_compressed_body_passthrough_unknown_encoding() -> None:
    from custom_components.extended_openai_conversation_responses.functions import web

    body = b"plain"
    assert web._decode_compressed_body(body, "identity", 100) is body



@pytest.mark.asyncio
async def test_native_automation_update_settlement_returns_successful_result() -> None:
    from custom_components.extended_openai_conversation_responses.functions.native import (
        _async_settle_automation_update,
    )

    async def operation():
        return "updated"

    assert await _async_settle_automation_update(operation()) == "updated"


def test_web_decode_gzip_round_trip_and_malformed_payload() -> None:
    import gzip

    import aiohttp

    from custom_components.extended_openai_conversation_responses.functions import web

    payload = b"hello compressed world"
    encoded = gzip.compress(payload)

    assert web._decode_compressed_body(encoded, "gzip", 1024) == payload

    with pytest.raises(aiohttp.ClientPayloadError, match="Malformed|Incomplete"):
        web._decode_compressed_body(b"not-gzip", "gzip", 1024)


def test_web_decode_deflate_round_trip() -> None:
    import zlib

    from custom_components.extended_openai_conversation_responses.functions import web

    payload = b"deflate payload"
    encoded = zlib.compress(payload)

    assert web._decode_compressed_body(encoded, "deflate", 1024) == payload


def test_bash_function_working_directory_uses_config_dir(tmp_path) -> None:
    from custom_components.extended_openai_conversation_responses.functions import bash

    hass = SimpleNamespace(config=SimpleNamespace(config_dir=str(tmp_path)))
    function = bash.BashFunction()

    assert function.get_working_dir(hass) == tmp_path / bash.DEFAULT_WORKING_DIRECTORY
