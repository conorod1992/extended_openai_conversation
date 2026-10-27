"""Claimed turns restore their caller and keep archive updates task-local."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
    conversation_lifecycle as lifecycle,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    FunctionGroupSession,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_orchestration import _pipeline_fixture


def _turn(name, *, private=False):
    return {
        "scope": user_scope(name, source="test", device_id=f"device-{name}"),
        "archive": (f"state-{name}", f"archive-{name}"),
        "archive_turn_private": private,
        "temporary_scope": f"temporary-{name}",
        "function_group_session": FunctionGroupSession(f"state-{name}", 0.0),
        "memory_session": (f"memory-{name}", 5),
    }


def _current_turn():
    current = owner._ACTIVE_TURN.get()
    return {
        name: getattr(current, name)
        for name in (
            "scope",
            "archive",
            "archive_turn_private",
            "temporary_scope",
            "function_group_session",
            "memory_session",
        )
    }


@contextmanager
def _bind_turn(values):
    token = owner._ACTIVE_TURN.set(owner.ConversationTurnContext(**values))
    try:
        yield
    finally:
        owner._ACTIVE_TURN.reset(token)


def _assert_turn(expected):
    current = _current_turn()
    assert current == expected
    assert current["scope"] is expected["scope"]
    assert current["function_group_session"] is expected["function_group_session"]


@pytest.mark.parametrize(
    "failure",
    [
        "archive_begin",
        "group_begin",
        "group_request",
        "rule_request",
        "lifecycle_begin",
    ],
)
async def test_turn_setup_failure_restores_caller_and_finishes_acquired_requests(
    monkeypatch, failure
):
    agent, _request, _chat_log, _policy, process = _pipeline_fixture(monkeypatch)
    groups = agent._function_groups_runtime = FunctionGroupRuntime()
    rules = agent._request_rule_runtime = RequestRuleRuntime()
    group_tokens = []
    rule_tokens = []
    original_group_begin = groups.begin_request
    original_rule_begin = rules.begin_request
    group_finish = MagicMock(wraps=groups.finish_request)
    rule_finish = MagicMock(wraps=rules.finish_request)
    monkeypatch.setattr(groups, "finish_request", group_finish)
    monkeypatch.setattr(rules, "finish_request", rule_finish)

    def fail():
        raise RuntimeError(f"setup failed at {failure}")

    def begin_group(session):
        if failure == "group_request":
            fail()
        token = original_group_begin(session)
        group_tokens.append(token)
        return token

    def begin_rule(session_id, timeout):
        if failure == "rule_request":
            fail()
        token = original_rule_begin(session_id, timeout)
        rule_tokens.append(token)
        return token

    monkeypatch.setattr(groups, "begin_request", begin_group)
    monkeypatch.setattr(rules, "begin_request", begin_rule)
    if failure == "archive_begin":
        agent._async_begin_archive_session.side_effect = RuntimeError(
            f"setup failed at {failure}"
        )
    elif failure == "group_begin":
        monkeypatch.setattr(groups, "begin", lambda *_args: fail())
    elif failure == "lifecycle_begin":
        monkeypatch.setattr(owner, "begin_conversation_lifecycle", fail)

    enclosing = _turn("enclosing", private=True)
    with _bind_turn(enclosing):
        with pytest.raises(RuntimeError, match=f"setup failed at {failure}"):
            await process()
        assert group_finish.call_args_list == [
            call(token, successful=False) for token in group_tokens
        ]
        assert rule_finish.call_args_list == [
            call(token, successful=False) for token in rule_tokens
        ]
        assert not groups._requests
        assert not rules._requests
        _assert_turn(enclosing)


async def test_failed_child_admission_does_not_consume_enclosing_reset(monkeypatch):
    agent, *_rest, process = _pipeline_fixture(monkeypatch)
    agent._function_groups_runtime = FunctionGroupRuntime()
    monkeypatch.setattr(
        agent._function_groups_runtime,
        "begin_request",
        MagicMock(side_effect=RuntimeError("child admission failed")),
    )
    reset = AsyncMock()
    monkeypatch.setattr(owner, "async_reset_conversation_context", reset)
    outer = lifecycle.begin_conversation_lifecycle()
    queued = lifecycle.request_fresh_conversation("parent-state", "parent-memory")
    enclosing = _turn("enclosing-reset")
    try:
        with _bind_turn(enclosing):
            with pytest.raises(RuntimeError, match="child admission failed"):
                await process()
            reset.assert_not_awaited()
            assert lifecycle.requested_conversation_reset() is queued
            assert lifecycle.conversation_lifecycle_active()
            _assert_turn(enclosing)
    finally:
        lifecycle.end_conversation_lifecycle(outer)


@pytest.mark.parametrize("failure", ["group", "rule", "lifecycle"])
async def test_finalization_failure_still_restores_all_remaining_owners(
    monkeypatch, failure
):
    agent, *_rest, process = _pipeline_fixture(monkeypatch)
    groups = agent._function_groups_runtime = FunctionGroupRuntime()
    rules = agent._request_rule_runtime = RequestRuleRuntime()
    agent._async_dispatch_message = AsyncMock(
        return_value=SimpleNamespace(response=SimpleNamespace(error_code=None))
    )
    completed = []

    def wrap(name, finish):
        def release(*args, **kwargs):
            finish(*args, **kwargs)
            completed.append(name)
            if name == failure:
                raise RuntimeError(f"{name} finalization failed")

        return release

    monkeypatch.setattr(groups, "finish_request", wrap("group", groups.finish_request))
    monkeypatch.setattr(rules, "finish_request", wrap("rule", rules.finish_request))
    monkeypatch.setattr(
        owner,
        "end_conversation_lifecycle",
        wrap("lifecycle", lifecycle.end_conversation_lifecycle),
    )
    outer = lifecycle.begin_conversation_lifecycle()
    queued = lifecycle.request_fresh_conversation("parent-state", "parent-memory")
    enclosing = _turn("enclosing-finish")
    try:
        with _bind_turn(enclosing):
            with pytest.raises(RuntimeError, match=f"{failure} finalization failed"):
                await process()
            assert completed == ["group", "rule", "lifecycle"]
            assert not groups._requests
            assert not rules._requests
            assert lifecycle.requested_conversation_reset() is queued
            assert lifecycle.conversation_lifecycle_active()
            _assert_turn(enclosing)
    finally:
        lifecycle.end_conversation_lifecycle(outer)


def _archive_agent(monkeypatch):
    agent, *_rest = _pipeline_fixture(monkeypatch)
    agent.subentry.data[CONF_ARCHIVE_ENABLED] = True
    agent._archive = SimpleNamespace(
        async_make_private=AsyncMock(return_value={"private_mode_enabled": True}),
        async_resume_saving=AsyncMock(),
    )
    return agent


async def test_nested_archive_updates_restore_parent_and_keep_group_session_identity(
    monkeypatch,
):
    agent = _archive_agent(monkeypatch)
    parent = _turn("parent", private=True)
    child = _turn("child")
    group = child["function_group_session"]
    agent._archive.async_resume_saving.return_value = SimpleNamespace(
        session_id="resumed-child", retention_state="retained"
    )

    with _bind_turn(parent):
        with _bind_turn(child):
            await agent._async_execute_archive_tool("private", {})
            assert owner._ACTIVE_TURN.get().archive_turn_private is True
            resumed = await agent._async_execute_archive_tool("resume", {})
            assert resumed["session_id"] == "resumed-child"
            assert owner._ACTIVE_TURN.get().archive == ("state-child", "resumed-child")
            assert owner._ACTIVE_TURN.get().archive_turn_private is True
            assert owner._ACTIVE_TURN.get().function_group_session is group
            group.loaded_group_ids.add("child-loaded")
            assert owner._ACTIVE_TURN.get().function_group_session.loaded_group_ids == {
                "child-loaded"
            }
        _assert_turn(parent)
        assert parent["function_group_session"].loaded_group_ids == set()
    assert group.loaded_group_ids == {"child-loaded"}


async def test_inherited_tasks_rebind_archive_flags_but_share_group_session(
    monkeypatch,
):
    agent = _archive_agent(monkeypatch)
    parent = _turn("parent")
    group = parent["function_group_session"]
    task_owner = ContextVar("test_turn_archive_owner", default=None)
    ready = {name: asyncio.Event() for name in ("a", "b")}
    release = asyncio.Event()

    async def make_private(session_id):
        assert session_id == "archive-parent"
        name = task_owner.get()
        ready[name].set()
        await release.wait()
        return {"private_mode_enabled": True}

    async def resume(*_args, **_kwargs):
        return SimpleNamespace(
            session_id=f"resumed-{task_owner.get()}", retention_state="retained"
        )

    agent._archive.async_make_private.side_effect = make_private
    agent._archive.async_resume_saving.side_effect = resume

    async def child(name):
        token = task_owner.set(name)
        try:
            assert owner._ACTIVE_TURN.get().function_group_session is group
            group.loaded_group_ids.add(name)
            await agent._async_execute_archive_tool("private", {})
            await agent._async_execute_archive_tool("resume", {})
            assert owner._ACTIVE_TURN.get().archive_turn_private is True
            assert owner._ACTIVE_TURN.get().archive == (
                "state-parent",
                f"resumed-{name}",
            )
            assert owner._ACTIVE_TURN.get().function_group_session is group
            return _current_turn()
        finally:
            task_owner.reset(token)

    with _bind_turn(parent):
        tasks = [asyncio.create_task(child(name)) for name in ("a", "b")]
        try:
            await asyncio.gather(*(event.wait() for event in ready.values()))
            _assert_turn(parent)
            assert group.loaded_group_ids == {"a", "b"}
            release.set()
            results = await asyncio.gather(*tasks)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        _assert_turn(parent)
        assert [result["archive"][1] for result in results] == [
            "resumed-a",
            "resumed-b",
        ]
        assert all(result["function_group_session"] is group for result in results)


async def test_failed_private_write_keeps_irreversible_turn_veto(monkeypatch):
    agent = _archive_agent(monkeypatch)
    agent._archive.async_make_private.side_effect = RuntimeError("private write failed")
    initial = _turn("private-failure")
    with _bind_turn(initial):
        with pytest.raises(RuntimeError, match="private write failed"):
            await agent._async_execute_archive_tool("private", {})
        assert owner._ACTIVE_TURN.get().archive_turn_private is True
        assert owner._ACTIVE_TURN.get().archive == initial["archive"]
        assert (
            owner._ACTIVE_TURN.get().function_group_session
            is initial["function_group_session"]
        )


async def test_resume_preserves_privacy_update_from_awaited_operation(monkeypatch):
    agent = _archive_agent(monkeypatch)
    initial = _turn("resume-callback")

    async def resume(*_args, **_kwargs):
        # An awaited operation may itself update this task's turn binding. A
        # caller must not restore a pre-await privacy flag while publishing its ID.
        await agent._async_execute_archive_tool("private", {})
        return SimpleNamespace(
            session_id="resumed-callback", retention_state="retained"
        )

    agent._archive.async_resume_saving.side_effect = resume
    with _bind_turn(initial):
        result = await agent._async_execute_archive_tool("resume", {})
        assert result["session_id"] == "resumed-callback"
        assert owner._ACTIVE_TURN.get().archive == (
            "state-resume-callback",
            "resumed-callback",
        )
        assert owner._ACTIVE_TURN.get().archive_turn_private is True
        assert owner._ACTIVE_TURN.get().scope is initial["scope"]
        assert (
            owner._ACTIVE_TURN.get().function_group_session
            is initial["function_group_session"]
        )
