"""Rule action callbacks and captured results belong to one native script run."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import UNDEFINED
from tests.test_request_rules import local_rule


@pytest.fixture
def rule_runs(hass, monkeypatch):
    callbacks = {}
    executors = {}
    captured = {}
    guards = {}
    calls = []
    monkeypatch.setattr(rr.cv, "SCRIPT_SCHEMA", lambda actions: actions)
    monkeypatch.setattr(
        rr,
        "async_validate_actions_config",
        AsyncMock(side_effect=lambda _hass, actions: actions),
    )

    class Script:
        def __init__(self, _hass, actions, name, *_args, **_kwargs):
            self.owner = name.removeprefix("Request Rule ")
            self.markers = next(
                step["variables"] for step in reversed(actions) if "variables" in step
            )

        async def async_run(self, _variables, _context):
            assert (
                rr._ACTIVE_RULE_ACTION.get().function_executor is executors[self.owner]
            )
            values = rr._ACTIVE_RULE_ACTION.get().function_results
            assert values == {}
            captured[self.owner] = values
            guards[self.owner] = rr._ACTIVE_RULE_ACTION.get().authorize_action
            assert guards[self.owner] is not None
            await callbacks[self.owner]()
            return SimpleNamespace(
                variables=self.markers, conversation_response=UNDEFINED
            )

        async def async_unload(self):
            pass

    monkeypatch.setattr(rr, "Script", Script)

    async def run(owner, callback):
        async def execute(function, arguments):
            calls.append((owner, function, arguments))
            return {"result": {"owner": owner}}

        callbacks[owner] = callback
        executors[owner] = execute
        rule = rr.validate_rule(local_rule(owner))
        rule["action"]["actions"] = [{"variables": {"value": owner}}]
        return await rr._async_evaluate_matched_rule(
            hass,
            rr.RuleMatch(rule, "run", False, 100),
            "run",
            rr.RequestRuleRuntime(),
            owner,
            rr.DEFAULT_CHAT_MODEL,
            None,
            10,
            execute,
            None,
        )

    return SimpleNamespace(
        run=run, captured=captured, guards=guards, calls=calls, executors=executors
    )


async def test_nested_rules_restore_callback_guard_and_result_owner(rule_runs):
    async def child():
        assert await rr.async_call_active_function("read", {}, "child") == {
            "owner": "b"
        }

    async def parent():
        await rr.async_call_active_function("read", {}, "before")
        assert (await rule_runs.run("b", child)).successful
        assert (
            rr._ACTIVE_RULE_ACTION.get().function_executor is rule_runs.executors["a"]
        )
        assert rr._ACTIVE_RULE_ACTION.get().authorize_action is rule_runs.guards["a"]
        assert rr._ACTIVE_RULE_ACTION.get().function_results is rule_runs.captured["a"]
        await rr.async_call_active_function("read", {}, "after")

    assert (await rule_runs.run("a", parent)).successful
    assert rule_runs.captured == {
        "a": {"before": {"owner": "a"}, "after": {"owner": "a"}},
        "b": {"child": {"owner": "b"}},
    }
    assert rule_runs.guards["a"] is not rule_runs.guards["b"]
    assert [owner for owner, *_rest in rule_runs.calls] == ["a", "b", "a"]
    with pytest.raises(HomeAssistantError, match="only available"):
        await rr.async_call_active_function("outside", {})


async def test_concurrent_rules_share_only_their_own_result_map(rule_runs):
    ready = {owner: asyncio.Event() for owner in ("a", "b")}

    async def body(owner, other):
        ready[owner].set()
        await ready[other].wait()
        assert (
            rr._ACTIVE_RULE_ACTION.get().function_executor is rule_runs.executors[owner]
        )
        await rr.async_call_active_function("read", {}, "value")
        assert rr._ACTIVE_RULE_ACTION.get().function_results == {
            "value": {"owner": owner}
        }

    results = await asyncio.gather(
        rule_runs.run("a", lambda: body("a", "b")),
        rule_runs.run("b", lambda: body("b", "a")),
    )
    assert all(result.successful for result in results)
    assert rule_runs.captured["a"] is not rule_runs.captured["b"]


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_child_rule_restores_parent_before_continuing(rule_runs, cancel):
    entered = asyncio.Event()

    async def child():
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("child script failed")

    async def parent():
        task = asyncio.create_task(rule_runs.run("b", child))
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert not (await task).successful
        assert (
            rr._ACTIVE_RULE_ACTION.get().function_executor is rule_runs.executors["a"]
        )
        await rr.async_call_active_function("read", {}, "healthy")

    assert (await rule_runs.run("a", parent)).successful
    assert rule_runs.captured["a"] == {"healthy": {"owner": "a"}}
    assert rr._ACTIVE_RULE_ACTION.get().function_executor is None
    assert rr._ACTIVE_RULE_ACTION.get().authorize_action is None
    assert rr._ACTIVE_RULE_ACTION.get().function_results is None
