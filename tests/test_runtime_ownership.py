"""Stable runtime ownership and live capability regression contracts."""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as owner,
    debug,
    entity as base,
    function_execution,
    tool_exchange,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _TEMPORARY_MEMORY_PREFETCH,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
    async_setup_delayed_tools,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    is_tool_result_content,
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    _ACTIVE_OWNER_SCOPE_ID,
)
from homeassistant.components import conversation
from homeassistant.helpers import llm

RUNTIME_METHODS = frozenset(
    {
        "_execute_function_tool",
        "_async_dispatch_function_tool",
        "_async_retrieve_memories",
        "_async_select_memories",
        "_async_rank_memories",
        "_async_retrieve_temporary_memories",
        "_async_load_temporary_memories",
        "_async_execute_memory_tool",
        "_async_execute_temporary_memory_tool",
        "_async_execute_archive_tool",
        "_async_execute_knowledge_tool",
    }
)


def _replacements(source):
    """Recognize writes through obvious aliases without constraining method bodies."""
    tree = ast.parse(source)
    strings = {name: name for name in RUNTIME_METHODS}
    setters = {"setattr", "delattr"}
    # Resolve simple aliases to attribute names and built-in mutators.
    for _ in range(3):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        value = node.value
                        if isinstance(value, ast.Constant) and isinstance(
                            value.value, str
                        ):
                            strings[target.id] = value.value
                        elif isinstance(value, ast.Name):
                            if value.id in strings:
                                strings[target.id] = strings[value.id]
                            if value.id in setters:
                                setters.add(target.id)

    def protected(value):
        return (isinstance(value, ast.Constant) and value.value in RUNTIME_METHODS) or (
            isinstance(value, ast.Name) and strings.get(value.id) in RUNTIME_METHODS
        )

    found = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if any(
                (isinstance(part, ast.Attribute) and part.attr in RUNTIME_METHODS)
                or (isinstance(part, ast.Name) and part.id in RUNTIME_METHODS)
                or (isinstance(part, ast.Subscript) and protected(part.slice))
                for part in ast.walk(target)
            ):
                found.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in setters
            and len(node.args) >= 2
            and protected(node.args[1])
        ):
            found.append(node.lineno)
    return found


def test_runtime_methods_have_source_owners_and_no_reassignments():
    violations = [
        f"{path.name}:{line}"
        for path in Path(owner.__file__).parent.rglob("*.py")
        for line in _replacements(path.read_text(encoding="utf-8"))
    ]
    assert violations == []
    for name in RUNTIME_METHODS:
        method = getattr(owner.ExtendedOpenAIAgentEntity, name)
        assert method.__module__ == owner.__name__
        assert not hasattr(method, "__wrapped__")
    assert (
        base.ExtendedOpenAIBaseLLMEntity._execute_function_tool.__module__
        == base.__name__
    )


@pytest.mark.parametrize(
    "source",
    [
        "Agent._execute_function_tool = replacement",
        "alias = Agent\nalias._async_retrieve_memories = replacement",
        "setattr(Agent, '_async_execute_archive_tool', replacement)",
        "name = '_async_execute_memory_tool'\nsetter = setattr\nsetter(Agent, name, replacement)",
        "delattr(Agent, '_async_retrieve_temporary_memories')",
        "del Agent._async_execute_temporary_memory_tool",
        "Agent.__dict__['_execute_function_tool'] = replacement",
    ],
)
def test_structural_guard_detects_obvious_replacement_forms(source):
    assert _replacements(source)


async def test_repeated_setup_preserves_runtime_identity(hass, monkeypatch):
    cls = owner.ExtendedOpenAIAgentEntity
    methods = {name: getattr(cls, name) for name in RUNTIME_METHODS}
    executor = base.ExtendedOpenAIBaseLLMEntity._execute_function_tool
    monkeypatch.setattr(DelayedToolManager, "async_setup", AsyncMock())
    for _ in range(2):
        await async_setup_delayed_tools(hass)
        assert methods == {name: getattr(cls, name) for name in methods}
        assert base.ExtendedOpenAIBaseLLMEntity._execute_function_tool is executor


