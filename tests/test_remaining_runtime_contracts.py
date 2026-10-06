"""Conversation error outcomes, control restoration and broadcast lifetime checks."""

from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as module,
    intercom,
    quiet_hours_runtime as quiet,
    transfer,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    LocalIntentResult,
)
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
from tests.test_intercom import _coverage_message
from tests.test_management_function_repair import _mixed_legacy_tool_data

Agent = module.ExtendedOpenAIAgentEntity


def _agent(hass):
    entity = object.__new__(Agent)
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity.entry = SimpleNamespace(entry_id="entry", data={})
    entity.subentry = SimpleNamespace(subentry_id="agent", data={})
    entity._usage = None
    entity._fire_conversation_finished = Mock()
    return entity


async def test_local_intent_error_is_recorded_and_never_falls_through_to_provider(
    hass, monkeypatch
):
    entity = _agent(hass)
    entity._async_handle_message_with_ha_tools = AsyncMock()
    monkeypatch.setattr(
        module,
        "async_try_handle_local_intent",
        AsyncMock(side_effect=HomeAssistantError("local action failed")),
    )
    log = SimpleNamespace(conversation_id="conversation", content=[])
    user = SimpleNamespace(language="en", conversation_id="conversation")
    metadata = {}
    token = module._PROCESS_METADATA.set(metadata)
    try:
        result = await entity._async_dispatch_message(
            user, log, {}, try_locally=True, guest_active=False
        )
    finally:
        module._PROCESS_METADATA.reset(token)
    assert result.response.error_code is not None
    assert metadata["handled_locally"] is True
    assert "local action failed" in log.content[-1].content
    entity._async_handle_message_with_ha_tools.assert_not_awaited()
    entity._fire_conversation_finished.assert_called_once()


async def test_local_intent_failure_without_speech_uses_error_message(hass):
    entity = _agent(hass)
    entity._usage = SimpleNamespace(mark_current_run_failed=Mock())
    response = intent.IntentResponse(language="en")
    response.async_set_error(
        intent.IntentResponseErrorCode.UNKNOWN, "Entity unavailable"
    )
    log = SimpleNamespace(conversation_id="conversation", content=[])
    metadata = {}
    token = module._PROCESS_METADATA.set(metadata)
    try:
        result = entity._local_intent_result(
            SimpleNamespace(), log, LocalIntentResult(response, "HassTurnOn")
        )
    finally:
        module._PROCESS_METADATA.reset(token)
    assert result.response is response and not result.continue_conversation
    assert log.content[-1].content == "Entity unavailable"
    assert metadata == {"handled_locally": True, "matched_intent": "HassTurnOn"}
    entity._usage.mark_current_run_failed.assert_called_once_with("LocalIntentError")


async def test_completed_message_applies_deferred_speech_cleanup(hass, monkeypatch):
    entity = _agent(hass)
    response = intent.IntentResponse(language="en")
    result = conversation.ConversationResult(
        response=response, conversation_id="conversation"
    )

    async def generate(_self, _user, _log, _options, *, deferred_speech):
        deferred_speech.append(("raw speech", {"speech_cleanup": "custom"}))
        return result

    monkeypatch.setattr(Agent, "_async_generate_message", generate)
    cleanup = AsyncMock(return_value="clean speech")
    monkeypatch.setattr(module, "async_process_speech_text", cleanup)
    assert (
        await entity._async_handle_message(SimpleNamespace(), SimpleNamespace())
        is result
    )
    assert response.speech["plain"]["speech"] == "clean speech"
    cleanup.assert_awaited_once_with(hass, "raw speech", {"speech_cleanup": "custom"})


@pytest.mark.parametrize("mode", [None, SimpleNamespace(is_active=lambda: False)])
def test_normal_owner_policy_is_reused_when_guest_mode_remains_inactive(hass, mode):
    entity = _agent(hass)
    entity._guest_mode = mode
    entity._resolve_live_guest_policy = Mock(
        side_effect=AssertionError("no policy recomputation needed")
    )
    policy = GuestCapabilityPolicy.unrestricted()
    token = module._ACTIVE_GUEST_POLICY.set(policy)
    try:
        assert entity._effective_guest_policy() is policy
    finally:
        module._ACTIVE_GUEST_POLICY.reset(token)
    entity._resolve_live_guest_policy.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "temperature", "source_ids": 5},
        {"query": "temperature", "source_ids": [7]},
        {"query": "temperature", "limit": True},
        {"query": "temperature", "limit": "5"},
    ],
)
async def test_knowledge_search_rejects_invalid_filters_before_retrieval(
    hass, arguments
):
    entity = _agent(hass)
    entity.subentry.data[module.CONF_KNOWLEDGE_ENABLED] = True
    entity._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    entity._knowledge = SimpleNamespace(
        source_count=1,
        initialized=True,
        resolve_source_filter=Mock(),
        async_search=AsyncMock(),
    )
    with pytest.raises(ValueError, match="invalid type"):
        await entity._async_execute_knowledge_tool("search", arguments)
    entity._knowledge.resolve_source_filter.assert_not_called()
    entity._knowledge.async_search.assert_not_awaited()


