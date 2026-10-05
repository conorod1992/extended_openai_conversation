"""Generated native Request Rule action-sequence contracts for Enhanced nightly."""

from __future__ import annotations

import asyncio

from tests_real_ha.test_cross_feature_acceptance import (
    _agent,
    _rule,
    _say,
    _speech,
)


async def test_generated_request_rule_action_sequences_keep_request_local_variables(
    hass
):
    """Concurrent generated sequences retain ordering and request-local variables."""
    agent = await _agent(hass, title="Generated Request Rule actions")
    observed = []

    async def record(call):
        observed.append((call.data["message"], call.context.user_id))

    hass.services.async_register("rule_probe", "record", record)

    case_count = 12
    for index in range(case_count):
        marker = f"generated-{index}"
        await agent._request_rules.async_create(
            _rule(
                "local_action",
                {
                    "actions": [
                        {"variables": {"marker": marker}},
                        {"delay": {"milliseconds": index % 3}},
                        {
                            "action": "rule_probe.record",
                            "data": {"message": "{{ marker }}:first"},
                        },
                        {
                            "action": "rule_probe.record",
                            "data": {"message": "{{ marker }}:second"},
                        },
                    ],
                    "success_response": f"Done {marker}",
                    "failure_response": f"Failed {marker}",
                },
                phrase=f"run {marker}",
            )
        )

    results = await asyncio.wait_for(
        asyncio.gather(
            *(_say(hass, agent, f"run generated-{index}") for index in range(case_count))
        ),
        10,
    )
    assert [_speech(result) for result in results] == [
        f"Done generated-{index}" for index in range(case_count)
    ]

    messages = [message for message, _user in observed]
    assert len(messages) == case_count * 2
    for index in range(case_count):
        marker = f"generated-{index}"
        first = f"{marker}:first"
        second = f"{marker}:second"
        assert messages.count(first) == messages.count(second) == 1
        assert messages.index(first) < messages.index(second)


async def test_generated_request_rule_failure_isolated_from_healthy_siblings(
    hass
):
    """One generated native failure must not poison neighboring generated rules."""
    agent = await _agent(hass, title="Generated Request Rule failures")
    effects = []

    async def healthy(call):
        effects.append(call.data["marker"])

    hass.services.async_register("rule_probe", "healthy", healthy)

    for index in range(6):
        broken = index == 3
        action = (
            {"action": "rule_probe.missing", "data": {"marker": f"case-{index}"}}
            if broken
            else {"action": "rule_probe.healthy", "data": {"marker": f"case-{index}"}}
        )
        await agent._request_rules.async_create(
            _rule(
                "local_action",
                {
                    "actions": [action],
                    "success_response": f"ok-{index}",
                    "failure_response": f"failed-{index}",
                },
                phrase=f"case {index}",
            )
        )

    speeches = []
    for index in range(6):
        speeches.append(_speech(await _say(hass, agent, f"case {index}"), successful=index != 3))

    assert speeches == ["ok-0", "ok-1", "ok-2", "failed-3", "ok-4", "ok-5"]
    assert effects == ["case-0", "case-1", "case-2", "case-4", "case-5"]

    # Prove recovery after the failed sequence, rather than merely checking that
    # the other rules happened to run before it.
    recovered = await _say(hass, agent, "case 5")
    assert _speech(recovered) == "ok-5"
    assert effects[-1] == "case-5"