@pytest.fixture
def runtime_agent():
    agent = object.__new__(owner.ExtendedOpenAIAgentEntity)
    agent.subentry = SimpleNamespace(
        data={
            "memory_mode": "automatic",
            "temporary_memory": "enabled",
            "archive_enabled": True,
            "archive_model_search_enabled": True,
        }
    )
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    scope = owner._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="user", scope_id="user:alice", user_id="alice")
    )
    temporary = owner._ACTIVE_TEMPORARY_SCOPE.set("session")
    archive = owner._ACTIVE_ARCHIVE.set(("session", "id"))
    prefetch = _TEMPORARY_MEMORY_PREFETCH.set(None)
    try:
        yield agent
    finally:
        _TEMPORARY_MEMORY_PREFETCH.reset(prefetch)
        owner._ACTIVE_ARCHIVE.reset(archive)
        owner._ACTIVE_TEMPORARY_SCOPE.reset(temporary)
        owner._ACTIVE_SCOPE.reset(scope)


async def test_live_memory_gate_and_embedding_provider(runtime_agent):
    agent = runtime_agent
    agent._memory = SimpleNamespace(
        set_embedding_provider=Mock(), async_search=AsyncMock(return_value=[])
    )
    agent._async_select_memories = AsyncMock(return_value=[])
    for enabled in (True, False, True):
        agent.subentry.data = {
            **agent.subentry.data,
            "memory_mode": "automatic" if enabled else "off",
        }
        before = agent._async_select_memories.await_count
        assert await agent._async_retrieve_memories(None, "query") == []
        assert agent._async_select_memories.await_count == before + int(enabled)
        if enabled:
            assert await agent._async_execute_memory_tool(
                "search", {"query": "query"}, None
            ) == {"memories": []}
        else:
            with pytest.raises(RuntimeError, match="disabled"):
                await agent._async_execute_memory_tool(
                    "search", {"query": "query"}, None
                )
    agent.subentry.data = {
        **agent.subentry.data,
        "memory_retrieval_mode": "hybrid",
        "memory_embedding_model": "changed",
    }
    await agent._async_retrieve_memories(None, "query")
    agent._memory.set_embedding_provider.assert_called_with(
        agent._async_create_embeddings, "changed"
    )


async def test_live_temporary_and_archive_gates(runtime_agent):
    agent = runtime_agent
    agent._temporary_memory = SimpleNamespace(
        async_active=AsyncMock(return_value=[]), async_delete=AsyncMock(return_value=1)
    )
    agent._archive = SimpleNamespace(async_get=AsyncMock(return_value={"turns": []}))
    for enabled in (True, False, True):
        agent.subentry.data = {
            **agent.subentry.data,
            "temporary_memory": "enabled" if enabled else "off",
            "archive_enabled": enabled,
            "archive_model_search_enabled": enabled,
        }
        before = agent._temporary_memory.async_active.await_count
        assert await agent._async_retrieve_temporary_memories() == []
        assert agent._temporary_memory.async_active.await_count == before + int(enabled)
        if enabled:
            assert await agent._async_execute_temporary_memory_tool(
                "delete", {"memory_ids": ["id"]}
            ) == {"status": "deleted", "deleted": 1}
            assert await agent._async_execute_archive_tool(
                "get", {"session_id": "id"}
            ) == {"turns": []}
        else:
            with pytest.raises(RuntimeError, match="disabled"):
                await agent._async_execute_temporary_memory_tool(
                    "delete", {"memory_ids": ["id"]}
                )
            with pytest.raises(RuntimeError, match="disabled"):
                await agent._async_execute_archive_tool("get", {"session_id": "id"})
        assert _ACTIVE_OWNER_SCOPE_ID.get() is None


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_temporary_owner_resets_after_failure(runtime_agent, failure):
    async def fail(*_args):
        assert _ACTIVE_OWNER_SCOPE_ID.get() == "user:alice"
        raise failure()

    runtime_agent._temporary_memory = SimpleNamespace(async_delete=fail)
    with pytest.raises(failure):
        await runtime_agent._async_execute_temporary_memory_tool(
            "delete", {"memory_ids": ["id"]}
        )
    assert _ACTIVE_OWNER_SCOPE_ID.get() is None


