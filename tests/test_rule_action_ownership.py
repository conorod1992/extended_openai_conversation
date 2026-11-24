"""Rule action callbacks belong to one native script run."""

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
            self.run_task = None
            self.markers = next(
                step["variables"] for step in reversed(actions) if "variables" in step
            )

        async def async_run(self, _variables, _context):
            self.run_task = asyncio.current_task()
            assert (
                rr._ACTIVE_RULE_ACTION.get().function_executor is executors[self.owner]
            )
            guards[self.owner] = rr._ACTIVE_RULE_ACTION.get().authorize_action
            assert guards[self.owner] is not None
            await callbacks[self.owner]()
            return SimpleNamespace(
                variables=self.markers, conversation_response=UNDEFINED
            )

        async def async_stop(self):
            # Match the native runner's public stop contract. Cancellation is
            # requested through async_stop, while the owner collects async_run.
            if self.run_task is not None and not self.run_task.done():
                self.run_task.cancel()
                await asyncio.gather(self.run_task, return_exceptions=True)

        async def async_unload(self):
            await self.async_stop()

    monkeypatch.setattr(rr, "Script", Script)

    async def run(owner, callback):
        async def execute(function, arguments):
            calls.append((owner, function, arguments))
            return {"owner": owner}

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
        run=run, guards=guards, calls=calls, executors=executors
    )


async def test_nested_rules_restore_executor_and_guard(rule_runs):
    async def child():
        assert await rr.async_call_active_function("read", {}) == {
            "owner": "b"
        }

    async def parent():
        assert await rr.async_call_active_function("read", {}) == {"owner": "a"}
        assert (await rule_runs.run("b", child)).successful
        assert (
            rr._ACTIVE_RULE_ACTION.get().function_executor is rule_runs.executors["a"]
        )
        assert rr._ACTIVE_RULE_ACTION.get().authorize_action is rule_runs.guards["a"]
        assert await rr.async_call_active_function("read", {}) == {"owner": "a"}

    assert (await rule_runs.run("a", parent)).successful
    assert rule_runs.guards["a"] is not rule_runs.guards["b"]
    assert [owner for owner, *_rest in rule_runs.calls] == ["a", "b", "a"]
    with pytest.raises(HomeAssistantError, match="only available"):
        await rr.async_call_active_function("outside", {})


async def test_concurrent_rules_use_only_their_own_executor(rule_runs):
    ready = {owner: asyncio.Event() for owner in ("a", "b")}

    async def body(owner, other):
        ready[owner].set()
        await ready[other].wait()
        assert (
            rr._ACTIVE_RULE_ACTION.get().function_executor is rule_runs.executors[owner]
        )
        assert await rr.async_call_active_function("read", {}) == {"owner": owner}

    results = await asyncio.gather(
        rule_runs.run("a", lambda: body("a", "b")),
        rule_runs.run("b", lambda: body("b", "a")),
    )
    assert all(result.successful for result in results)


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
        assert await rr.async_call_active_function("read", {}) == {"owner": "a"}

    assert (await rule_runs.run("a", parent)).successful
    assert rr._ACTIVE_RULE_ACTION.get().function_executor is None
    assert rr._ACTIVE_RULE_ACTION.get().authorize_action is None
