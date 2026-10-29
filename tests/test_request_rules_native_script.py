"""Contract tests against Home Assistant's actual Script engine."""

import asyncio
from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRules,
    _outcome_probes,
    async_evaluate_rule,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_UNAVAILABLE,
)
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError


class MemoryStore:
    def __init__(self, rule):
        self.rule = rule

    async def async_load(self):
        return {"defaults": dict(DEFAULT_MATCHING), "rules": [deepcopy(self.rule)]}

    async def async_save(self, _value):
        pass


async def evaluate(hass, actions, *, handoff=False, live_guest_policy=None):
    rule = {
        "id": "native",
        "name": "Native",
        "enabled": True,
        "phrases": ["run"],
        "match_type": "equals",
        "action_type": "local_action",
        "action": {
            "actions": actions,
            "success_response": "Done",
            "failure_response": "Failed",
            "continue_to_ai": handoff,
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }
    rules = RequestRules(MemoryStore(rule))
    await rules.async_initialize()
    return await async_evaluate_rule(
        hass,
        rules,
        RequestRuleRuntime(),
        "run",
        "native",
        live_guest_policy=live_guest_policy,
    )


@pytest.mark.parametrize(
    ("actions", "handoff", "response", "consume", "successful"),
    [
        ([{"variables": {"x": 1}}], False, "Done", True, True),
        ([{"variables": {"x": 1}}], True, None, False, True),
        ([{"set_conversation_response": "first"}], False, "first", True, True),
        ([{"set_conversation_response": "first"}], True, "first", True, True),
        (
            [
                {"set_conversation_response": "first"},
                {"set_conversation_response": "second"},
            ],
            True,
            "second",
            True,
            True,
        ),
        (
            [{"stop": "finished"}, {"variables": {"later": True}}],
            False,
            "Done",
            True,
            True,
        ),
        ([{"stop": "finished"}], True, "Done", True, True),
        ([{"stop": "disabled", "enabled": False}], True, None, False, True),
        (
            [{"set_conversation_response": "before"}, {"stop": "finished"}],
            True,
            "before",
            True,
            True,
        ),
        ([{"stop": "failed", "error": True}], True, "Failed", True, False),
        (
            [
                {"set_conversation_response": "before"},
                {"stop": "failed", "error": True},
            ],
            True,
            "Failed",
            True,
            False,
        ),
        (
            [{"condition": "template", "value_template": "{{ false }}"}],
            True,
            "Failed",
            True,
            False,
        ),
        (
            [
                {
                    "wait_template": "{{ false }}",
                    "timeout": "00:00:00",
                    "continue_on_timeout": False,
                }
            ],
            True,
            "Failed",
            True,
            False,
        ),
        (
            [
                {
                    "wait_template": "{{ false }}",
                    "timeout": "00:00:00",
                    "continue_on_timeout": True,
                }
            ],
            False,
            "Done",
            True,
            True,
        ),
        (
            [
                {
                    "wait_template": "{{ false }}",
                    "timeout": "00:00:00",
                    "continue_on_timeout": False,
                    "enabled": False,
                }
            ],
            True,
            None,
            False,
            True,
        ),
        (
            [
                {
                    "if": [{"condition": "template", "value_template": "{{ false }}"}],
                    "then": [
                        {"condition": "template", "value_template": "{{ false }}"}
                    ],
                    "else": [{"variables": {"branch": 1}}],
                },
                {"set_conversation_response": "after"},
            ],
            True,
            "after",
            True,
            True,
        ),
        (
            [
                {
                    "choose": [
                        {
                            "conditions": [
                                {
                                    "condition": "template",
                                    "value_template": "{{ true }}",
                                }
                            ],
                            "sequence": [
                                {
                                    "condition": "template",
                                    "value_template": "{{ false }}",
                                }
                            ],
                        }
                    ]
                },
                {"set_conversation_response": "after"},
            ],
            True,
            "after",
            True,
            True,
        ),
        (
            [
                {
                    "if": [{"condition": "template", "value_template": "{{ true }}"}],
                    "then": [{"set_conversation_response": "nested"}],
                }
            ],
            True,
            "nested",
            True,
            True,
        ),
        (
            [{"parallel": [{"sequence": [{"set_conversation_response": "parallel"}]}]}],
            True,
            "parallel",
            True,
            True,
        ),
        (
            [
                {
                    "if": [{"condition": "template", "value_template": "{{ true }}"}],
                    "then": [{"stop": "nested"}],
                },
                {"set_conversation_response": "never"},
            ],
            True,
            "Done",
            True,
            True,
        ),
    ],
)
async def test_native_script_outcomes(
    hass, actions, handoff, response, consume, successful
):
    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **_kwargs: (
        asyncio.create_task(coro)
    )
    outcome = await evaluate(hass, actions, handoff=handoff)
    assert outcome is not None
    assert (outcome.response, outcome.consume, outcome.successful) == (
        response,
        consume,
        successful,
    )


async def test_continue_on_error_follows_native_script_policy(hass):
    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **_kwargs: (
        asyncio.create_task(coro)
    )
    hass.services.async_call.side_effect = HomeAssistantError("recoverable")
    outcome = await evaluate(
        hass,
        [
            {"action": "light.turn_on", "continue_on_error": True},
            {"set_conversation_response": "recovered"},
        ],
        handoff=True,
    )
    assert outcome is not None
    assert (outcome.response, outcome.consume, outcome.successful) == (
        "recovered",
        True,
        True,
    )


async def test_script_variables_feed_native_response_but_not_generic_success(hass):
    hass.loop = asyncio.get_running_loop()
    response = await evaluate(
        hass,
        [
            {"variables": {"message": "native"}},
            {"set_conversation_response": "{{ message }}"},
        ],
    )
    assert response is not None and response.response == "native"
    generic = await evaluate(hass, [{"variables": {"message": "native"}}])
    assert generic is not None and generic.response == "Done"


async def test_native_service_response_variable_flows_to_later_actions(hass):
    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **_kwargs: (
        asyncio.create_task(coro)
    )
    hass.services.supports_response.return_value = SupportsResponse.OPTIONAL
    hass.services.async_call.return_value = {"message": "from service"}
    outcome = await evaluate(
        hass,
        [
            {"action": "test.echo", "response_variable": "reply"},
            {"set_conversation_response": "{{ reply.message }}"},
        ],
    )
    assert outcome is not None and outcome.response == "from service"


def test_outcome_instrumentation_leaves_service_payloads_untouched():
    action = {"action": "test.echo", "data": {"items": [{"stop": "payload"}]}}
    instrumented, _, _ = _outcome_probes([action])
    assert instrumented[1] == action


def test_stop_probe_shares_one_native_enabled_decision_and_keeps_source():
    stop = {
        "stop": "finished",
        "enabled": "{{ now().second > 10 }}",
        "alias": "Dynamic stop",
        "continue_on_error": True,
    }
    original = deepcopy(stop)
    sequence, _, marker = _outcome_probes([stop])
    wrapper = sequence[1]
    assert wrapper["enabled"] == original["enabled"]
    assert wrapper["alias"] == original["alias"]
    assert wrapper["continue_on_error"] is True
    assert wrapper["sequence"][0] == {"variables": {marker: True}}
    assert "enabled" not in wrapper["sequence"][1]
    assert wrapper["sequence"][1] == {
        key: value for key, value in original.items() if key != "enabled"
    }
    assert stop == original


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("kind", ["data", "alias", "metadata", "event"])
async def test_live_guard_preserves_native_template_boundaries(hass, live, kind):
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestCapabilityPolicy,
    )

    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **kw: (
        asyncio.create_task(coro)
    )
    action = {"action": "light.turn_on", "continue_on_error": True}
    if kind == "data":
        action["data"] = {"brightness": "{{ 1 / 0 }}"}
    elif kind == "event":
        action = {"event": "test_event", "event_data": {"value": "{{ 1 / 0 }}"}}
    else:
        action[kind] = "{{ 1 / 0 }}" if kind == "alias" else {"note": "{{ 1 / 0 }}"}
    outcome = await evaluate(
        hass,
        [action, {"set_conversation_response": "reached"}],
        live_guest_policy=GuestCapabilityPolicy.unrestricted if live else None,
    )
    assert outcome.successful and outcome.response == "reached"
    if kind == "data":
        assert not hass.services.async_call.called
    elif kind != "event":
        assert any(
            (
                call.args[:2] == ("light", "turn_on")
                or (call.kwargs.get("domain"), call.kwargs.get("service"))
                == ("light", "turn_on")
            )
            for call in hass.services.async_call.call_args_list
        ), hass.services.async_call.call_args_list


