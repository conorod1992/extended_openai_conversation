"""Completion ownership through genuine HA Conversation and Script execution."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_groups,
    request_rules,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONVERSATION_CONTINUITY_DEVICE,
)
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_request_rules_script_semantics import _local


@pytest.mark.parametrize("invalidation", ["route_reset", "end", "reset_all"])
async def test_inflight_completion_cannot_refresh_replacement_state(
    hass, monkeypatch, invalidation
):
    now = 0.0
    monkeypatch.setattr(function_groups, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(request_rules, "monotonic", lambda: now)
    agent = await _agent(
        hass, **{CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE}
    )
    _provider(monkeypatch, agent, [])
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(_call):
        entered.set()
        await release.wait()

    hass.services.async_register("lease_probe", "wait", wait)
    await agent._request_rules.async_create(_local([{"action": "lease_probe.wait"}]))
    task = asyncio.create_task(_say(hass, agent, "run rule", device_id="lease-device"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        rules = agent._request_rule_runtime
        groups = agent._function_groups_runtime
        (session_id,) = rules._requests.owners
        ownership = rules.request_ownership(session_id)
        assert groups._requests.active(session_id)
        now = 30.0
        if invalidation == "route_reset":
            rules.reset(session_id)
            assert not ownership[0].ended
        elif invalidation == "end":
            owner = session_id.removeprefix("continuity:").rpartition(":conversation:")[
                0
            ]
            assert owner
            groups.end_continuity(owner)
            rules.reset_continuity(owner)
            assert ownership[0].ended
        else:
            rules.reset_all()
            groups.reset_all()
            assert ownership[0].ended
        rules.set(session_id, {"chat_model": "replacement"}, 1)
        replacement = groups.begin(session_id, 1)
        now = 80.0
        release.set()
        assert _speech(await asyncio.wait_for(task, 3)) == "Done"
        assert rules._conversation_overrides[session_id][1] == 30.0
        assert replacement.last_active == (
            80.0 if invalidation == "route_reset" else 30.0
        )
        assert not rules._requests and not groups._requests
        now = 91.0
        assert rules.get(session_id, 1) == {}
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
