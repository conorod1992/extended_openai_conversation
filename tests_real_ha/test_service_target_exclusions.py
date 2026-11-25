"""Real Home Assistant target exclusions at EOAI's service boundaries."""

import logging
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
    ScheduledActionOwner,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    EntityNotExposed,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    async_authorize_ha_action,
    resolve_action_entity_ids,
)
from custom_components.extended_openai_conversation_responses.ha_service_compat import (
    prepare_service_dispatch,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    async_setup_ha_permissions,
)
from custom_components.extended_openai_conversation_responses.scheduled_action_summary import (
    describe_native_action,
)
from homeassistant.auth.models import Group
from homeassistant.auth.permissions.const import (
    CAT_ENTITIES,
    POLICY_CONTROL,
    POLICY_READ,
)
from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    floor_registry,
    label_registry,
    target as target_helpers,
)
from homeassistant.helpers.entity_component import EntityComponent

_SUPPORTS_EXCLUSIONS = hasattr(target_helpers, "async_extract_excluded_entity_ids")


@pytest.fixture
async def switch_targets(hass):
    effects = []

    class ProbeSwitch(SwitchEntity):
        _attr_should_poll = False
        _attr_is_on = False

        def __init__(self, name):
            self._attr_name = self._attr_unique_id = name

        async def async_turn_on(self, **kwargs):
            effects.append((self.entity_id, kwargs))
            self._attr_is_on = True
            self.async_write_ha_state()

    component = EntityComponent(logging.getLogger(__name__), "switch", hass)
    switches = [ProbeSwitch("Exclusion visible"), ProbeSwitch("Exclusion hidden")]
    await component.async_add_entities(switches)
    component.async_register_entity_service("turn_on", {}, "async_turn_on")

    visible, hidden = (item.entity_id for item in switches)
    area = area_registry.async_get(hass).async_create("Exclusion test")
    label = label_registry.async_get(hass).async_create("Exclusion hidden")
    registry = entity_registry.async_get(hass)
    registry.async_update_entity(visible, area_id=area.id)
    registry.async_update_entity(hidden, area_id=area.id, labels={label.label_id})
    async_expose_entity(hass, "conversation", visible, True)
    async_expose_entity(hass, "conversation", hidden, False)
    return visible, hidden, area.id, label.label_id, effects


async def test_scheduled_summary_only_names_visible_indirect_targets(
    hass, switch_targets
):
    visible, hidden, mixed_area, hidden_label, _ = switch_targets
    floor = floor_registry.async_get(hass).async_create("Ground floor")
    area = area_registry.async_get(hass).async_create("Hall", floor_id=floor.floor_id)
    label = label_registry.async_get(hass).async_create("Evening lights")
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = device_registry.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "summary-device")},
        name="Hall switch",
    )
    registry = entity_registry.async_get(hass)
    registry.async_update_entity(
        visible,
        area_id=area.id,
        device_id=device.id,
        labels={label.label_id, hidden_label},
    )
    # The old area is hidden by exposure. The visible label and floor contain
    # only the visible switch, and the old label includes a hidden switch.
    allowed = MockUser(
        is_owner=False,
        groups=[
            Group(
                name="Read visible switch",
                policy={
                    CAT_ENTITIES: {ENTITY_ENTITY_IDS: {visible: {POLICY_READ: True}}}
                },
            )
        ],
    ).add_to_hass(hass)
    denied = MockUser(is_owner=False, groups=[]).add_to_hass(hass)
    await async_setup_ha_permissions(hass)
    manager = DelayedToolManager(hass)

    def show(targets, user=allowed):
        owner = ScheduledActionOwner("provider", "agent", user_id=user.id)
        return describe_native_action(
            hass,
            "execute_service_single",
            {"domain": "switch", "service": "turn_on", "service_data": targets},
            lambda entities: manager._visible_summary_entities(entities, owner),
        )

    assert show({"area_id": area.id, "device_id": device.id}) == (
        "switch.turn_on (area Hall, device Hall switch)"
    )
    assert show({"floor_id": floor.floor_id, "label_id": label.label_id}) == (
        "switch.turn_on (floor Ground floor, label Evening lights)"
    )
    assert show(
        {"area_id": mixed_area, "label_id": hidden_label, "entity_id": hidden}
    ) == ("switch.turn_on (additional targets omitted)")
    assert (
        show({"area_id": area.id}, denied)
        == "switch.turn_on (additional targets omitted)"
    )


