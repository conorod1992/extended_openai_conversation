"""Service participation must agree across native tools and local Request Rules."""

import asyncio
from copy import deepcopy
from functools import partial
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    bind_active_ha_context,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRules,
    async_evaluate_rule,
)
from homeassistant.core import Context, State
from homeassistant.exceptions import HomeAssistantError


@pytest.fixture
def selected_home(hass, monkeypatch):
    selected = {"light.kitchen", "sensor.kitchen_temperature"}
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda _hass, selection: SimpleNamespace(
            referenced=selection.entity_ids,
            indirectly_referenced=selected if selection.has_any_target else set(),
        ),
    )
    # Use the real participant reader, including the HA entity-service registration shape.
    registered = SimpleNamespace(
        job=SimpleNamespace(
            target=partial(
                ha_actions.service_helpers.entity_service_call,
                object(),
                {"light.kitchen": object()},
            )
        )
    )
    hass.services.async_services_for_domain.side_effect = lambda domain: (
        {"turn_on": registered} if domain == "light" else {}
    )
    hass.services.has_service.side_effect = lambda domain, service: (
        domain in {"light", "homeassistant", "custom"}
    )
    hass.states.get.side_effect = lambda entity_id: State(entity_id, "on")
    hass.auth.async_get_user.return_value = SimpleNamespace(
        is_active=True,
        is_admin=False,
        permissions=SimpleNamespace(
            check_entity=lambda entity_id, _policy: entity_id == "light.kitchen",
            access_all_entities=lambda _: False,
        ),
    )
    return hass


class RuleStore:
    def __init__(self, rule):
        self.rule = rule

    async def async_load(self):
        return {"defaults": dict(DEFAULT_MATCHING), "rules": [deepcopy(self.rule)]}

    async def async_save(self, _value):
        pass


