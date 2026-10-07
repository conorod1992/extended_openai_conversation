"""Independent behaviour invariants and unrelated HA integration coexistence.

No universal permission guarantee for user-exposed Function Tools is asserted here:
these tests only enforce existing ownership, isolation and HA registration boundaries.
"""

from __future__ import annotations

from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _rule, _say, _speech


async def test_disabled_unrelated_rule_is_behaviorally_inert(hass, monkeypatch):
    agent = await _agent(hass)
    _provider(monkeypatch, agent, ["Baseline answer", "Baseline answer"])
    first = await _say(hass, agent, "ordinary question")
    assert _speech(first) == "Baseline answer"

    unrelated = _rule(
        "model_routing",
        {
            "model": "gpt-5-mini",
            "reasoning_effort": "medium",
            "scope": "request",
            "reset": False,
            "continue_to_ai": True,
            "success_response": "Should never route",
        },
        phrase="unrelated disabled phrase",
    )
    unrelated["enabled"] = False
    await agent._request_rules.async_create(unrelated)

    second = await _say(hass, agent, "ordinary question")
    assert _speech(second) == _speech(first)


async def test_unrelated_service_owner_survives_eoai_unload_and_reload(hass):
    """A synthetic other integration retains its HA service ownership."""
    calls = []

    async def neighbour(call):
        calls.append((call.data["marker"], call.context.user_id))

    hass.services.async_register("other_integration_probe", "ping", neighbour)
    agent = await _agent(hass)
    entry = agent.entry

    async def ping(marker):
        await hass.services.async_call(
            "other_integration_probe",
            "ping",
            {"marker": marker},
            blocking=True,
            context=Context(user_id="other-integration-owner"),
        )

    await ping("before")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.services.has_service("other_integration_probe", "ping")
    await ping("during-unload")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service("other_integration_probe", "ping")
    await ping("after")
    assert calls == [
        ("before", "other-integration-owner"),
        ("during-unload", "other-integration-owner"),
        ("after", "other-integration-owner"),
    ]
