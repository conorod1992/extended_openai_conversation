"""Focused defensive coverage for conversation-agent policy and tool boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import conversation as conv


def _agent(*, data=None):
    agent = object.__new__(conv.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(data=data or {})
    agent._temporary_memory = None
    agent._archive = None
    agent._memory = None
    return agent


def _policy(*, read=None, control=None, **extra):
    return SimpleNamespace(
        guest_active=extra.pop("guest_active", False),
        shared_memory_read=extra.pop("shared_memory_read", False),
        shared_memory_write=extra.pop("shared_memory_write", False),
        temporary_memory=extra.pop("temporary_memory", True),
        knowledge_access=extra.pop("knowledge_access", True),
        archive_access=extra.pop("archive_access", True),
        web_search=extra.pop("web_search", True),
        allows_entity_read=read or (lambda _entity_id: True),
        allows_entity_control=control or (lambda _entity_id: True),
        **extra,
    )


@pytest.mark.parametrize(
    ("mode", "ha_default", "conditional", "expected"),
    [
        (conv.CONTINUE_CONVERSATION_ALWAYS, False, None, True),
        (conv.CONTINUE_CONVERSATION_ALWAYS, True, False, True),
        (conv.CONTINUE_CONVERSATION_CONDITIONAL, True, True, True),
        (conv.CONTINUE_CONVERSATION_CONDITIONAL, True, False, False),
        (conv.CONTINUE_CONVERSATION_CONDITIONAL, True, None, False),
        ("home_assistant", False, True, False),
        ("home_assistant", True, False, True),
    ],
)
def test_continue_conversation_resolution(mode, ha_default, conditional, expected) -> None:
    assert conv._resolve_continue_conversation(mode, ha_default, conditional) is expected


def test_guest_argument_filter_rejects_broad_area_and_device_selectors() -> None:
    policy = _policy()

    assert not conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"target": {"area_id": "kitchen"}}, policy, control=False
    )
    assert not conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"device_ids": ["abc"]}, policy, control=True
    )


def test_guest_argument_filter_requires_every_explicit_entity_to_be_allowed() -> None:
    policy = _policy(
        read=lambda entity_id: entity_id != "sensor.private",
        control=lambda entity_id: entity_id == "light.allowed",
    )

    assert conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"entity_id": "sensor.public,sensor.other"}, policy, control=False
    )
    assert not conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"entity_ids": "sensor.public, sensor.private"}, policy, control=False
    )
    assert conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"target": [{"entity_id": "light.allowed"}]}, policy, control=True
    )
    assert not conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"target": [{"entity_id": "light.denied"}]}, policy, control=True
    )


def test_guest_argument_filter_checks_list_entity_selectors() -> None:
    policy = _policy(read=lambda entity_id: entity_id != "sensor.private")

    assert conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"entity_id": ["sensor.one"]}, policy, control=False
    )
    assert not conv.ExtendedOpenAIAgentEntity._guest_arguments_allowed(
        {"entity_id": ["sensor.one", "sensor.private"]}, policy, control=False
    )


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        (None, ["user-1", conv.SHARED_HOUSEHOLD_SCOPE_ID]),
        ("personal", ["user-1"]),
        ("household", [conv.SHARED_HOUSEHOLD_SCOPE_ID]),
    ],
)
def test_filter_read_scopes_selects_only_requested_scope(selector, expected) -> None:
    assert conv.ExtendedOpenAIAgentEntity._filter_read_scopes(
        ["user-1", conv.SHARED_HOUSEHOLD_SCOPE_ID], selector
    ) == expected


def test_filter_read_scopes_rejects_unknown_selector() -> None:
    with pytest.raises(ValueError, match="scope must be personal or household"):
        conv.ExtendedOpenAIAgentEntity._filter_read_scopes(["user-1"], "other")


def test_provider_tool_policy_only_restricts_web_search(monkeypatch) -> None:
    agent = _agent()
    monkeypatch.setattr(
        conv.ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        lambda self: _policy(web_search=False),
    )

    assert agent._provider_tool_allowed("code_interpreter") is True
    assert agent._provider_tool_allowed("web_search") is False


def test_conversation_lifecycle_rejects_unknown_operation() -> None:
    agent = _agent()

    with pytest.raises(ValueError, match="unknown conversation lifecycle operation"):
        agent._execute_conversation_lifecycle_tool("reset_everything")


def test_conversation_lifecycle_schedules_fresh_context_without_active_sessions(
    monkeypatch,
) -> None:
    agent = _agent()
    calls = []
    monkeypatch.setattr(
        conv,
        "request_fresh_conversation",
        lambda state_key, memory_key: calls.append((state_key, memory_key)),
    )
    state_token = conv._ACTIVE_FUNCTION_GROUP_SESSION.set(None)
    memory_token = conv._ACTIVE_MEMORY_SESSION.set(None)
    try:
        result = agent._execute_conversation_lifecycle_tool("start_fresh")
    finally:
        conv._ACTIVE_FUNCTION_GROUP_SESSION.reset(state_token)
        conv._ACTIVE_MEMORY_SESSION.reset(memory_token)

    assert calls == [(None, None)]
    assert result["status"] == "scheduled"


async def test_temporary_memory_tool_requires_permission_store_and_scope(monkeypatch) -> None:
    agent = _agent(data={conv.CONF_TEMPORARY_MEMORY: "balanced"})

    monkeypatch.setattr(
        conv.ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        lambda self: _policy(temporary_memory=False),
    )
    with pytest.raises(RuntimeError, match=conv.GUEST_MODE_UNAVAILABLE):
        await agent._async_execute_temporary_memory_tool("add", {})

    monkeypatch.setattr(
        conv.ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        lambda self: _policy(temporary_memory=True),
    )
    with pytest.raises(RuntimeError, match="temporary memory is unavailable"):
        await agent._async_execute_temporary_memory_tool("add", {})

    agent._temporary_memory = SimpleNamespace()
    token = conv._ACTIVE_TEMPORARY_SCOPE.set(None)
    try:
        with pytest.raises(RuntimeError, match="unavailable for this request"):
            await agent._async_execute_temporary_memory_tool("add", {})
    finally:
        conv._ACTIVE_TEMPORARY_SCOPE.reset(token)


class _TemporaryMemory:
    def __init__(self):
        self.calls = []

    async def async_add(self, scope_id, content, expires_at, category):
        self.calls.append(("add", scope_id, content, expires_at, category))
        return {"status": "added"}

    async def async_delete(self, scope_id, memory_ids):
        self.calls.append(("delete", scope_id, memory_ids))
        return len(memory_ids)


async def test_temporary_memory_tool_validates_and_executes_add_delete(monkeypatch) -> None:
    agent = _agent(data={conv.CONF_TEMPORARY_MEMORY: "balanced"})
    store = _TemporaryMemory()
    agent._temporary_memory = store
    monkeypatch.setattr(
        conv.ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        lambda self: _policy(temporary_memory=True),
    )
    token = conv._ACTIVE_TEMPORARY_SCOPE.set("scope-1")
    try:
        with pytest.raises(ValueError, match="content, expires_at, and category"):
            await agent._async_execute_temporary_memory_tool(
                "add", {"content": 123, "expires_at": "tomorrow"}
            )

        added = await agent._async_execute_temporary_memory_tool(
            "add",
            {
                "content": "buy milk",
                "expires_at": "2026-09-13T12:00:00+01:00",
                "category": "shopping",
            },
        )
        assert added == {"status": "added"}

        with pytest.raises(ValueError, match="memory_ids must be a list of strings"):
            await agent._async_execute_temporary_memory_tool("delete", {"memory_ids": "x"})

        deleted = await agent._async_execute_temporary_memory_tool(
            "delete", {"memory_ids": ["one", "two"]}
        )
        assert deleted == {"status": "deleted", "deleted": 2}

        with pytest.raises(ValueError, match="unknown temporary-memory operation"):
            await agent._async_execute_temporary_memory_tool("unknown", {})
    finally:
        conv._ACTIVE_TEMPORARY_SCOPE.reset(token)


async def test_archive_tool_requires_store_and_active_session() -> None:
    agent = _agent(data={conv.CONF_ARCHIVE_ENABLED: True})

    with pytest.raises(RuntimeError, match="conversation archive is unavailable"):
        await agent._async_execute_archive_tool("search", {})

    agent._archive = SimpleNamespace()
    scope_token = conv._ACTIVE_SCOPE.set(None)
    archive_token = conv._ACTIVE_ARCHIVE.set(None)
    try:
        with pytest.raises(RuntimeError, match="active conversation session is unavailable"):
            await agent._async_execute_archive_tool("search", {})
    finally:
        conv._ACTIVE_SCOPE.reset(scope_token)
        conv._ACTIVE_ARCHIVE.reset(archive_token)


class _Archive:
    def __init__(self):
        self.calls = []

    async def async_delete_session(self, scope_id, session_id):
        self.calls.append((scope_id, session_id))
        return {"deleted": True}

    async def async_delete_selected(self, scope_id, session_ids, *, confirm):
        return {"scope": scope_id, "sessions": session_ids, "confirm": confirm}


async def test_archive_delete_operations_validate_inputs_and_preserve_scope() -> None:
    agent = _agent(data={conv.CONF_ARCHIVE_ENABLED: True})
    archive = _Archive()
    agent._archive = archive
    scope = SimpleNamespace(scope_id="scope-1")
    scope_token = conv._ACTIVE_SCOPE.set(scope)
    archive_token = conv._ACTIVE_ARCHIVE.set(("session-key", "session-1"))
    try:
        current = await agent._async_execute_archive_tool("delete_current", {})
        assert current == {"session_id": "session-1", "deleted": True}
        assert archive.calls == [("scope-1", "session-1")]

        with pytest.raises(ValueError, match="session_ids must be a list of strings"):
            await agent._async_execute_archive_tool(
                "delete_selected", {"session_ids": ["one", 2]}
            )

        selected = await agent._async_execute_archive_tool(
            "delete_selected", {"session_ids": ["one", "two"], "confirm": True}
        )
        assert selected == {
            "scope": "scope-1",
            "sessions": ["one", "two"],
            "confirm": True,
        }

        with pytest.raises(ValueError, match="unknown archive operation"):
            await agent._async_execute_archive_tool("unknown", {})
    finally:
        conv._ACTIVE_SCOPE.reset(scope_token)
        conv._ACTIVE_ARCHIVE.reset(archive_token)