async def test_native_tool_dispatch_refreshes_assist_exposure(tmp_path):
    entity = _agent(HomeAssistant(str(tmp_path)))
    fresh = [{"entity_id": "light.current"}]
    entity._get_exposed_entities = Mock(return_value=fresh)
    content = conversation.ToolResultContent(
        agent_id="agent",
        tool_call_id="call",
        tool_name="native",
        tool_result={"result": "ok"},
    )
    entity._async_dispatch_function_tool = AsyncMock(return_value=content)
    tool, tool_input, context = (
        {"function": {"type": "native"}},
        SimpleNamespace(id="call", tool_name="native"),
        SimpleNamespace(),
    )
    assert (
        await entity._execute_function_tool(
            tool, tool_input, context, [{"entity_id": "light.stale"}]
        )
        is content
    )
    entity._async_dispatch_function_tool.assert_awaited_once_with(
        tool, tool_input, context, fresh
    )


async def test_restore_function_validation_quarantines_invalid_named_siblings(
    hass, monkeypatch
):
    config, _, valid = _mixed_legacy_tool_data()
    validate = AsyncMock()
    monkeypatch.setattr(transfer, "async_validate_request_rule_functions", validate)
    await transfer._async_validate_request_rule_function_dependencies(
        hass, {"rules": [{"id": "rule"}]}, config
    )
    assert validate.await_args.args[2] == [valid]
    assert validate.await_args.kwargs["quarantined_names"] == {
        valid["spec"]["name"] + "_broken"
    }


async def test_broadcast_disabled_during_idle_stability_is_not_delivered(
    hass, monkeypatch
):
    manager = intercom.IntercomManager(hass)
    manager._enabled = True
    entity_id = "assist_satellite.kitchen"
    message = _coverage_message(entity_id)
    manager._queues[entity_id] = deque([message])
    hass.states.get.return_value = SimpleNamespace(state="idle")

    async def disable(_seconds):
        manager._enabled = False

    monkeypatch.setattr(intercom.asyncio, "sleep", disable)
    await manager._async_drain(entity_id)
    assert message.deliveries[entity_id].status == "expired"
    assert message.deliveries[entity_id].detail == "broadcast_disabled"
    hass.services.async_call.assert_not_awaited()


async def test_broadcast_state_generation_change_restarts_stability_interval(
    hass, monkeypatch
):
    manager = intercom.IntercomManager(hass)
    manager._enabled = True
    entity_id = "assist_satellite.kitchen"
    message = _coverage_message(entity_id)
    manager._queues[entity_id] = deque([message])
    hass.states.get.return_value = SimpleNamespace(state="idle")
    sleeps = []

    async def change_once(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            manager._state_generations[entity_id] = 1

    monkeypatch.setattr(intercom.asyncio, "sleep", change_once)
    await manager._async_drain(entity_id)
    assert len(sleeps) == 2
    hass.services.async_call.assert_awaited_once()
    assert message.deliveries[entity_id].status == "delivered"


async def test_broadcast_shutdown_expires_queue_unsubscribes_and_stays_closed(hass):
    manager = intercom.IntercomManager(hass)
    entity_id = "assist_satellite.kitchen"
    message = _coverage_message(entity_id)
    manager._queues[entity_id] = deque([message])
    unsubscribe = Mock()
    manager._unsub_state = unsubscribe
    await manager.async_shutdown()
    assert message.deliveries[entity_id].status == "expired"
    assert message.deliveries[entity_id].detail == "integration_removed"
    assert manager._queues == {}
    unsubscribe.assert_called_once()
    hass.states.async_all.reset_mock()
    manager._refresh_state_listener()
    hass.states.async_all.assert_not_called()


async def test_quiet_hours_pending_restore_releases_observed_control(hass, monkeypatch):
    manager = quiet.QuietHoursManager(hass)
    manager._store = SimpleNamespace(async_save=AsyncMock())
    manager._active = {
        "controls": {
            "switch.wake": {
                "kind": "switch",
                "original_value": True,
                "quiet_value": False,
                "restoration_pending": True,
            }
        },
        "observed_controls": ["switch.wake"],
    }
    monkeypatch.setattr(quiet, "_current_switch", lambda *_: False)
    await manager._async_restore_locked(pending_only=True)
    assert manager._active["observed_controls"] == []
    assert manager._active["controls"] == {}
    hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_on", {"entity_id": "switch.wake"}, blocking=True
    )


async def test_quiet_hours_new_period_retains_and_retries_unavailable_control(
    hass, monkeypatch
):
    manager = quiet.QuietHoursManager(hass)
    manager._initialized = True
    manager._config = quiet.QuietHoursConfig(enabled=True)
    manager._store = SimpleNamespace(async_save=AsyncMock())
    manager._publish_state = Mock()
    now = datetime(2026, 10, 5, 23, tzinfo=UTC)
    period = quiet.QuietPeriod(now - timedelta(hours=1), now + timedelta(hours=8))
    manager._active = {
        "period_started_at": "old",
        "controls": {
            "media_player.offline": {
                "kind": "volume",
                "original_value": 0.8,
                "quiet_value": 0.2,
            }
        },
    }
    monkeypatch.setattr(quiet, "quiet_period_for", lambda *_: period)
    monkeypatch.setattr(quiet, "_current_volume", lambda *_: None)
    monkeypatch.setattr(quiet, "discover_satellite_capabilities", lambda *_: [])
    await manager.async_reconcile(now=now)
    assert manager._active["period_started_at"] == period.start.isoformat()
    assert manager._active["period_ends_at"] == period.end.isoformat()
    assert manager._active["controls"]["media_player.offline"]["restoration_pending"]
    manager._store.async_save.assert_awaited()
    hass.services.async_call.assert_not_awaited()
