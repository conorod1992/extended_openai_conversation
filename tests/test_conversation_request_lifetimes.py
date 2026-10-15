"""Shared request guarantees and deliberately different runtime expiry policies."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    function_groups,
    request_rules,
)


@pytest.fixture(params=["groups", "rules"])
def runtime(request, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        function_groups, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    monkeypatch.setattr(request_rules, "monotonic", lambda: clock.now)
    if request.param == "groups":
        state = function_groups.FunctionGroupRuntime()

        def begin(key):
            return state.begin_request(state.begin(key, 1))

        def save(key):
            state.begin(key, 1).loaded_group_ids.add("loaded")

        def present(key):
            state.begin("sweep", 1)
            return key in state._sessions and bool(
                state._sessions[key].loaded_group_ids
            )

        end = state.end_continuity
    else:
        state = request_rules.RequestRuleRuntime()

        def begin(key):
            return state.begin_request(key, 1)

        def save(key):
            state.set(key, {"model": "chosen"}, 1)

        def present(key):
            state.get("sweep", 1)
            return key in state._conversation_overrides

        end = state.reset_continuity
    return SimpleNamespace(
        state=state, clock=clock, begin=begin, save=save, present=present, end=end
    )


@pytest.mark.parametrize("successful", [True, False])
def test_overlapping_completion_and_duplicate_tokens(runtime, successful):
    key = "continuity:owner:conversation:one"
    runtime.save(key)
    first, second = runtime.begin(key), runtime.begin(key)
    runtime.clock.now = 120
    assert runtime.present(key)
    runtime.state.finish_request(first, successful=successful)
    assert runtime.present(key)
    runtime.state.finish_request(second, successful=successful)
    runtime.clock.now = 121
    assert runtime.present(key) is successful
    runtime.clock.now = 179
    runtime.state.finish_request(second, successful=True)
    runtime.state.finish_request(object(), successful=True)
    runtime.clock.now = 181
    assert not runtime.present(key)
    assert not runtime.state._requests


@pytest.mark.parametrize("reset_all", [True, False])
def test_end_and_replacement_reject_late_completion(runtime, reset_all):
    key = "continuity:owner:conversation:one"
    sibling = "continuity:owner-other:conversation:one"
    runtime.save(key)
    runtime.save(sibling)
    old, other = runtime.begin(key), runtime.begin(sibling)
    runtime.clock.now = 30
    if reset_all:
        runtime.state.reset_all()
    else:
        runtime.end("owner")
        assert runtime.present(sibling)
    runtime.save(key)
    runtime.clock.now = 80
    runtime.state.finish_request(old, successful=True)
    runtime.state.finish_request(other, successful=False)
    runtime.clock.now = 91
    assert not runtime.present(key)


def test_function_groups_use_incoming_sweep_timeout_and_strict_boundary(monkeypatch):
    now = 0.0
    monkeypatch.setattr(function_groups, "time", SimpleNamespace(monotonic=lambda: now))
    runtime = function_groups.FunctionGroupRuntime()
    original = runtime.begin("one", 10)
    now = 60
    runtime.begin("sweep", 1)
    assert runtime._sessions["one"] is original
    now = 60.001
    runtime.begin("sweep", 1)
    assert "one" not in runtime._sessions


def test_rules_use_stored_timeout_and_inclusive_boundary(monkeypatch):
    now = 0.0
    monkeypatch.setattr(request_rules, "monotonic", lambda: now)
    runtime = request_rules.RequestRuleRuntime()
    runtime.set("one", {"model": "chosen"}, 10)
    now = 60
    runtime.get("sweep", 1)
    assert "one" in runtime._conversation_overrides
    now = 599.999
    runtime.get("sweep", 1)
    assert "one" in runtime._conversation_overrides
    now = 600
    runtime.get("sweep", 1)
    assert "one" not in runtime._conversation_overrides


def test_route_reset_preserves_execution_and_active_protection_only(monkeypatch):
    now = 0.0
    monkeypatch.setattr(request_rules, "monotonic", lambda: now)
    runtime = request_rules.RequestRuleRuntime()
    token = runtime.begin_request("one", 1)  # No override is required for ownership.
    ownership = runtime.request_ownership("one")
    runtime.reset("one")
    assert not ownership[0].ended
    runtime.set("one", {"model": "replacement"}, 1)
    now = 120
    runtime.get("sweep", 1)
    assert "one" in runtime._conversation_overrides
    runtime.finish_request(token, successful=True)
    assert runtime.get("one", 1) == {}
    assert not ownership[0].ended


def test_route_reset_all_invalidates_captured_execution_ownership():
    runtime = request_rules.RequestRuleRuntime()
    token = runtime.begin_request("one", 1)
    ownership = runtime.request_ownership("one")
    runtime.reset_all()
    assert ownership[0].ended
    runtime.set("one", {"model": "replacement"}, 1)
    runtime.finish_request(token, successful=True)
    assert runtime.get("one", 1) == {"model": "replacement"}


def test_stale_group_session_cannot_refresh_replacement(monkeypatch):
    now = 0.0
    monkeypatch.setattr(function_groups, "time", SimpleNamespace(monotonic=lambda: now))
    runtime = function_groups.FunctionGroupRuntime()
    old = runtime.begin("one", 1)
    runtime.end("one")
    new = runtime.begin("one", 1)
    token = runtime.begin_request(old)
    now = 120
    runtime.begin("sweep", 1)
    assert "one" not in runtime._sessions
    runtime.finish_request(token, successful=True)
    assert new.last_active == 0


def test_new_request_after_route_reset_can_refresh_while_old_request_cannot(
    monkeypatch,
):
    now = 0.0
    monkeypatch.setattr(request_rules, "monotonic", lambda: now)
    runtime = request_rules.RequestRuleRuntime()
    old = runtime.begin_request("one", 1)
    runtime.reset("one")
    now = 30.0
    runtime.set("one", {"model": "replacement"}, 1)
    new = runtime.begin_request("one", 1)
    now = 80.0
    runtime.finish_request(old, successful=True)
    assert runtime._conversation_overrides["one"][1] == 30.0
    runtime.finish_request(new, successful=True)
    assert runtime._conversation_overrides["one"][1] == 80.0
    now = 139.999
    runtime.get("sweep", 1)
    assert "one" in runtime._conversation_overrides
    now = 140.0
    assert runtime.get("one", 1) == {}