@pytest.mark.parametrize("shape", ["direct", "while", "until"])
@pytest.mark.parametrize("wrap_service_errors", [False, True])
async def test_live_authorization_after_permission_lookup_cannot_be_ignored(
    hass, monkeypatch, wrap_service_errors, shape
):
    from unittest.mock import AsyncMock
    from custom_components.extended_openai_conversation_responses import ha_actions
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestCapabilityPolicy,
    )

    if wrap_service_errors:
        # HA 2026.10 wraps non-HA service errors when continue_on_error is set.
        # Exercise that compatibility boundary even on the older local engine.
        from homeassistant.helpers import script as native_script

        original_step = native_script._ScriptRun._async_step_call_service

        async def current_ha_step(run):
            try:
                return await original_step(run)
            except HomeAssistantError:
                raise
            except Exception as err:
                if run._action.get("continue_on_error", False):
                    raise HomeAssistantError(err) from err
                raise

        monkeypatch.setattr(
            native_script._ScriptRun, "_async_step_call_service", current_ha_step
        )

    hass.loop = asyncio.get_running_loop()
    hass.async_create_task_internal.side_effect = lambda coro, **kw: (
        asyncio.create_task(coro)
    )
    policy = GuestCapabilityPolicy.unrestricted()
    lookups = []

    async def lookup(*args, **kwargs):
        nonlocal policy
        lookups.append(True)
        policy = GuestCapabilityPolicy(guest_active=True)
        return set()

    dispatch = AsyncMock()
    monkeypatch.setattr(ha_actions, "async_authorize_ha_action", lookup)
    monkeypatch.setattr(ha_actions, "async_dispatch_ha_service", dispatch)
    effect = {"action": "light.turn_on", "continue_on_error": True}
    if shape != "direct":
        effect = {
            "repeat": {
                shape: "{{ repeat.index <= 1 }}"
                if shape == "while"
                else "{{ repeat.index >= 1 }}",
                "sequence": [effect],
            }
        }
    if shape != "direct":
        effect = {"choose": [{"conditions": "{{ true }}", "sequence": [effect]}]}
    outcome = await evaluate(
        hass,
        [effect, {"set_conversation_response": "must not reach"}],
        live_guest_policy=lambda: policy,
    )
    assert lookups == [True], "The native action must reach permission lookup"
    assert not outcome.successful and outcome.response == GUEST_MODE_UNAVAILABLE
    dispatch.assert_not_called()


