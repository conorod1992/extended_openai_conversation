"""Public Assist failures must not expose rendered data in HA child loggers."""

import logging

from tests_real_ha.test_action_failure_safety import _authenticated_say
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _rule, _speech


async def test_public_rule_repeat_count_keeps_rendered_value_private(
    hass, monkeypatch, caplog
):
    secret = "FABRICATED-RULE-SECRET-repeat-count-42"
    hass.states.async_set("sensor.private_repeat_input", secret)
    agent = await _agent(hass)
    sent = _provider(monkeypatch, agent, ["Unexpected fallback"])
    await agent._request_rules.async_create(
        _rule(
            "local_action",
            {
                "actions": [
                    {
                        "repeat": {
                            "count": "{{ states('sensor.private_repeat_input') }}",
                            "sequence": [{"delay": {"milliseconds": 0}}],
                        }
                    }
                ],
                "success_response": "Done",
                "failure_response": "Failed safely",
            },
            phrase="run rule",
        )
    )
    caplog.clear()
    result = await _authenticated_say(hass, agent, "run rule", is_admin=True)
    await hass.async_block_till_done()
    assert _speech(result, successful=False) == "Failed safely"
    assert not sent
    assert secret not in caplog.text
    assert any(
        record.levelno >= logging.ERROR
        and "EOAI private script diagnostic" in record.getMessage()
        for record in caplog.records
    )
