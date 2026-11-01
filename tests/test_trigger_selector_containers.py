"""Trigger-container traversal must not rewrite ordinary payload selectors."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from custom_components.extended_openai_conversation_responses.script_structure import (
    script_selector_fields,
)


@pytest.mark.parametrize("alias", ["trigger", "platform"])
def test_trigger_options_and_group_selectors_leave_payload_lookalikes_opaque(alias):
    payload = {
        "entity_id": "{{ arbitrary_payload }}",
        "triggers": [
            {alias: "state", "options": {"entity_id": "{{ arbitrary_payload }}"}}
        ],
    }
    options = {"entity_id": ["{entity}"], "event_data": deepcopy(payload)}
    trigger = {alias: "state", "options": options, "variables": deepcopy(payload)}
    sequence = [
        {"wait_for_trigger": [{"triggers": [trigger]}]},
        {"event": "probe", "event_data": deepcopy(payload)},
        {"variables": deepcopy(payload)},
        {"action": "probe.record", "data": deepcopy(payload)},
    ]
    before = deepcopy(sequence)
    selectors = script_selector_fields(sequence)
    assert "entity_id" in selectors[id(options)]
    assert id(options["event_data"]) not in selectors
    assert id(trigger["variables"]) not in selectors
    prepared = rr._prepare_concrete_captures(sequence, {"entity": "sensor.selected"})
    assert prepared[0]["wait_for_trigger"][0]["triggers"][0]["options"][
        "entity_id"
    ] == ["sensor.selected"]
    assert (
        prepared[0]["wait_for_trigger"][0]["triggers"][0]["options"]["event_data"]
        == payload
    )
    for action, key in zip(
        prepared[1:], ("event_data", "variables", "data"), strict=True
    ):
        assert action[key] == payload
    assert sequence == before
    with pytest.raises(ValueError, match="unavailable"):
        rr._prepare_concrete_captures(sequence, {})