async def test_disabled_temporary_memory_cancels_and_drains_prefetch(runtime_agent):
    started = asyncio.Event()
    finished = asyncio.Event()

    async def load():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    task = asyncio.create_task(load())
    _TEMPORARY_MEMORY_PREFETCH.set(task)
    await started.wait()
    runtime_agent.subentry.data["temporary_memory"] = "off"
    assert await runtime_agent._async_retrieve_temporary_memories() == []
    assert finished.is_set() and task.cancelled()
    assert _TEMPORARY_MEMORY_PREFETCH.get() is None


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("recovery", [False, True])
async def test_validation_once_and_results_in_provider_order(
    hass, monkeypatch, parallel, recovery
):
    agent = object.__new__(base.ExtendedOpenAIBaseLLMEntity)
    agent.hass = hass
    agent.entity_id = "conversation.runtime"
    tools = [
        {
            "spec": {
                "name": str(index),
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
            },
            "function": {
                "type": "native",
                "name": "get_history" if parallel else "execute_service_single",
            },
        }
        for index in range(2)
    ]
    calls = [
        llm.ToolInput(
            id=str(index),
            tool_name=str(index),
            tool_args={"value": index},
            external=True,
        )
        for index in range(2)
    ]
    validate = AsyncMock(wraps=function_execution.async_validate_function_arguments)
    monkeypatch.setattr(
        function_execution, "async_validate_function_arguments", validate
    )
    monkeypatch.setattr(tool_exchange, "async_validate_function_arguments", validate)
    executed = []

    async def execute(_hass, _config, arguments, *_args):
        if arguments["value"] == 0:
            await asyncio.sleep(0)
        executed.append(arguments["value"])
        return arguments

    function = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    monkeypatch.setattr(base, "get_function", lambda _kind: function)
    log = conversation.ChatLog(hass, "runtime")
    log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(agent_id=agent.entity_id, tool_calls=calls)
    )
    await tool_exchange.async_execute_tool_exchange(
        agent,
        log,
        calls,
        tools,
        FunctionCallBudget(2),
        None,
        [],
        recovery_state=ToolRecoveryState(enabled=recovery),
    )
    assert validate.await_count == function.execute.await_count == 2
    results = [item for item in log.content if is_tool_result_content(item)]
    assert [item.tool_call_id for item in results] == ["0", "1"]
    assert [tool_result_data(item)["result"] for item in results] == [
        {"value": 0},
        {"value": 1},
    ]
    assert executed == ([1, 0] if parallel else [0, 1])
    assert function_execution._VALIDATED_CALL.get() is None


async def test_prepared_validation_is_specific_to_call_schema_and_arguments(
    hass, monkeypatch
):
    spec = {"parameters": {"type": "object"}}
    call = llm.ToolInput(id="id", tool_name="test", tool_args={})
    validate = AsyncMock(return_value={})
    monkeypatch.setattr(
        function_execution, "async_validate_function_arguments", validate
    )
    with function_execution.validated_function_call(call, spec):
        await function_execution.async_execution_arguments(hass, spec, call)
        validate.assert_not_awaited()
        await function_execution.async_execution_arguments(hass, spec, deepcopy(call))
        await function_execution.async_execution_arguments(
            hass, {"name": "changed", **spec}, call
        )
        call.tool_args["new"] = True
        await function_execution.async_execution_arguments(hass, spec, call)
    assert validate.await_count == 3
    assert function_execution._VALIDATED_CALL.get() is None


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_owned_executor_failure_closes_every_retained_call(
    hass, monkeypatch, parallel, failure
):
    agent = object.__new__(base.ExtendedOpenAIBaseLLMEntity)
    agent.hass = hass
    agent.entity_id = "conversation.failure"
    tools = [
        {
            "spec": {"name": str(index), "parameters": {"type": "object"}},
            "function": {
                "type": "native",
                "name": "get_history" if parallel else "execute_service_single",
            },
        }
        for index in range(2)
    ]
    calls = [
        llm.ToolInput(
            id=str(index),
            tool_name=str(index),
            tool_args={"value": index},
            external=True,
        )
        for index in range(2)
    ]
    completed = []

    async def execute(_hass, _config, arguments, *_args):
        try:
            if arguments["value"] == 0:
                raise failure("failed execution")
            return "second result"
        finally:
            completed.append(arguments["value"])

    monkeypatch.setattr(
        base, "get_function", lambda _kind: SimpleNamespace(execute=execute)
    )
    log = conversation.ChatLog(hass, "runtime-failure")
    log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(agent_id=agent.entity_id, tool_calls=calls)
    )
    with pytest.raises(failure):
        await tool_exchange.async_execute_tool_exchange(
            agent,
            log,
            calls,
            tools,
            FunctionCallBudget(2),
            None,
            [],
            recovery_state=ToolRecoveryState(enabled=True),
        )
    results = [item for item in log.content if is_tool_result_content(item)]
    assert [item.tool_call_id for item in results] == ["0", "1"]
    assert len(completed) == (2 if parallel else 1)
    assert function_execution._VALIDATED_CALL.get() is None