@pytest.mark.skipif(
    not _SUPPORTS_EXCLUSIONS, reason="HA has no target exclusion support"
)
@pytest.mark.parametrize("domain", ["switch", "homeassistant"])
@pytest.mark.parametrize("indirect", [False, True])
async def test_excluded_targets_never_require_scope_or_enter_state_and_receipt(
    hass, switch_targets, domain, indirect
):
    visible, hidden, area_id, label_id, effects = switch_targets
    missing = "switch.exclusion_missing"
    selection = (
        {"area_id": area_id, "entity_id": [missing]}
        if indirect
        else {"entity_id": [visible, hidden, missing]}
    )
    exclusion = {"entity_id": [missing]}
    if indirect:
        exclusion["label_id"] = [label_id]
    else:
        exclusion["entity_id"].append(hidden)

    user = MockUser(
        is_owner=False,
        groups=[
            Group(
                name="Exclusion control",
                policy={
                    CAT_ENTITIES: {
                        ENTITY_ENTITY_IDS: {
                            visible: {POLICY_READ: True, POLICY_CONTROL: True},
                            hidden: {POLICY_READ: True},
                        }
                    }
                },
            )
        ],
    )
    user.add_to_hass(hass)
    context = Context(user_id=user.id)
    with pytest.raises(HomeAssistantError, match="does not have permission to control"):
        await async_authorize_ha_action(
            hass, domain, "turn_on", target=selection, context=context
        )
    # A target block wins over data exactly as in Core dispatch.
    assert await async_authorize_ha_action(
        hass,
        domain,
        "turn_on",
        data={"exclude": {"entity_id": [visible]}},
        target={**selection, "exclude": exclusion},
        context=context,
    ) == {visible}

    native = NativeFunction()
    arguments = {"domain": domain, "service": "turn_on"}
    exposed = [{"entity_id": visible}]
    with pytest.raises(EntityNotExposed):
        await native.execute_service_single(
            hass, {}, {**arguments, "service_data": selection}, None, exposed
        )
    assert not effects

    selected = {**selection, "exclude": exclusion}
    _, receipt = await prepare_service_dispatch(
        hass, domain, "turn_on", {"service_data": selected, "context": context}
    )
    _, explicit_receipt = await prepare_service_dispatch(
        hass,
        domain,
        "turn_on",
        {"service_data": {"entity_id": [visible]}, "context": context},
    )
    assert receipt == explicit_receipt
    assert receipt["data"] == {"entity_id": [visible]}

    result = await native.execute_service_single(
        hass,
        {},
        {**arguments, "service_data": selected},
        SimpleNamespace(context=context),
        exposed,
    )
    assert result == {"success": True, "previous_state": {visible: {"state": "off"}}}
    assert effects == [(visible, {})]
    assert hass.states.get(hidden).state == "off"


@pytest.mark.parametrize(
    "exclusion", ["opaque option", {"entity_id": "switch.exclusion_hidden"}]
)
async def test_custom_service_keeps_exclude_data_and_target_checks(
    hass, switch_targets, exclusion
):
    visible, hidden, area_id, _, effects = switch_targets
    calls = []

    async def custom_handler(call):
        calls.append(dict(call.data))

    hass.services.async_register("switch", "custom_action", custom_handler)
    data = {"area_id": area_id, "exclude": exclusion}
    assert resolve_action_entity_ids(hass, "switch", "custom_action", data) == {
        visible,
        hidden,
    }
    _, prepared = await prepare_service_dispatch(
        hass, "switch", "custom_action", {"service_data": data}
    )
    assert prepared["data"] == data
    args = {"domain": "switch", "service": "custom_action", "service_data": data}
    native = NativeFunction()
    with pytest.raises(EntityNotExposed):
        await native.execute_service_single(
            hass, {}, args, None, [{"entity_id": visible}]
        )
    assert not calls

    result = await native.execute_service_single(
        hass, {}, args, None, [{"entity_id": visible}, {"entity_id": hidden}]
    )
    assert result["success"]
    assert set(result["previous_state"]) == {visible, hidden}
    assert calls == [data]
    assert not effects


@pytest.mark.skipif(
    not _SUPPORTS_EXCLUSIONS, reason="HA has no target exclusion support"
)
async def test_generic_power_checks_targets_forwarded_to_an_opaque_delegate(
    hass, switch_targets
):
    visible, hidden, area_id, _, _ = switch_targets
    calls = []

    async def custom_handler(call):
        calls.append(dict(call.data))

    hass.services.async_register("switch", "turn_on", custom_handler)
    data = {"area_id": area_id, "exclude": {"entity_id": [hidden]}}
    assert resolve_action_entity_ids(hass, "homeassistant", "turn_on", data) == {
        visible,
        hidden,
    }
    arguments = {"domain": "homeassistant", "service": "turn_on", "service_data": data}
    native = NativeFunction()
    with pytest.raises(EntityNotExposed):
        await native.execute_service_single(
            hass, {}, arguments, None, [{"entity_id": visible}]
        )
    assert not calls
    result = await native.execute_service_single(
        hass, {}, arguments, None, [{"entity_id": visible}, {"entity_id": hidden}]
    )
    assert result["success"]
    assert calls == [{**data, "entity_id": [visible]}]


async def test_broad_targets_and_unsupported_exclusions_fail_closed(
    hass, switch_targets
):
    visible, hidden, _, _, effects = switch_targets
    data = {"entity_id": "all", "exclude": {"entity_id": [hidden]}}
    with pytest.raises(HomeAssistantError, match="'all' is not supported"):
        resolve_action_entity_ids(hass, "switch", "turn_on", data)
    data["entity_id"] = [visible, hidden]
    if not _SUPPORTS_EXCLUSIONS:
        assert resolve_action_entity_ids(hass, "switch", "turn_on", data) == {
            visible,
            hidden,
        }
    else:
        for invalid in ("invalid exclusion", {"entity_id": "all"}):
            with pytest.raises(
                HomeAssistantError, match="Invalid Home Assistant target exclusion"
            ):
                resolve_action_entity_ids(
                    hass, "switch", "turn_on", {**data, "exclude": invalid}
                )
    assert not effects
