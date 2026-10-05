"""Meaningful residual coverage after the stable suite reached ~99.65%."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from homeassistant.components import conversation as ha_conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses import conversation


@pytest.mark.asyncio
async def test_agent_initialization_failure_returns_not_ready_result() -> None:
    ready = asyncio.Event()
    ready.set()
    expected = object()
    entity = SimpleNamespace(
        _agent_ready=ready,
        _agent_initialization_failed=True,
        _agent_not_ready_result=Mock(return_value=expected),
    )
    user_input = SimpleNamespace(text="hello", language="en", conversation_id="conv")

    result = await conversation.ExtendedOpenAIAgentEntity._async_process(
        entity, user_input
    )

    assert result is expected
    entity._agent_not_ready_result.assert_called_once_with(user_input)


def test_local_intent_error_without_speech_uses_error_message() -> None:
    response = SimpleNamespace(
        error_code="failed",
        speech={"plain": {"speech": ""}},
        as_dict=Mock(return_value={"error": {"message": "Fallback failure"}}),
    )
    usage = SimpleNamespace(mark_current_run_failed=Mock())
    entity = SimpleNamespace(
        _usage=usage,
        _fire_conversation_finished=Mock(),
        subentry=SimpleNamespace(data={}),
    )
    chat_log = SimpleNamespace(content=[], conversation_id="conv")
    user_input = SimpleNamespace()

    result = conversation.ExtendedOpenAIAgentEntity._local_intent_result(
        entity,
        user_input,
        chat_log,
        SimpleNamespace(response=response, intent_name="HassTurnOn"),
    )

    assert result.response is response
    assert chat_log.content[-1].content == "Fallback failure"
    usage.mark_current_run_failed.assert_called_once_with("LocalIntentError")
    entity._fire_conversation_finished.assert_called_once()


@pytest.mark.asyncio
async def test_persistent_memory_failure_cancels_owned_temporary_prefetch(
    monkeypatch,
) -> None:
    started = asyncio.Event()

    async def temporary_prefetch():
        started.set()
        await asyncio.Event().wait()

    entity = SimpleNamespace(
        _temporary_memory=object(),
        subentry=SimpleNamespace(data={}),
        _async_load_temporary_memories=temporary_prefetch,
        _async_select_memories=AsyncMock(side_effect=RuntimeError("memory failed")),
    )
    monkeypatch.setattr(conversation, "memory_enabled", Mock(return_value=True))
    monkeypatch.setattr(conversation, "sync_memory_embedding_provider", Mock())
    monkeypatch.setattr(conversation, "record_memory_retrieval", Mock())

    token = conversation._TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        with pytest.raises(RuntimeError, match="memory failed"):
            await conversation.ExtendedOpenAIAgentEntity._async_retrieve_memories(
                entity,
                SimpleNamespace(),
                "query",
            )
        await started.wait()
        assert conversation._TEMPORARY_MEMORY_PREFETCH.get() is None
    finally:
        conversation._TEMPORARY_MEMORY_PREFETCH.reset(token)


@pytest.mark.asyncio
async def test_temporary_memory_request_cancellation_drains_prefetch(monkeypatch) -> None:
    child_started = asyncio.Event()

    async def child():
        child_started.set()
        await asyncio.Event().wait()

    child_task = asyncio.create_task(child())
    await child_started.wait()
    entity = SimpleNamespace(
        subentry=SimpleNamespace(data={conversation.CONF_TEMPORARY_MEMORY: "enabled"}),
        _effective_guest_policy=Mock(
            return_value=SimpleNamespace(temporary_memory=True)
        ),
    )
    monkeypatch.setattr(
        conversation,
        "_owner_from_resolved_scope",
        Mock(return_value="user:alice"),
    )

    prefetch_token = conversation._TEMPORARY_MEMORY_PREFETCH.set(child_task)
    temp_scope_token = conversation._ACTIVE_TEMPORARY_SCOPE.set("conversation:one")
    scope_token = conversation._ACTIVE_SCOPE.set(SimpleNamespace())
    try:
        caller = asyncio.create_task(
            conversation.ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories(
                entity
            )
        )
        await asyncio.sleep(0)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert child_task.cancelled()
    finally:
        conversation._ACTIVE_SCOPE.reset(scope_token)
        conversation._ACTIVE_TEMPORARY_SCOPE.reset(temp_scope_token)
        conversation._TEMPORARY_MEMORY_PREFETCH.reset(prefetch_token)


def test_integration_tool_error_propagates_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        conversation,
        "function_execution_errors_propagate",
        Mock(return_value=True),
    )
    entity = SimpleNamespace(entity_id="conversation.agent")
    tool_input = SimpleNamespace(id="call", tool_name="tool")

    with pytest.raises(HomeAssistantError, match=conversation.EXECUTION_FAILED):
        conversation.ExtendedOpenAIAgentEntity._tool_result(
            entity,
            tool_input,
            {"status": "error", "error": "boom"},
        )


def test_guest_denial_propagates_as_guest_mode_denied(monkeypatch) -> None:
    monkeypatch.setattr(
        conversation,
        "function_execution_errors_propagate",
        Mock(return_value=True),
    )
    entity = SimpleNamespace(entity_id="conversation.agent")
    tool_input = SimpleNamespace(id="call", tool_name="tool")

    with pytest.raises(conversation.GuestModeDenied):
        conversation.ExtendedOpenAIAgentEntity._tool_result(
            entity,
            tool_input,
            {"status": "denied", "reason": "guest_mode"},
        )


@pytest.mark.asyncio
async def test_delayed_waiter_stops_when_call_disappears(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import delayed_tools

    manager = delayed_tools.DelayedToolManager(hass)
    manager._started = True
    manager._records["call"] = SimpleNamespace(
        status=delayed_tools._PENDING,
        due_at="2026-10-05T00:00:00+00:00",
        entry_id="entry",
        subentry_id="agent",
    )
    now = datetime(2026, 10, 6, tzinfo=UTC)
    monkeypatch.setattr(
        delayed_tools.dt_util,
        "parse_datetime",
        Mock(return_value=now),
    )
    monkeypatch.setattr(delayed_tools.dt_util, "as_utc", Mock(side_effect=lambda x: x))
    monkeypatch.setattr(delayed_tools.dt_util, "utcnow", Mock(return_value=now))

    async def sleep_and_remove(_seconds):
        manager._records.pop("call", None)

    monkeypatch.setattr(delayed_tools.asyncio, "sleep", sleep_and_remove)

    await manager._async_wait_and_execute("call")

    assert "call" not in manager._records


def test_streaming_speech_finish_flushes_unresolved_link_tail() -> None:
    from custom_components.extended_openai_conversation_responses import speech

    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=True)
    assert sanitizer.feed("[unfinished") == ""
    assert sanitizer.buffered_chars > 0

    final = sanitizer.finish()

    assert "unfinished" in final
    assert sanitizer.buffered_chars == 0


def test_streaming_speech_finish_flushes_pending_format_tail() -> None:
    from custom_components.extended_openai_conversation_responses import speech

    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)
    partial = sanitizer.feed("*unfinished")
    assert partial == ""
    assert sanitizer.buffered_chars > 0

    final = sanitizer.finish()

    assert "unfinished" in final
    assert sanitizer.buffered_chars == 0


def test_quiet_hours_normalizes_spring_forward_gap() -> None:
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours_runtime as quiet,
    )

    zone = ZoneInfo("Europe/Dublin")
    now = datetime(2026, 3, 29, 2, 45, tzinfo=zone)

    period = quiet.quiet_period_for(now, "01:30", "03:30")

    assert period is not None
    assert period.start.hour == 2
    assert period.start.minute == 30
    assert period.start.astimezone(UTC) <= now.astimezone(UTC) < period.end.astimezone(
        UTC
    )


@pytest.mark.asyncio
async def test_quiet_hours_pending_volume_restore_releases_ownership(
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
                "restoration_pending": True,
            }
        },
        "observed_controls": ["media_player.kitchen"],
    }
    monkeypatch.setattr(quiet, "_current_volume", Mock(return_value=0.2))
    manager._async_set_volume = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked(pending_only=True)

    manager._async_set_volume.assert_awaited_once_with("media_player.kitchen", 0.7)
    assert manager._active["controls"] == {}
    assert manager._active["observed_controls"] == []


@pytest.mark.asyncio
async def test_quiet_hours_pending_switch_restore_releases_ownership(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        quiet_hours_runtime as quiet,
    )

    manager = quiet.QuietHoursManager(hass)
    manager._active = {
        "controls": {
            "switch.kitchen_wake_sound": {
                "kind": "switch",
                "original_value": True,
                "quiet_value": False,
                "restoration_pending": True,
            }
        },
        "observed_controls": ["switch.kitchen_wake_sound"],
    }
    monkeypatch.setattr(quiet, "_current_switch", Mock(return_value=False))
    manager._async_set_switch = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked(pending_only=True)

    manager._async_set_switch.assert_awaited_once_with(
        "switch.kitchen_wake_sound", True
    )
    assert manager._active["controls"] == {}


def test_skill_recovery_rolls_back_interrupted_publish(tmp_path) -> None:
    from custom_components.extended_openai_conversation_responses import (
        skill_transactions,
    )

    root = tmp_path / "staging"
    installed = tmp_path / "installed"
    root.mkdir()
    installed.mkdir()
    target = installed / "demo"
    target.mkdir()
    (target / "old.txt").write_text("old")
    staged = root / "demo.download"
    staged.mkdir()
    (staged / "new.txt").write_text("new")
    backup = root / f"demo.backup-{'a' * 32}"

    journal = skill_transactions.prepare_transaction(root, target, backup, staged)
    target.rename(backup)
    staged.rename(target)

    skill_transactions.recover_transaction(journal, installed)

    assert (target / "old.txt").read_text() == "old"
    assert not (target / "new.txt").exists()
    assert not backup.exists()
    assert not journal.exists()


def test_skill_recovery_removes_interrupted_new_install(tmp_path) -> None:
    from custom_components.extended_openai_conversation_responses import (
        skill_transactions,
    )

    root = tmp_path / "staging"
    installed = tmp_path / "installed"
    root.mkdir()
    installed.mkdir()
    target = installed / "demo"
    staged = root / "demo.download"
    staged.mkdir()
    (staged / "new.txt").write_text("new")
    backup = root / f"demo.backup-{'b' * 32}"

    journal = skill_transactions.prepare_transaction(root, target, backup, staged)
    staged.rename(target)

    skill_transactions.recover_transaction(journal, installed)

    assert not target.exists()
    assert not journal.exists()


def test_skill_publish_failure_restores_previous_target(tmp_path, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses.skills import (
        SkillManager,
    )

    target = tmp_path / "installed" / "demo"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "new.txt").write_text("new")
    backup = tmp_path / "backup"
    real_rename = type(staged).rename

    def rename(self, destination):
        if self == staged:
            raise OSError("publish failed")
        return real_rename(self, destination)

    monkeypatch.setattr(type(staged), "rename", rename)

    with pytest.raises(OSError, match="publish failed"):
        SkillManager._activate_staged_skill_sync(staged, target, backup)

    assert (target / "old.txt").read_text() == "old"
    assert not backup.exists()



@pytest.mark.asyncio
async def test_temporary_memory_initialization_is_idempotent_after_first_load() -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    store = SimpleNamespace(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
    manager = temporary_memory.TemporaryMemory(store)

    await manager.async_initialize()
    await manager.async_initialize()

    assert manager.initialized is True
    store.async_load.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_completed_tool_call_id_removes_new_duplicate_only() -> None:
    from custom_components.extended_openai_conversation_responses import tool_exchange
    from custom_components.extended_openai_conversation_responses.function_call_budget import (
        FunctionCallBudget,
    )
    from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
        make_tool_result_content,
    )

    call = llm.ToolInput(
        id="duplicate",
        tool_name="demo",
        tool_args={},
        external=True,
    )
    prior_result = make_tool_result_content(
        agent_id="conversation.agent",
        tool_call_id="duplicate",
        tool_name="demo",
        tool_result={"result": {"status": "ok"}},
    )
    duplicate_assistant = ha_conversation.AssistantContent(
        agent_id="conversation.agent",
        content="",
        tool_calls=[call],
    )
    content = [prior_result, duplicate_assistant]
    chat_log = SimpleNamespace(
        content=content,
        async_add_assistant_content_without_tools=Mock(
            side_effect=lambda item: content.append(item)
        ),
    )
    entity = SimpleNamespace(entity_id="conversation.agent")

    with pytest.raises(HomeAssistantError, match="repeated a completed tool call id"):
        await tool_exchange.async_execute_tool_exchange(
            entity,
            chat_log,
            [call],
            [],
            FunctionCallBudget(10),
            None,
            [],
        )

    assert prior_result in content
    assert duplicate_assistant not in content
    assert len(
        [
            item
            for item in content
            if getattr(item, "tool_call_id", None) == "duplicate"
        ]
    ) == 1


def test_chat_completions_streaming_requests_usage_metadata() -> None:
    from custom_components.extended_openai_conversation_responses import request
    from custom_components.extended_openai_conversation_responses.const import (
        API_MODE_CHAT_COMPLETIONS,
        CONF_API_MODE,
        CONF_CHAT_MODEL,
    )
    from custom_components.extended_openai_conversation_responses.model_catalog import (
        BUNDLED_CATALOG,
    )

    model_id, capabilities = next(
        (model_id, metadata)
        for model_id, metadata in BUNDLED_CATALOG.resolved.items()
        if metadata["api"]["chat_completions"] and metadata["streaming"]
    )

    snapshot = request.build_provider_request_snapshot(
        {
            CONF_CHAT_MODEL: model_id,
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
        },
        {},
        tools_required=False,
        model_capabilities=capabilities,
    )

    assert snapshot.api_mode == API_MODE_CHAT_COMPLETIONS
    assert snapshot.api_kwargs["stream"] is True
    assert snapshot.api_kwargs["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_voice_identity_inactive_mapping_falls_back_to_active_default() -> None:
    from custom_components.extended_openai_conversation_responses import (
        voice_identity_runtime as voice,
    )

    async def get_user(user_id):
        if user_id == "inactive":
            return SimpleNamespace(id=user_id, is_active=False)
        if user_id == "default":
            return SimpleNamespace(id=user_id, is_active=True)
        return None

    agent = SimpleNamespace(
        hass=SimpleNamespace(
            auth=SimpleNamespace(async_get_user=AsyncMock(side_effect=get_user))
        ),
        subentry=SimpleNamespace(
            data={
                voice.CONF_VOICE_SCOPE_POLICY: voice.VOICE_POLICY_DEVICE_MAPPING,
                voice.CONF_VOICE_DEVICE_MAPPINGS: {"device": "user:inactive"},
                voice.CONF_VOICE_UNMAPPED_POLICY: voice.VOICE_POLICY_DEFAULT_USER,
                voice.CONF_VOICE_DEFAULT_USER_ID: "default",
            }
        ),
    )
    user_input = SimpleNamespace(
        context=None,
        device_id="device",
        satellite_id=None,
    )

    assert await voice._active_configured_users(agent, user_input) == frozenset(
        {"default"}
    )


def test_restore_runtime_rebinds_live_agent_and_clears_transient_state(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
        restore_recovery,
        sensor,
        usage,
    )

    key = ("entry", "agent")
    registry_continuity = SimpleNamespace(
        _sessions={"old": 1},
        _memory_bundles={"old": 1},
        _pending_ends={"old"},
        _ignored_conversation_ids={"old": None},
    )
    agent_continuity = SimpleNamespace(
        _sessions={"live": 1},
        _memory_bundles={"live": 1},
        _pending_ends={"live"},
        _ignored_conversation_ids={"live": None},
    )
    registry_rules = SimpleNamespace(_conversation_overrides={"old": 1})
    agent_rules = SimpleNamespace(_conversation_overrides={"live": 1})
    registry_groups = SimpleNamespace(_sessions={"old": 1}, _last_request={"old": 1})
    agent_groups = SimpleNamespace(_sessions={"live": 1}, _last_request={"live": 1})
    fallback_usage = object()
    durable_usage = object()
    agent = SimpleNamespace(
        _continuity=agent_continuity,
        _request_rule_runtime=agent_rules,
        _function_groups_runtime=agent_groups,
        _usage=fallback_usage,
    )

    monkeypatch.setattr(
        restore_recovery, "_active_agent", Mock(return_value=agent)
    )
    rebind = Mock()
    monkeypatch.setattr(sensor, "rebind_usage_sensors", rebind)

    hass.data[continuity._MANAGERS] = {key: registry_continuity}
    hass.data[request_rules._RUNTIMES] = {key: registry_rules}
    hass.data[function_groups._RUNTIMES] = {key: registry_groups}
    hass.data[usage._VOLATILE_USAGE_MANAGERS] = {key: fallback_usage}
    hass.data[restore_recovery.SUBSYSTEM_STATUS_KEY] = {key: {"x": 1}}
    managers = (None, None, None, None, durable_usage)

    restore_recovery.reset_restored_runtime(
        hass,
        "entry",
        "agent",
        managers,
    )

    for manager in (registry_continuity, agent_continuity):
        assert manager._sessions == {}
        assert manager._memory_bundles == {}
        assert manager._pending_ends == set()
        assert manager._ignored_conversation_ids == {}
    assert registry_rules._conversation_overrides == {}
    assert agent_rules._conversation_overrides == {}
    assert registry_groups._sessions == {}
    assert registry_groups._last_request == {}
    assert agent_groups._sessions == {}
    assert agent_groups._last_request == {}
    assert hass.data[continuity._MANAGERS][key] is agent_continuity
    assert hass.data[request_rules._RUNTIMES][key] is agent_rules
    assert hass.data[function_groups._RUNTIMES][key] is agent_groups
    assert agent._usage is durable_usage
    assert key not in hass.data[usage._VOLATILE_USAGE_MANAGERS]
    assert key not in hass.data[restore_recovery.SUBSYSTEM_STATUS_KEY]
    rebind.assert_called_once_with(
        hass, "agent", fallback_usage, durable_usage
    )


@pytest.mark.asyncio
async def test_live_function_group_change_blocks_stale_tool_execution(
    monkeypatch,
) -> None:
    old_groups = [{"id": "old", "name": "Old", "functions": ["demo"]}]
    live_groups = [{"id": "new", "name": "New", "functions": ["demo"]}]
    function_tool = {
        "spec": {"name": "demo"},
        "function": {"type": "native", "name": "execute_service"},
    }
    live_data = {"function_groups": live_groups}
    entity = SimpleNamespace(
        _effective_guest_policy=Mock(
            return_value=SimpleNamespace(guest_active=False)
        ),
        _configured_function_tools_from_data=Mock(return_value=[function_tool]),
    )
    monkeypatch.setattr(
        conversation,
        "current_configuration_data",
        Mock(return_value=live_data),
    )
    monkeypatch.setattr(
        conversation,
        "validate_function_groups",
        Mock(return_value=live_groups),
    )
    token = conversation._ACTIVE_FUNCTION_CONFIG.set(([function_tool], old_groups))
    try:
        with pytest.raises(conversation.FunctionNotFound, match="demo"):
            await conversation.ExtendedOpenAIAgentEntity._async_dispatch_function_tool(
                entity,
                function_tool,
                SimpleNamespace(id="call", tool_name="demo", tool_args={}),
                None,
                [],
            )
    finally:
        conversation._ACTIVE_FUNCTION_CONFIG.reset(token)