async def test_prefetch_uses_resolved_owner_and_records_each_retrieval_once(
    runtime_agent,
):
    agent = runtime_agent
    started = asyncio.Event()

    async def active(_scope):
        assert _ACTIVE_OWNER_SCOPE_ID.get() == "user:alice"
        started.set()
        return []

    async def select(*_args):
        await started.wait()
        assert _ACTIVE_OWNER_SCOPE_ID.get() is None
        return []

    agent._temporary_memory = SimpleNamespace(
        async_active=AsyncMock(side_effect=active)
    )
    agent._async_select_memories = select
    trace = SimpleNamespace(phases_ms={}, memory={})
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        assert await agent._async_retrieve_memories(None, "query") == []
        assert await agent._async_retrieve_temporary_memories() == []
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)
    agent._temporary_memory.async_active.assert_awaited_once_with("session")
    assert trace.memory["persistent_count"] == trace.memory["temporary_count"] == 0
    assert set(trace.phases_ms) == {
        "persistent_memory_retrieval",
        "temporary_memory_retrieval",
    }
    assert _ACTIVE_OWNER_SCOPE_ID.get() is None
    assert _TEMPORARY_MEMORY_PREFETCH.get() is None


async def test_request_cancellation_survives_disabled_prefetch_cleanup(runtime_agent):
    started = asyncio.Event()
    cleaning = asyncio.Event()
    finished = asyncio.Event()

    async def load():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

    prefetch = asyncio.create_task(load())
    _TEMPORARY_MEMORY_PREFETCH.set(prefetch)
    await started.wait()
    runtime_agent.subentry.data["temporary_memory"] = "off"
    request = asyncio.create_task(runtime_agent._async_retrieve_temporary_memories())
    await cleaning.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert finished.is_set() and prefetch.cancelled()


async def test_prefetch_is_drained_when_live_policy_check_fails(runtime_agent):
    task = asyncio.create_task(asyncio.sleep(60))
    _TEMPORARY_MEMORY_PREFETCH.set(task)
    runtime_agent._effective_guest_policy = Mock(
        side_effect=RuntimeError("policy failed")
    )
    with pytest.raises(RuntimeError, match="policy failed"):
        await runtime_agent._async_retrieve_temporary_memories()
    assert task.cancelled()
    assert _TEMPORARY_MEMORY_PREFETCH.get() is None


async def test_failed_completed_prefetch_is_drained_on_policy_failure(
    runtime_agent, caplog
):
    async def fail():
        raise RuntimeError("prefetch policy failed")

    task = asyncio.create_task(fail())
    _TEMPORARY_MEMORY_PREFETCH.set(task)
    await asyncio.sleep(0)
    assert task.done()
    runtime_agent._effective_guest_policy = Mock(
        side_effect=RuntimeError("policy failed")
    )
    with pytest.raises(RuntimeError, match="policy failed"):
        await runtime_agent._async_retrieve_temporary_memories()
    assert _TEMPORARY_MEMORY_PREFETCH.get() is None
    del task
    assert "Task exception was never retrieved" not in caplog.text