@pytest.mark.parametrize("effect_kind", ["service", "event"])
async def test_real_service_registry_rechecks_revoked_guest_control(
    tmp_path, effect_kind
):
    from homeassistant.core import HomeAssistant
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestCapabilityPolicy,
    )
    from custom_components.extended_openai_conversation_responses.ha_permissions import (
        bind_active_ha_context,
    )

    real = HomeAssistant(str(tmp_path))
    calls = []
    policy = GuestCapabilityPolicy.unrestricted()

    async def pause(call):
        nonlocal policy
        policy = GuestCapabilityPolicy(True, controllable_entity_ids=frozenset())

    async def effect(call):
        calls.append(dict(call.data))

    real.services.async_register("test", "pause", pause)
    real.services.async_register("light", "turn_on", effect)
    real.states.async_set("light.kitchen", "off")
    real.bus.async_listen("denied_probe", lambda event: calls.append(event.data))
    effect_action = (
        {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}
        if effect_kind == "service"
        else {"event": "denied_probe"}
    )
    with bind_active_ha_context(None):
        result = await evaluate(
            real,
            [
                {"action": "test.pause"},
                {
                    "sequence": [
                        {
                            "repeat": {
                                "while": "{{ repeat.index <= 1 }}",
                                "sequence": [effect_action],
                            }
                        }
                    ],
                    "continue_on_error": True,
                },
                {"set_conversation_response": "must not reach"},
            ],
            live_guest_policy=lambda: policy,
        )
    assert not result.successful, calls
    assert calls == []
