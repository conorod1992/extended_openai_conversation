"""Native payload keys are not script selectors at save, reload or execution."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from homeassistant.core import Context
from tests.test_request_rules import MemoryStore, local_rule


@pytest.mark.parametrize(
    "payload",
    [
        {"scene": "{{ selected_scene }}"},
        {
            "condition": "state",
            "entity_id": "{{ selected_scene }}",
            "scene": "{{ selected_scene }}",
        },
        {"nested": {"sequence": [{"scene": "{{ selected_scene }}"}]}},
    ],
)
@pytest.mark.parametrize("repeat", [False, True])
async def test_event_payloads_and_iteration_items_survive_native_execution(
    hass, payload, repeat
):
    emitted = []
    hass.bus.async_listen(
        "scene_selected", lambda event: emitted.append(dict(event.data))
    )
    actions = [{"variables": {"selected_scene": "scene.relax"}}]
    if repeat:
        actions.append(
            {
                "repeat": {
                    "for_each": [deepcopy(payload)],
                    "sequence": [
                        {
                            "event": "scene_selected",
                            "event_data": {"item": "{{ repeat.item }}"},
                        }
                    ],
                }
            }
        )
    else:
        actions.append({"event": "scene_selected", "event_data": payload})
    raw = local_rule()
    raw["action"]["actions"] = actions
    store = MemoryStore()
    rules = rr.RequestRules(store)
    await rules.async_initialize()
    await rules.async_create(raw)
    loaded = rr.RequestRules(store)
    await loaded.async_initialize()
    assert not loaded._diagnostics
    result = await rr.async_evaluate_rule(
        hass,
        loaded,
        rr.RequestRuleRuntime(),
        "good night",
        "payload-session",
        context=Context(),
    )
    await hass.async_block_till_done()
    assert result.successful
    expected = rr.resolve_slot_values(payload, {})

    def render(value):
        if isinstance(value, str):
            return value.replace("{{ selected_scene }}", "scene.relax")
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        return value

    assert emitted == [{"item": render(expected)} if repeat else render(expected)]


@pytest.mark.parametrize(
    "action",
    [
        {"scene": "{{ selected_scene }}"},
        {
            "if": [
                {
                    "condition": "state",
                    "entity_id": "{{ selected_scene }}",
                    "state": "on",
                }
            ],
            "then": [{"stop": "done"}],
        },
        {
            "repeat": {
                "while": [
                    {
                        "condition": "state",
                        "entity_id": "{{ selected_scene }}",
                        "state": "on",
                    }
                ],
                "sequence": [{"stop": "done"}],
            }
        },
    ],
)
def test_real_selectors_still_require_preexecution_captures(hass, action):
    raw = local_rule()
    raw["action"]["actions"] = [action]
    with pytest.raises(ValueError, match=r"capture|selector"):
        rr.validate_rule(raw)
