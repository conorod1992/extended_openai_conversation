"""Deterministic, bounded grammar of valid native Home Assistant script shapes.

The HA script engine validates and runs every generated script through the public
EOAI Request Rule path. Keep seeds and case IDs stable for CI triage.
"""
from itertools import product

import pytest

from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_request_rules_script_semantics import _local


def _script_shape(shape, value, gate):
    """Construct semantically equivalent scripts using different HA syntax."""
    template = "{{ marker }}"
    base = {"action": "grammar_probe.record", "data": {"message": template}}
    if shape == "variables":
        return [{"variables": {"marker": value}}, base]
    if shape == "choose":
        return [
            {"variables": {"marker": value, "gate": gate}},
            {
                "choose": [
                    {"conditions": "{{ gate }}", "sequence": [base]},
                ],
                "default": [base],
            },
        ]
    if shape == "repeat":
        return [
            {"variables": {"marker": value}},
            {"repeat": {"count": 1, "sequence": [base]}},
        ]
    if shape == "nested_choose":
        return [
            {"variables": {"marker": value, "gate": gate}},
            {
                "choose": [
                    {
                        "conditions": "{{ gate }}",
                        "sequence": [
                            {"choose": [{"conditions": "{{ true }}", "sequence": [base]}]}
                        ],
                    },
                ],
                "default": [base],
            },
        ]
    raise AssertionError(shape)


@pytest.mark.parametrize(
    "shape,value,gate",
    list(product(
        ("variables", "choose", "repeat", "nested_choose"),
        ("alpha", "an apostrophe's value", "unicode-é"),
        (True, False),
    )),
)
async def test_generated_native_script_grammar_preserves_output(
    hass, monkeypatch, shape, value, gate
):
    """Every valid generated variant produces exactly one typed HA service call."""
    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])
    recorded = []

    async def record(call):
        recorded.append(call.data["message"])

    hass.services.async_register("grammar_probe", "record", record)
    await agent._request_rules.async_create(
        _local(_script_shape(shape, value, gate), phrase="run grammar")
    )
    answer = await _say(hass, agent, "run grammar")
    assert _speech(answer) == "Done"
    assert recorded == [value], (shape, value, gate, recorded)
