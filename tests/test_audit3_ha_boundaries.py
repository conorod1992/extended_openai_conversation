"""Regression contracts for HA authorization and registration boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from custom_components.extended_openai_conversation_responses.functions.script import (
    ScriptFunction,
)
from custom_components.extended_openai_conversation_responses.functions.template import (
    TemplateFunction,
)
from custom_components.extended_openai_conversation_responses.legacy_agent_alias import (
    register_legacy_agent,
    unregister_legacy_agent,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
)
from homeassistant.components.conversation.agent_manager import AgentManager
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

DEVICE = {"device_id": "0" * 32, "domain": "nut", "type": "load_off"}


@pytest.mark.parametrize(
    "sequence",
    [
        [DEVICE],
        [{"if": "{{ true }}", "then": DEVICE}],
        [{"repeat": {"count": 1, "sequence": [DEVICE]}}],
        [{"choose": [{"conditions": "{{ true }}", "sequence": [DEVICE]}]}],
        [{"parallel": [{"sequence": [DEVICE]}]}],
    ],
)
async def test_device_actions_rejected_before_dispatch(hass, sequence):
    function = ScriptFunction()
    with patch(
        "custom_components.extended_openai_conversation_responses.functions.script.Script"
    ) as runner:
        with pytest.raises(HomeAssistantError, match="do not support device actions"):
            await function.execute(
                hass, {"type": "script", "sequence": sequence}, {}, None, []
            )
        runner.assert_not_called()
    with pytest.raises(HomeAssistantError, match="do not support device actions"):
        function.validate_schema({"type": "script", "sequence": sequence})


@pytest.mark.parametrize("state", [None, "unavailable", "on"])
def test_hidden_entities_have_uniform_errors_without_state_lookup(hass, state):
    hass.states.get.return_value = SimpleNamespace(state=state) if state else None
    with pytest.raises(HomeAssistantError, match="Entity access is denied"):
        TemplateFunction().validate_entity_ids(hass, ["sensor.hidden"], [])
    hass.states.get.assert_not_called()


@pytest.mark.parametrize("remove_owner", [False, True])
async def test_shared_alias_survives_either_sibling_removal(
    tmp_path, monkeypatch, remove_owner
):
    hass = HomeAssistant(str(tmp_path))
    manager = AgentManager(hass)
    monkeypatch.setattr(
        "homeassistant.components.conversation.get_agent_manager", lambda _: manager
    )
    entry = SimpleNamespace(entry_id="parent")
    first, second = Mock(), Mock()
    register_legacy_agent(hass, entry, first)
    register_legacy_agent(hass, entry, second)
    removed, remaining = (second, first) if remove_owner else (first, second)
    unregister_legacy_agent(hass, entry, removed)
    assert manager.async_get_agent("parent") is remaining
    unregister_legacy_agent(hass, entry, remaining)
    assert "parent" not in manager._agents


async def test_disabled_quiet_sensor_stays_absent_and_can_be_reenabled(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    dr.async_setup(hass)
    await dr.async_load(hass)
    registry = er.async_get(hass)
    await registry.async_load()
    manager = QuietHoursManager(hass)
    manager._publish_state(None)
    entity_id = manager._registered_state_entity_id
    registry.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    manager._publish_state(None)
    assert hass.states.get(entity_id) is None
    manager._publish_state(None)
    assert hass.states.get(entity_id) is None
    registry.async_update_entity(entity_id, disabled_by=None)
    manager._publish_state(None)
    assert hass.states.get(entity_id).state == "off"