async def run_rule(hass, target, context):
    rule = {
        "id": "lights",
        "name": "Lights",
        "enabled": True,
        "phrases": ["lights"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": [{"action": "light.turn_on", "target": target}],
            "success_response": "Done",
            "failure_response": "Failed",
            "continue_to_ai": False,
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }
    rules = RequestRules(RuleStore(rule))
    await rules.async_initialize()
    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **_: asyncio.create_task(
        coro
    )
    return await async_evaluate_rule(
        hass, rules, RequestRuleRuntime(), "lights", "session", context=context
    )


@pytest.mark.parametrize("selector", ["area_id", "device_id", "floor_id", "label_id"])
@pytest.mark.parametrize("entrypoint", ["native", "rule"])
async def test_unrelated_hidden_sensor_does_not_block_light(
    selected_home, selector, entrypoint
):
    hass = selected_home
    target = {selector: "kitchen"}
    context = Context(user_id="restricted-user")
    checked = []

    def validate(_hass, entities):
        checked.append(entities)
        NativeFunction().validate_entity_ids(
            hass, sorted(entities), [{"entity_id": "light.kitchen"}]
        )

    with (
        bind_active_ha_context(context),
        ha_actions.action_target_revalidation(validate),
    ):
        if entrypoint == "native":
            result = await NativeFunction().execute_service_single(
                hass,
                {},
                {"domain": "light", "service": "turn_on", "service_data": target},
                None,
                [{"entity_id": "light.kitchen"}],
            )
            assert result["success"]
        else:
            result = await run_rule(hass, target, context)
            assert result.successful
    assert checked == [{"light.kitchen"}]
    hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize("selector", ["area_id", "device_id", "floor_id", "label_id"])
@pytest.mark.parametrize("denial", ["exposure", "guest", "permission"])
async def test_participating_light_still_requires_authorization(
    selected_home, monkeypatch, selector, denial
):
    hass = selected_home
    policy = GuestCapabilityPolicy(
        guest_active=True, controllable_entity_ids=frozenset()
    )

    from custom_components.extended_openai_conversation_responses import conversation

    monkeypatch.setattr(
        conversation,
        "get_exposed_entities",
        lambda _hass: [{"entity_id": "light.kitchen"}],
    )
    agent = SimpleNamespace(
        hass=hass,
        _filter_guest_entities=lambda *_args, **_kwargs: [],
        _effective_guest_policy=lambda: (
            policy if denial == "guest" else GuestCapabilityPolicy(guest_active=False)
        ),
    )

    def validate(_hass, entities):
        assert entities == {"light.kitchen"}
        conversation.ExtendedOpenAIAgentEntity._require_current_action_targets(
            agent, _hass, entities
        )

    if denial == "permission":
        hass.auth.async_get_user.return_value.permissions.check_entity = lambda *_: (
            False
        )
    with (
        ha_actions.action_target_revalidation(validate),
        pytest.raises(HomeAssistantError),
    ):
        await ha_actions.async_call_ha_action(
            hass,
            "light",
            "turn_on",
            target={selector: "kitchen"},
            context=Context(user_id="restricted-user"),
        )
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize(
    "domain,service,expected",
    [
        ("homeassistant", "turn_on", {"light.kitchen"}),
        (
            "homeassistant",
            "update_entity",
            {"light.kitchen", "sensor.kitchen_temperature"},
        ),
        ("custom", "dispatch", {"light.kitchen", "sensor.kitchen_temperature"}),
    ],
)
async def test_generic_and_custom_service_semantics(
    selected_home, monkeypatch, domain, service, expected
):
    seen = []

    async def require(_hass, entities, **_kwargs):
        seen.append(entities)

    monkeypatch.setattr(ha_actions, "async_require_control_permission", require)
    result = await ha_actions.async_authorize_ha_action(
        selected_home, domain, service, target={"area_id": "kitchen"}
    )
    assert result == expected
    assert seen == [expected]


async def test_generic_power_previous_state_contains_only_service_participants(
    selected_home,
):
    result = await NativeFunction().execute_service_single(
        selected_home,
        {},
        {
            "domain": "homeassistant",
            "service": "turn_on",
            "service_data": {"area_id": "kitchen"},
        },
        None,
        [{"entity_id": "light.kitchen"}],
    )
    assert result["success"]
    assert result["previous_state"] == {"light.kitchen": {"state": "on"}}


async def test_generic_power_retains_other_participating_domains(selected_home):
    hass = selected_home
    hass.services.has_service.side_effect = lambda *_: True
    switch = SimpleNamespace(
        job=SimpleNamespace(
            target=partial(
                ha_actions.service_helpers.entity_service_call,
                object(),
                {"switch.hidden": object()},
            )
        )
    )
    hass.services.async_services_for_domain.side_effect = lambda domain: (
        {"turn_on": switch} if domain == "switch" else {}
    )
    assert ha_actions.service_target_entity_ids(
        hass, "homeassistant", "turn_on", {"switch.hidden", "light.kitchen"}
    ) == {"switch.hidden", "light.kitchen"}
    # Unknown light dispatch semantics stay conservative rather than being dropped.


async def test_explicit_targets_recheck_participants_after_permission_await(
    selected_home, monkeypatch
):
    hass = selected_home
    registered = hass.services.async_services_for_domain("light")["turn_on"]
    participants = registered.job.target.args[1]
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda _hass, selection: SimpleNamespace(
            referenced=selection.entity_ids, indirectly_referenced=set()
        ),
    )

    async def check_permissions(_hass, entity_ids, **_kwargs):
        assert entity_ids == {"light.kitchen"}
        # HA can add an already-existing state to a component without replacing
        # the service registration or the existing public target's identity.
        participants["light.private"] = object()

    monkeypatch.setattr(
        ha_actions, "async_require_control_permission", check_permissions
    )
    with pytest.raises(HomeAssistantError, match="target changed"):
        await ha_actions.async_call_ha_action(
            hass,
            "light",
            "turn_on",
            target={"entity_id": ["light.kitchen", "light.private"]},
        )
    hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("domain", ["light", "homeassistant"])
@pytest.mark.parametrize("source", ["data", "target"])
@pytest.mark.parametrize("entity_ids", ["all", ["all"], "light.kitchen,all"])
async def test_all_sentinel_is_rejected_before_participant_filtering(
    selected_home, domain, source, entity_ids
):
    with pytest.raises(HomeAssistantError, match="entity_id 'all' is not supported"):
        await ha_actions.async_call_ha_action(
            selected_home, domain, "turn_on", **{source: {"entity_id": entity_ids}}
        )
    selected_home.services.async_call.assert_not_awaited()
