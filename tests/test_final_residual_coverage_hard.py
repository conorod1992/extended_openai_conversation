"""Harder residual branches with real callable seams."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError


def test_repair_persistence_reuses_tools_while_preserving_absence(monkeypatch, hass) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
        management_loading_performance as loading,
    )

    tools = [{"spec": {"name": "tool"}}]
    groups = [{"id": "group", "functions": ["tool"]}]
    subentry = SimpleNamespace(data={}, title="Agent")
    entry = SimpleNamespace()
    projection = SimpleNamespace(
        snapshot={
            repair.CONF_FUNCTION_TOOLS: tools,
            repair.CONF_FUNCTION_GROUPS: groups,
        }
    )
    monkeypatch.setattr(repair, "require_agent_config_revision", Mock())
    monkeypatch.setattr(repair, "persisted_config_projection", Mock(return_value=projection))
    monkeypatch.setattr(
        repair,
        "_strict_merge_agent_config",
        Mock(
            return_value={
                repair.CONF_FUNCTION_TOOLS: tools,
                repair.CONF_FUNCTION_GROUPS: groups,
            }
        ),
    )
    monkeypatch.setattr(repair, "preserve_legacy_guest_policy", lambda raw, value: value)
    update = Mock()
    monkeypatch.setattr(repair, "update_live_subentry", update)
    monkeypatch.setattr(repair, "saved_agent_config_revision", Mock(return_value="rev"))
    monkeypatch.setattr(repair, "seed_persisted_config_projection", Mock())
    monkeypatch.setattr(
        loading,
        "_snapshot_normalized_configuration",
        lambda value, validated=False: dict(value),
    )

    result = repair.persist_valid_function_configuration(
        hass, entry, subentry, tools, groups
    )

    persisted = update.call_args.kwargs["data"]
    assert repair.CONF_FUNCTION_TOOLS not in persisted
    assert result["functions"] == tools
    assert result["function_groups"] == groups


@pytest.mark.asyncio
async def test_memory_move_between_personal_users_is_rejected(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    request = management_ui._ManagementRequest(
        hass=hass,
        user_id="alice",
        is_admin=True,
        message={
            "section": "memories",
            "action": "update",
            "scope_id": "user:alice",
            "target_scope_id": "user:bob",
            "memory_id": "m1",
        },
        entry_id="entry",
        subentry_id="agent",
        entry=SimpleNamespace(),
        subentry=SimpleNamespace(data={}),
    )
    monkeypatch.setattr(management_ui, "async_get_memory", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(management_ui, "_require_existing_user_destination", AsyncMock())

    with pytest.raises(HomeAssistantError, match="Personal and Shared scopes"):
        await management_ui.async_memories_command(request)


@pytest.mark.asyncio
async def test_memory_move_to_disabled_shared_scope_is_rejected(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    request = management_ui._ManagementRequest(
        hass=hass,
        user_id="alice",
        is_admin=True,
        message={
            "section": "memories",
            "action": "update",
            "scope_id": "user:alice",
            "target_scope_id": management_ui.SHARED_HOUSEHOLD_SCOPE_ID,
            "memory_id": "m1",
        },
        entry_id="entry",
        subentry_id="agent",
        entry=SimpleNamespace(),
        subentry=SimpleNamespace(
            data={management_ui.CONF_SHARED_MEMORY_MODE: management_ui.SHARED_MEMORY_DISABLED}
        ),
    )
    monkeypatch.setattr(management_ui, "async_get_memory", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(management_ui, "_require_existing_user_destination", AsyncMock())

    with pytest.raises(HomeAssistantError, match="Shared household memory is disabled"):
        await management_ui.async_memories_command(request)


@pytest.mark.asyncio
async def test_quiet_hours_failed_volume_apply_releases_unchanged_control(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import quiet_hours

    manager = quiet_hours.QuietHoursManager(hass)
    manager._active = {"controls": {}, "observed_controls": ["media_player.kitchen"]}
    control = {
        "kind": "volume",
        "original_value": 0.8,
        "quiet_value": 0.2,
        "application_state": "prepared",
    }
    manager._config = quiet_hours.QuietHoursConfig(max_volume=0.2)
    manager._async_prepare_control_locked = AsyncMock(return_value=control)
    manager._async_revalidate_control_locked = AsyncMock(return_value=True)
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("failed"))
    manager._async_save_control_state_locked = AsyncMock()
    manager._log_control_failure = Mock()
    monkeypatch.setattr(quiet_hours, "_current_volume", Mock(side_effect=[0.8, 0.8]))
    controls = {"media_player.kitchen": control}

    await manager._async_apply_volume_locked(
        "assist_satellite.kitchen", "media_player.kitchen", controls
    )

    assert "media_player.kitchen" not in controls
    assert "media_player.kitchen" not in manager._active["observed_controls"]
    manager._async_save_control_state_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_quiet_hours_failed_volume_apply_retains_changed_control(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import quiet_hours

    manager = quiet_hours.QuietHoursManager(hass)
    manager._active = {"controls": {}, "observed_controls": ["media_player.kitchen"]}
    control = {
        "kind": "volume",
        "original_value": 0.8,
        "quiet_value": 0.2,
        "application_state": "prepared",
    }
    manager._config = quiet_hours.QuietHoursConfig(max_volume=0.2)
    manager._async_prepare_control_locked = AsyncMock(return_value=control)
    manager._async_revalidate_control_locked = AsyncMock(return_value=True)
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("failed"))
    manager._async_save_control_state_locked = AsyncMock()
    manager._log_control_failure = Mock()
    monkeypatch.setattr(quiet_hours, "_current_volume", Mock(side_effect=[0.8, 0.5]))
    controls = {"media_player.kitchen": control}

    await manager._async_apply_volume_locked(
        "assist_satellite.kitchen", "media_player.kitchen", controls
    )

    assert controls["media_player.kitchen"] is control
    manager._async_save_control_state_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_rule_live_guest_policy_reauthorizes_scene(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode
    from custom_components.extended_openai_conversation_responses import request_rules

    captured = {}

    def allowed(_hass, actions, policy, **kwargs):
        captured["action"] = actions[0]
        return False

    monkeypatch.setattr(request_rules, "_guest_script_allowed", allowed)
    monkeypatch.setattr(request_rules, "_ensure_action_guard_service", Mock())
    monkeypatch.setattr(request_rules, "_guard_native_actions", lambda actions: actions)
    monkeypatch.setattr(
        request_rules,
        "_outcome_probes",
        lambda actions: (list(actions), "completed", "stopped"),
    )
    monkeypatch.setattr(request_rules.cv, "SCRIPT_SCHEMA", lambda actions: actions)
    monkeypatch.setattr(
        request_rules,
        "async_validate_actions_config",
        AsyncMock(side_effect=lambda hass, actions: actions),
    )

    class FakeScript:
        def __init__(self, *args, **kwargs):
            pass

        async def async_run(self, variables, context):
            guard = request_rules._ACTIVE_ACTION_GUARD.get()
            guard({"scene": "scene.movie"})
            return None

        async def async_unload(self):
            return None

    monkeypatch.setattr(request_rules, "Script", FakeScript)

    rule = {
        "id": "r1",
        "name": "Rule",
        "action_type": "local_action",
        "action": {
            "actions": [{"scene": "scene.movie"}],
            "continue_to_ai": False,
            "failure_response": "failed",
            "success_response": "ok",
        },
    }
    match = request_rules.RuleMatch(rule, "phrase", False, 100.0)
    runtime = SimpleNamespace()
    live_policy = lambda: guest_mode.GuestCapabilityPolicy(
        True, controllable_entity_ids=frozenset()
    )

    result = await request_rules._async_evaluate_matched_rule(
        hass,
        match,
        "hello",
        runtime,
        "session",
        "model",
        None,
        30,
        None,
        None,
        live_guest_policy=live_policy,
    )

    assert result.successful is False
    assert result.response == guest_mode.GUEST_MODE_UNAVAILABLE
    assert captured["action"] == {
        "action": "scene.turn_on",
        "target": {"entity_id": "scene.movie"},
    }


@pytest.mark.asyncio
async def test_image_query_provider_failure_requests_reauthentication(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import services

    entry = SimpleNamespace(
        domain=services.DOMAIN,
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("auth")))
        ),
    )
    monkeypatch.setattr(services, "OpenAIError", RuntimeError)
    monkeypatch.setattr(services, "get_api_mode", Mock(return_value=services.API_MODE_RESPONSES))
    monkeypatch.setattr(
        services,
        "normalize_output_token_limit",
        Mock(return_value=("max_output_tokens", 10)),
    )
    monkeypatch.setattr(services, "prepare_image_params", Mock(return_value=[]))
    monkeypatch.setattr(services, "provider_user_message", Mock(return_value="provider failed"))
    reauth = Mock()
    monkeypatch.setattr(services, "request_reauthentication", reauth)
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    hass.async_add_executor_job = AsyncMock(return_value=[])

    registered = {}

    def capture_register(domain, service, handler, **kwargs):
        registered[(domain, service)] = handler

    monkeypatch.setattr(hass.services, "async_register", capture_register)
    await services.async_setup_services(hass, {})
    query_image = registered[(services.DOMAIN, services.SERVICE_QUERY_IMAGE)]

    call = SimpleNamespace(
        data={
            "config_entry": "entry",
            "model": "model",
            services.CONF_API_MODE: services.API_MODE_RESPONSES,
            "prompt": "describe",
            "images": [{"url": "data:image/png;base64,AA=="}],
            "max_tokens": 10,
        },
        context=SimpleNamespace(user_id=None),
    )
    with pytest.raises(HomeAssistantError, match="provider failed"):
        await query_image(call)

    reauth.assert_called_once_with(hass, entry, ANY)
