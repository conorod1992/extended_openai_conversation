"""Final reachable residual coverage.

This file intentionally covers the remaining reachable branches after the broad
coverage hardening work. Structurally impossible invariant guards are excluded.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import aiohttp
import pytest

from homeassistant.exceptions import HomeAssistantError


@pytest.mark.asyncio
async def test_removed_subentries_delete_only_stale_known_agents(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import agent_deletion

    delete = AsyncMock()
    monkeypatch.setattr(agent_deletion, "async_delete_agent_data", delete)
    entry = SimpleNamespace(
        entry_id="entry",
        subentries={"current": object(), "new": object()},
    )
    hass.data[agent_deletion.KNOWN_AGENTS] = {
        "entry": {"current", "removed"},
    }

    await agent_deletion.async_delete_removed_subentries(hass, entry)

    delete.assert_awaited_once_with(hass, "entry", "removed")
    assert hass.data[agent_deletion.KNOWN_AGENTS]["entry"] == {"current", "new"}


@pytest.mark.asyncio
async def test_entry_deletion_discovers_orphaned_store_identity(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import agent_deletion

    orphan_store = (
        f"{agent_deletion.DOMAIN}.memory.entry.orphaned.private"
    )
    hass.async_add_executor_job = AsyncMock(return_value=[orphan_store])
    hass.data[agent_deletion.KNOWN_AGENTS] = {"entry": {"known"}}
    delete = AsyncMock()
    monkeypatch.setattr(agent_deletion, "async_delete_agent_data", delete)
    entry = SimpleNamespace(entry_id="entry", subentries={"live": object()})

    await agent_deletion.async_delete_entry_data(hass, entry)

    deleted = {call.args[2] for call in delete.await_args_list}
    assert deleted == {"live", "known", "orphaned"}
    assert "entry" not in hass.data[agent_deletion.KNOWN_AGENTS]


def test_context_summary_prunes_oldest_completed_until_below_limit(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import context_summary

    monkeypatch.setattr(context_summary, "MAX_PENDING_CONTEXT_SUMMARIES", 3)
    manager = context_summary.DeferredContextSummaryManager()
    loop = asyncio.new_event_loop()
    try:
        pending = {}
        for index in range(4):
            future = loop.create_future()
            future.set_result(context_summary.ContextSummaryResult([], False))
            pending[str(index)] = context_summary.PendingContextSummary(
                snapshot_length=1,
                snapshot_signature="sig",
                fallback_content=[],
                task=future,
                created_at=float(index),
            )
        manager._pending = pending

        manager._prune_completed()

        assert list(manager._pending) == ["2", "3"]
    finally:
        loop.close()


def test_continuity_lock_user_reference_count_keeps_then_removes_lock() -> None:
    from custom_components.extended_openai_conversation_responses import continuity

    manager = continuity.ConversationContinuity("agent")
    lock = asyncio.Lock()
    manager._ha_default_locks["conversation"] = lock
    manager._ha_default_lock_users["conversation"] = 2

    manager._drop_ha_default_lock_user("conversation", lock)
    assert manager._ha_default_lock_users["conversation"] == 1
    assert manager._ha_default_locks["conversation"] is lock

    manager._drop_ha_default_lock_user("conversation", lock)
    assert "conversation" not in manager._ha_default_lock_users
    assert "conversation" not in manager._ha_default_locks


def test_continuity_does_not_remove_replaced_lock() -> None:
    from custom_components.extended_openai_conversation_responses import continuity

    manager = continuity.ConversationContinuity("agent")
    old = asyncio.Lock()
    new = asyncio.Lock()
    manager._ha_default_locks["conversation"] = new
    manager._ha_default_lock_users["conversation"] = 1

    manager._drop_ha_default_lock_user("conversation", old)

    assert manager._ha_default_locks["conversation"] is new
    assert "conversation" not in manager._ha_default_lock_users


@pytest.mark.asyncio
@pytest.mark.parametrize("session_key", [None, "scope-key"])
async def test_archive_unretained_session_uses_in_memory_fast_path(session_key) -> None:
    from custom_components.extended_openai_conversation_responses import (
        conversation_archive,
    )

    storage = SimpleNamespace(async_save_metadata=AsyncMock())
    archive = conversation_archive.ConversationArchive(storage, "agent")
    archive._initialized = True
    session = conversation_archive.ArchiveSession(
        session_id="session",
        home_assistant_conversation_id=None,
        agent_subentry_id="agent",
        scope_id="user:alice",
        scope_type="user",
        scope_source="voice",
        source_device_id=None,
        started_at="2026-10-06T00:00:00+00:00",
        last_message_at="2026-10-06T00:00:00+00:00",
        title="",
        turn_count=0,
        retention_state="unretained",
    )

    await archive._async_publish_session_locked(session_key, session)

    assert archive._sessions["session"] is session
    if session_key is None:
        assert archive._active == {}
    else:
        assert archive._active[session_key] == "session"
    storage.async_save_metadata.assert_not_awaited()


def test_debug_model_timing_without_active_usage_run_records_duration(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import debug

    trace = SimpleNamespace(phases_ms={}, usage_run_id=None)
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    entity = SimpleNamespace(
        _usage=SimpleNamespace(current_run=Mock(return_value=None))
    )
    ticks = iter([10.0, 10.025, 10.025, 10.025])
    monkeypatch.setattr(debug.time, "monotonic", lambda: next(ticks, 10.025))
    try:
        with debug.model_path_timing(entity):
            pass
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)

    assert trace.usage_run_id is None
    assert trace.phases_ms["model_path_total"] >= 0


def test_function_group_quarantine_all_ignores_malformed_group_members(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import agent_config
    from custom_components.extended_openai_conversation_responses import (
        function_tool_quarantine as quarantine,
    )

    captured = {}

    def validate(value, tools):
        captured["value"] = value
        return value

    monkeypatch.setattr(agent_config, "validate_function_groups", validate)
    all_token = quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.set(True)
    names_token = quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.set(frozenset())
    try:
        value = [
            "not-a-group",
            {"id": "bad-functions", "functions": "not-a-list"},
            {"id": "mixed", "functions": ["one", 7, None]},
        ]
        result = quarantine._runtime_validate_function_groups(value, [])
    finally:
        quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.reset(names_token)
        quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.reset(all_token)

    assert result[0] == "not-a-group"
    assert result[1]["functions"] == "not-a-list"
    assert result[2]["functions"] == [7, None]


def test_function_group_named_quarantine_removes_only_named_tools(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import agent_config
    from custom_components.extended_openai_conversation_responses import (
        function_tool_quarantine as quarantine,
    )

    monkeypatch.setattr(
        agent_config,
        "validate_function_groups",
        lambda value, tools: value,
    )
    all_token = quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.set(False)
    names_token = quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.set(
        frozenset({"bad"})
    )
    try:
        result = quarantine._runtime_validate_function_groups(
            [{"id": "group", "functions": ["good", "bad", 9]}],
            [],
        )
    finally:
        quarantine._RUNTIME_QUARANTINED_FUNCTION_NAMES.reset(names_token)
        quarantine._RUNTIME_QUARANTINE_ALL_FUNCTIONS.reset(all_token)

    assert result[0]["functions"] == ["good", 9]


def test_knowledge_chunk_overlap_prefers_line_boundary() -> None:
    from custom_components.extended_openai_conversation_responses import knowledge

    prefix = "a" * (knowledge.CHUNK_SIZE - 100)
    content = prefix + "\n" + ("b" * 500)

    chunks = knowledge._split_chunks(content)

    assert len(chunks) >= 2
    first_start, first_text = chunks[0]
    second_start, second_text = chunks[1]
    assert first_start == 0
    assert first_text.endswith("a" * 10)
    assert second_start > 0
    assert second_text


@pytest.mark.asyncio
async def test_overview_primary_falls_back_when_setup_health_projection_fails(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
    )

    monkeypatch.setattr(loading, "peek_function_tool_health", Mock(return_value=None))
    monkeypatch.setattr(
        loading,
        "_agent_snapshot",
        Mock(return_value={"knowledge_source_count": 0}),
    )
    monkeypatch.setattr(loading, "settings_snapshot", Mock(return_value={}))
    monkeypatch.setattr(
        loading,
        "build_setup_health_facts",
        Mock(side_effect=RuntimeError("projection failed")),
    )
    entry = SimpleNamespace(entry_id="entry", data={}, runtime_data=None)
    subentry = SimpleNamespace(subentry_id="agent", data={})

    result = await loading.async_overview_primary(
        hass, entry, subentry, is_admin=True
    )

    assert result["setup_health"]["unavailable"] is True
    assert result["setup_health"]["function_tools"] == {"loading": True}
    assert result["setup_health"]["provider_runtime"]["client_loaded"] is False


@pytest.mark.asyncio
async def test_overview_primary_marks_function_health_loading_when_unknown(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
    )

    monkeypatch.setattr(loading, "peek_function_tool_health", Mock(return_value=None))
    monkeypatch.setattr(
        loading,
        "_agent_snapshot",
        Mock(return_value={"knowledge_source_count": 0}),
    )
    monkeypatch.setattr(loading, "settings_snapshot", Mock(return_value={}))
    monkeypatch.setattr(
        loading,
        "build_setup_health_facts",
        Mock(return_value={"memory": {}, "knowledge": {}}),
    )
    entry = SimpleNamespace(entry_id="entry", data={}, runtime_data=object())
    subentry = SimpleNamespace(subentry_id="agent", data={})

    result = await loading.async_overview_primary(
        hass, entry, subentry, is_admin=False
    )

    assert result["setup_health"]["function_tools"] == {"loading": True}
    assert result["setup_health"]["memory"]["loading"] is True
    assert result["setup_health"]["knowledge"]["loading"] is True


def test_quiet_hours_autumn_fold_uses_last_end_occurrence() -> None:
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours_runtime as quiet,
    )

    zone = ZoneInfo("Europe/Dublin")
    now = datetime(2026, 10, 25, 1, 15, tzinfo=zone, fold=1)

    period = quiet.quiet_period_for(now, "00:30", "01:30")

    assert period is not None
    assert period.end.fold == 1
    assert period.start.astimezone(UTC) <= now.astimezone(UTC) < period.end.astimezone(
        UTC
    )


@pytest.mark.asyncio
async def test_quiet_hours_independent_volume_change_releases_control(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours_runtime as quiet,
    )

    manager = quiet.QuietHoursManager(hass)
    manager._active = {
        "controls": {
            "media_player.kitchen": {
                "kind": "volume",
                "original_value": 0.7,
                "quiet_value": 0.2,
            }
        }
    }
    monkeypatch.setattr(quiet, "_current_volume", Mock(return_value=0.5))
    manager._async_set_volume = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked(pending_only=False)

    manager._async_set_volume.assert_not_awaited()
    assert manager._active is None


@pytest.mark.asyncio
async def test_quiet_hours_failed_restore_retains_control_for_retry(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours_runtime as quiet,
    )

    manager = quiet.QuietHoursManager(hass)
    manager._active = {
        "controls": {
            "switch.wake": {
                "kind": "switch",
                "original_value": True,
                "quiet_value": False,
            }
        }
    }
    monkeypatch.setattr(quiet, "_current_switch", Mock(return_value=False))
    manager._async_set_switch = AsyncMock(side_effect=RuntimeError("offline"))
    manager._async_save_locked = AsyncMock()
    manager._log_control_failure = Mock()

    await manager._async_restore_locked(pending_only=False)

    assert "switch.wake" in manager._active["controls"]
    manager._log_control_failure.assert_called_once()


def test_request_rule_fuzzy_fallback_skips_rule_already_seen_strictly() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    seen_rule = {
        "id": "seen",
        "name": "Seen",
        "match_type": "equals",
        "order": 0,
    }
    other_rule = {
        "id": "other",
        "name": "Other",
        "match_type": "contains",
        "order": 1,
    }
    settings = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy_threshold": 0,
    }
    strict = request_rules.CompiledPhrase("hello", normalized="hello")
    seen_fuzzy = request_rules.CompiledPhrase("helo", normalized="helo")
    other_fuzzy = request_rules.CompiledPhrase("hello", normalized="hello")
    snapshot = request_rules._MatchingSnapshot(
        phrases=((seen_rule, settings, strict),),
        wording_groups=(),
        deterministic=((seen_rule, settings, strict),),
        fuzzy=(
            (seen_rule, settings, seen_fuzzy),
            (other_rule, settings, other_fuzzy),
        ),
    )
    cursor = request_rules._MatchCursor(snapshot, "hello")

    first = cursor.next_match()
    second = cursor.next_match()

    assert first is not None and first.rule["id"] == "seen"
    assert second is not None and second.rule["id"] == "other"


@pytest.mark.asyncio
async def test_template_setup_failure_does_not_remove_replacement_manager(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import template

    replacement = object()
    manager = SimpleNamespace(
        async_on_unload=AsyncMock(),
        acquire=Mock(),
    )

    async def fail_setup():
        hass.data[template.DOMAIN][template.DATA_TEMPLATE_MANAGER] = replacement
        raise RuntimeError("setup failed")

    manager.async_setup = fail_setup
    monkeypatch.setattr(template, "async_setup_delayed_tools", AsyncMock())
    monkeypatch.setattr(
        template,
        "ExtendedOpenAITemplateManager",
        Mock(return_value=manager),
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        await template.async_setup_templates(hass, "entry")

    assert hass.data[template.DOMAIN][template.DATA_TEMPLATE_MANAGER] is replacement
    manager.async_on_unload.assert_awaited_once()


def test_transfer_scope_ids_respect_selected_sections_only() -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    prepared = SimpleNamespace(
        memories=[
            SimpleNamespace(user_id="memory-user"),
        ],
        temporary_memories=[
            SimpleNamespace(owner_scope_id="user:temporary-user"),
        ],
        archive_sessions=[
            SimpleNamespace(scope_id="user:archive-user"),
        ],
        config={
            transfer.CONF_VOICE_DEFAULT_USER_ID: "config-user",
            transfer.CONF_VOICE_DEVICE_MAPPINGS: {
                "device": "user:mapped-user",
            },
        },
    )

    assert transfer.transfer_user_scope_ids(
        prepared, [transfer.SECTION_PERSISTENT_MEMORY]
    ) == frozenset({"memory-user"})
    assert transfer.transfer_user_scope_ids(
        prepared,
        [
            transfer.SECTION_TEMPORARY_MEMORY,
            transfer.SECTION_CONVERSATION_ARCHIVE,
        ],
    ) == frozenset({"temporary-user", "archive-user"})
    assert transfer.transfer_user_scope_ids(
        prepared, [transfer.SECTION_CONFIGURATION]
    ) == frozenset({"config-user", "mapped-user"})


@pytest.mark.asyncio
async def test_transfer_mapping_plan_rejects_malformed_mapping(hass) -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    prepared = SimpleNamespace(
        memories=[SimpleNamespace(user_id="source")],
        temporary_memories=[],
        archive_sessions=[],
        config={},
    )
    hass.auth.async_get_users = AsyncMock(return_value=[])

    with pytest.raises(transfer.backup.BackupError, match="must be an object"):
        await transfer.async_user_scope_mapping_plan(
            hass,
            prepared,
            [transfer.SECTION_PERSISTENT_MEMORY],
            supplied=["not", "a", "mapping"],
        )


@pytest.mark.asyncio
async def test_transfer_mapping_plan_reports_and_resolves_missing_users(hass) -> None:
    from custom_components.extended_openai_conversation_responses import transfer

    prepared = SimpleNamespace(
        memories=[
            SimpleNamespace(user_id="existing"),
            SimpleNamespace(user_id="missing"),
        ],
        temporary_memories=[],
        archive_sessions=[],
        config={},
    )
    hass.auth.async_get_users = AsyncMock(
        return_value=[
            SimpleNamespace(id="existing", name="Existing"),
            SimpleNamespace(id="target", name="Target"),
        ]
    )

    unresolved = await transfer.async_user_scope_mapping_plan(
        hass,
        prepared,
        [transfer.SECTION_PERSISTENT_MEMORY],
    )
    resolved = await transfer.async_user_scope_mapping_plan(
        hass,
        prepared,
        [transfer.SECTION_PERSISTENT_MEMORY],
        supplied={"missing": "target"},
    )

    assert unresolved["missing_source_user_ids"] == ["missing"]
    assert unresolved["resolved"] == {"existing": "existing"}
    assert resolved["missing_source_user_ids"] == []
    assert resolved["resolved"] == {
        "existing": "existing",
        "missing": "target",
    }


@pytest.mark.asyncio
async def test_usage_shutdown_returns_immediately_for_deleted_recovery_gate(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import usage

    storage = usage.RecoveryGuardedStore.__new__(usage.RecoveryGuardedStore)
    storage._recovery_gate = SimpleNamespace(deleted=True, recovery_required=False)
    manager = usage.UsageManager(storage)
    manager._async_save_aggregates = AsyncMock()
    manager._async_save_details = AsyncMock()

    await manager.async_shutdown()

    manager._async_save_aggregates.assert_not_awaited()
    manager._async_save_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_usage_shutdown_swallows_storage_error_after_gate_deleted(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import usage

    gate = SimpleNamespace(deleted=False, recovery_required=False)
    storage = usage.RecoveryGuardedStore.__new__(usage.RecoveryGuardedStore)
    storage._recovery_gate = gate
    manager = usage.UsageManager(storage)

    @asynccontextmanager
    async def failing_lock(_storage, _lock):
        gate.deleted = True
        raise HomeAssistantError("deleted during shutdown")
        yield

    monkeypatch.setattr(usage, "async_storage_lock", failing_lock)

    await manager.async_shutdown()

    assert gate.deleted is True


def test_management_settings_reject_unknown_field() -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    with pytest.raises(HomeAssistantError, match="Unknown settings"):
        management_ui._validate_settings({"definitely_unknown_setting": True})


def test_guest_custom_group_policy_ignores_non_string_function_members(hass) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    tools = [
        {
            "spec": {"name": "safe"},
            "function": {"type": "native", "name": "execute_service"},
            "enabled": True,
        }
    ]
    options = {
        guest_mode.CONF_GUEST_FUNCTION_POLICY: "custom",
        guest_mode.CONF_GUEST_ALLOWED_GROUP_IDS: ["group"],
        guest_mode.CONF_FUNCTION_GROUPS: [
            {"id": "group", "functions": ["safe", 123, None]},
        ],
    }

    policy = guest_mode._resolve_exclusion_policy(
        hass,
        options,
        tools,
        exposed_entities=[],
    )

    assert policy.configured_tool_names <= {"safe"}


def test_truncated_zstd_response_is_rejected() -> None:
    from compression.zstd import ZstdCompressor

    from custom_components.extended_openai_conversation_responses.functions import web

    encoded = ZstdCompressor().compress(b"payload" * 100)
    truncated = encoded[:-1]

    with pytest.raises(aiohttp.ClientPayloadError):
        web._decode_zstd(truncated, 10000)
