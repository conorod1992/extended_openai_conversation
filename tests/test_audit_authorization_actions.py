"""Regressions for authorization and Home Assistant action audit findings."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.functions.file import ReadFileFunction
from custom_components.extended_openai_conversation_responses.guest_mode import GuestCapabilityPolicy, guest_arguments_allowed_runtime, resolve_guest_selector_entity_ids
from custom_components.extended_openai_conversation_responses.ha_actions import serialize_reversible_state
from custom_components.extended_openai_conversation_responses.request_rules import _guest_script_allowed
from custom_components.extended_openai_conversation_responses.skill_availability import CANONICAL_SKILL_LOADER_PATH, is_canonical_skill_loader
from custom_components.extended_openai_conversation_responses.skill_runtime_availability import selected_skill_scope, require_selected_skill
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template


def test_model_cannot_override_trusted_file_config_dir(hass):
    rendered = ReadFileFunction()._render_allow_dirs(hass, [Template("{{ config_dir }}/allowed", hass)], {"config_dir":"/outside"}, include_defaults=False)
    assert rendered == [f"{hass.config.config_dir}/allowed"]


def test_hydrated_loader_is_recognized_and_selection_is_enforced(hass):
    assert is_canonical_skill_loader({"spec":{"name":"load_skill"}, "function":{"type":"read_file", "path":Template(CANONICAL_SKILL_LOADER_PATH, hass)}})
    with selected_skill_scope({"skills":["public"]}):
        require_selected_skill("public")
        with pytest.raises(ValueError, match="not selected"):
            require_selected_skill("private_owner")


def test_guest_cannot_read_private_state_in_local_response(hass):
    hass.states.get.return_value = State("sensor.private", "private-value")
    policy = GuestCapabilityPolicy(guest_active=True, readable_entity_ids=frozenset())
    assert not _guest_script_allowed(hass, [{"set_conversation_response":"{{ states('sensor.private') }}"}], policy)
    assert not _guest_script_allowed(hass, [{"variables":{"entity":"sensor.private"}}, {"set_conversation_response":"{{ expand(entity) | map(attribute='state') | list }}"}], policy)
    assert _guest_script_allowed(hass, [{"set_conversation_response":"Hello {{ request.slots.name }}"}], policy, slots={"name":"guest"})


def test_guest_entity_target_cannot_authorize_global_shell(hass):
    hass.services.async_services_for_domain.return_value = {}
    policy = GuestCapabilityPolicy(guest_active=True, controllable_entity_ids=frozenset({"light.public"}))
    assert not guest_arguments_allowed_runtime(hass, {"domain":"shell_command", "service":"global", "entity_id":"light.public"}, policy, control=True)


def test_area_labels_participate_in_guest_exclusions(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import guest_mode

    monkeypatch.setattr(guest_mode.er, "async_get", lambda _h: SimpleNamespace(async_get=lambda _id: SimpleNamespace(area_id="private-room", device_id=None, labels=set())))
    monkeypatch.setattr(guest_mode.dr, "async_get", lambda _h: SimpleNamespace())
    monkeypatch.setattr(guest_mode.ar, "async_get", lambda _h: SimpleNamespace(async_get_area=lambda _id: SimpleNamespace(labels={"private"})))
    assert resolve_guest_selector_entity_ids(hass, {"light.room"}, labels=["private"]) == {"light.room"}


@pytest.mark.parametrize(("domain", "service", "entity_id", "state"), [
    ("input_button", "press", "input_button.new", "unknown"),
    ("homeassistant", "update_entity", "sensor.offline", "unavailable"),
])
async def test_legitimate_unknown_and_recovery_targets_dispatch(hass, domain, service, entity_id, state):
    hass.states.get.return_value = State(entity_id, state)
    hass.services.async_services_for_domain.return_value = {}
    result = await native.NativeFunction().execute_service_single(hass, {}, {"domain":domain, "service":service, "entity_id":entity_id}, None, [{"entity_id":entity_id}])
    assert result["success"]
    hass.services.async_call.assert_awaited_once()


async def test_explicit_incompatible_target_cannot_report_success(hass):
    hass.states.get.return_value = State("switch.public", "on")
    hass.services.async_services_for_domain.return_value = {}
    with pytest.raises(HomeAssistantError, match="participating"):
        await native.NativeFunction().execute_service_single(hass, {}, {"domain":"light", "service":"turn_on", "entity_id":"switch.public"}, None, [{"entity_id":"switch.public"}])
    hass.services.async_call.assert_not_awaited()


def test_only_service_participants_need_exposure(hass, monkeypatch):
    monkeypatch.setattr(native.target_helpers, "async_extract_referenced_entity_ids", lambda *_: SimpleNamespace(referenced=set(), indirectly_referenced={"light.public", "sensor.hidden"}))
    monkeypatch.setattr(native, "_service_participants", lambda *_: {"light.public"})
    hass.states.get.side_effect = lambda entity_id: State(entity_id, "on")
    native.NativeFunction().validate_service_targets(hass, {"area_id":"room"}, [{"entity_id":"light.public"}], domain="light", service="turn_on")


def test_automation_append_preserves_existing_secret_tags(tmp_path):
    path = tmp_path / "automations.yaml"
    path.write_text("- id: old\n  alias: !secret automation_alias\n", encoding="utf-8")
    native._append_automation_atomic(path, {"id":"new", "alias":"New"})
    assert "!secret automation_alias" in path.read_text(encoding="utf-8")
    assert "id: new" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("mode", "attribute", "channels"), [("rgbw", "rgbw_color", (10,20,30,40)), ("rgbww", "rgbww_color", (10,20,30,40,50))])
def test_light_snapshots_preserve_white_channels(mode, attribute, channels):
    snapshot = serialize_reversible_state(State("light.white", "on", {"color_mode":mode, attribute:channels, "rgb_color":(10,20,30)}))
    assert snapshot[attribute] == channels


async def test_quiet_hours_restores_same_registered_speaker_after_rename(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import quiet_hours
    from tests.test_quiet_hours_runtime import _stateful_public_manager
    from tests.test_quiet_hours_restoration_regressions import _active

    manager = _stateful_public_manager(hass)
    manager._active = _active("media_player.old", "volume")
    manager._active["controls"]["media_player.old"]["registry_identity"] = ["media_player", "esphome", "speaker-unique"]
    monkeypatch.setattr(quiet_hours.er, "async_get", lambda _h: SimpleNamespace(async_get_entity_id=lambda *_: "media_player.new"))
    hass.states.get.side_effect = lambda entity_id: State(entity_id, "idle", {"volume_level":0.2}) if entity_id == "media_player.new" else None
    manager._async_set_volume = AsyncMock()
    await manager._async_restore_locked()
    manager._async_set_volume.assert_awaited_once_with("media_player.new", 0.8)


async def test_broadcast_exclusion_precedes_local_interception(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import local_intents

    intercept = AsyncMock(return_value=object())
    monkeypatch.setattr(local_intents, "_async_try_targeted_broadcast", intercept)
    monkeypatch.setattr(local_intents.conversation, "async_handle_intents", None)
    await local_intents.async_try_handle_local_intent(hass, SimpleNamespace(text="broadcast a message"), SimpleNamespace(), {"local_intents_enabled":True, "local_intent_exclusions":["HassBroadcast"]}, guest_active=False)
    intercept.assert_not_awaited()
