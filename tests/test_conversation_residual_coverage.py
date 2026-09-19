"""Residual branch coverage for the conversation agent boundary."""

from __future__ import annotations

from inspect import unwrap
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
    lifecycle_optimizations as lifecycle,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_GUEST_MODE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SHARED_MEMORY_MODE,
    MEMORY_MODE_AUTOMATIC,
    SHARED_MEMORY_DISABLED,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    FunctionLoadFailed,
    FunctionNotFound,
    InvalidFunction,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupSession,
    FunctionToolAssembly,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
    shared_scope,
    unretained_scope,
    user_scope,
)

Agent = conversation_module.ExtendedOpenAIAgentEntity


def _agent(*, data: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        subentry=SimpleNamespace(data=data or {}),
        _memory=None,
        _temporary_memory=None,
        _archive=None,
        _guest_mode=None,
        _knowledge=None,
        _effective_guest_policy=lambda: GuestCapabilityPolicy.unrestricted(),
    )


async def _retrieve_temporary_direct(agent: SimpleNamespace):
    """Exercise the conversation retrieval path without suite-installed wrappers."""
    prefetch_token = lifecycle._TEMPORARY_MEMORY_PREFETCH.set(None)
    scope_token = conversation_module._ACTIVE_SCOPE.set(
        user_scope("test", source="coverage")
    )
    try:
        return await unwrap(Agent._async_retrieve_temporary_memories)(agent)
    finally:
        conversation_module._ACTIVE_SCOPE.reset(scope_token)
        lifecycle._TEMPORARY_MEMORY_PREFETCH.reset(prefetch_token)


@pytest.mark.asyncio
async def test_startup_resolves_an_absolute_skills_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entry = SimpleNamespace(entry_id="entry")
    subentry = SimpleNamespace(
        subentry_id="subentry",
        title="Test agent",
        data={
            conversation_module.CONF_TEMPORARY_MEMORY: conversation_module.TEMPORARY_MEMORY_OFF
        },
    )
    entity = Agent(entry, subentry)
    entity.hass = SimpleNamespace(data={}, config=SimpleNamespace(config_dir="unused"))
    monkeypatch.setattr(
        conversation_module.ConversationEntity, "async_added_to_hass", AsyncMock()
    )
    monkeypatch.setattr(conversation_module.conversation, "async_set_agent", Mock())
    monkeypatch.setattr(conversation_module, "async_track_time_interval", Mock(return_value=lambda: None))
    absolute_dir = tmp_path / "absolute-agent-data"
    monkeypatch.setattr(
        conversation_module, "DEFAULT_WORKING_DIRECTORY", str(absolute_dir)
    )
    get_skills = AsyncMock(return_value=object())
    monkeypatch.setattr(
        conversation_module.SkillManager, "async_get_instance", get_skills
    )
    usage = SimpleNamespace(
        request_retention_days=None,
        run_retention_days=None,
        async_prune_details=AsyncMock(),
    )
    monkeypatch.setattr(
        conversation_module, "async_get_usage", AsyncMock(return_value=usage)
    )
    monkeypatch.setattr(
        conversation_module, "async_get_guest_mode", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        conversation_module, "async_get_continuity", lambda *_args: object()
    )
    monkeypatch.setattr(
        conversation_module, "reset_function_group_runtime", lambda *_args: object()
    )
    monkeypatch.setattr(
        conversation_module, "async_get_request_rules", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        conversation_module, "get_request_rule_runtime", lambda *_args: object()
    )
    monkeypatch.setattr(conversation_module, "memory_enabled", lambda _data: False)
    monkeypatch.setattr(
        conversation_module, "async_get_knowledge", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(Agent, "_async_initialize_archive", AsyncMock())

    await entity.async_added_to_hass()

    get_skills.assert_awaited_once_with(
        entity.hass, user_skills_dir=str(absolute_dir / "skills")
    )


@pytest.mark.asyncio
async def test_retrieval_short_circuits_and_isolates_temporary_store_failures() -> None:
    agent = _agent()
    agent._current_readable_memory_scope_ids = lambda _context: ["user:one"]
    assert await Agent._async_retrieve_memories(agent, object(), "query") == []

    agent._memory = object()
    agent._current_readable_memory_scope_ids = lambda _context: []
    assert await Agent._async_retrieve_memories(agent, object(), "query") == []
    assert await Agent._async_search_memories(agent, [], "query", 3) == []

    agent._current_readable_memory_scope_ids = lambda _context: ["user:one"]
    agent.subentry.data[conversation_module.CONF_MEMORY_AUTO_RETRIEVE_LIMIT] = 2
    agent._continuity = SimpleNamespace(
        async_get_memory_bundle=AsyncMock(side_effect=RuntimeError("offline"))
    )
    token = conversation_module._ACTIVE_MEMORY_SESSION.set(("session", 30))
    try:
        assert await Agent._async_retrieve_memories(agent, object(), "query") == []
    finally:
        conversation_module._ACTIVE_MEMORY_SESSION.reset(token)

    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(
        True, temporary_memory=False
    )
    assert await _retrieve_temporary_direct(agent) == []

    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    assert await _retrieve_temporary_direct(agent) == []

    agent._temporary_memory = SimpleNamespace(
        async_active=AsyncMock(side_effect=RuntimeError("offline"))
    )
    token = conversation_module._ACTIVE_TEMPORARY_SCOPE.set("request:one")
    try:
        assert await _retrieve_temporary_direct(agent) == []
    finally:
        conversation_module._ACTIVE_TEMPORARY_SCOPE.reset(token)


@pytest.mark.asyncio
async def test_embedding_and_temporary_retrieval_success_paths() -> None:
    embeddings = SimpleNamespace(
        create=AsyncMock(
            return_value=SimpleNamespace(
                data=[
                    SimpleNamespace(embedding=(0.1, 0.2)),
                    SimpleNamespace(embedding=[0.3]),
                ]
            )
        )
    )
    agent = _agent(data={conversation_module.CONF_MEMORY_EMBEDDING_MODEL: "embed-v1"})
    agent._client = SimpleNamespace(embeddings=embeddings)

    assert await Agent._async_create_embeddings(agent, ["one", "two"]) == [
        [0.1, 0.2],
        [0.3],
    ]
    embeddings.create.assert_awaited_once_with(model="embed-v1", input=["one", "two"])

    records = [object()]
    agent._temporary_memory = SimpleNamespace(
        async_active=AsyncMock(return_value=records)
    )
    token = conversation_module._ACTIVE_TEMPORARY_SCOPE.set("request:one")
    try:
        assert await _retrieve_temporary_direct(agent) is records
    finally:
        conversation_module._ACTIVE_TEMPORARY_SCOPE.reset(token)


def test_simple_properties_skill_and_entity_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent(data={conversation_module.CONF_SKILLS: ["weather"]})
    agent.skill_manager = SimpleNamespace(
        get_all_skills=lambda: [
            SimpleNamespace(name="weather"),
            SimpleNamespace(name="music"),
        ]
    )
    agent.skills = ["weather"]

    assert Agent.supported_languages.fget(agent) == conversation_module.MATCH_ALL
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(True, skills=False)
    assert Agent._get_enabled_skills(agent) == []
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    assert [skill.name for skill in Agent._get_enabled_skills(agent)] == ["weather"]

    entities = [
        {"entity_id": "sensor.allowed"},
        {"entity_id": "sensor.denied"},
        {"entity_id": 3},
    ]
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(
        True, readable_entity_ids=frozenset({"sensor.allowed"})
    )
    assert Agent._filter_guest_entities(agent, entities) == [entities[0]]
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    assert Agent._filter_guest_entities(agent, entities) is entities
    agent.hass = object()
    agent._filter_guest_entities = Mock(return_value=[entities[0]])
    monkeypatch.setattr(
        conversation_module, "get_exposed_entities", lambda _hass: entities
    )
    assert Agent._get_exposed_entities(agent) == [entities[0]]
    agent._filter_guest_entities.assert_called_once_with(entities)

    assert Agent._guest_arguments_allowed(
        "ordinary scalar", GuestCapabilityPolicy.unrestricted(), control=False
    )


@pytest.mark.asyncio
async def test_archive_opt_out_and_assistant_text_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = SimpleNamespace(async_record_turn=AsyncMock())
    agent = _agent()
    agent._archive = archive
    request_policy = GuestCapabilityPolicy(False, archive_retention=False)
    assert (
        await Agent._async_begin_archive_session(
            agent, "session", user_scope("one", source="test"), None, request_policy
        )
        is None
    )

    class Assistant:
        def __init__(self, content: str) -> None:
            self.content = content

    monkeypatch.setattr(conversation_module.conversation, "AssistantContent", Assistant)
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    await Agent._async_archive_turn(
        agent,
        SimpleNamespace(session_id="archive-1"),
        "run-1",
        SimpleNamespace(text="hello"),
        SimpleNamespace(content=[Assistant("world")]),
        successful=True,
    )
    archive.async_record_turn.assert_awaited_once_with(
        "archive-1",
        run_id="run-1",
        user_text="hello",
        assistant_text="world",
        successful=True,
    )


def test_function_tool_assembly_records_runtime_and_maps_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = [{"spec": {"name": "demo"}, "function": {"type": "script"}}]
    assembly = FunctionToolAssembly(configured, 1, 1, 0, [], 20)
    runtime = SimpleNamespace(record_request=Mock())
    agent = _agent()
    agent._function_groups_runtime = runtime
    agent._temporary_memory = None
    agent._archive = None
    agent._get_configured_function_tools = lambda: configured
    agent._filter_guest_tools_and_groups = lambda tools, groups, policy: (tools, groups)
    agent._current_memory_scope_id = lambda: None
    agent._knowledge_available = False
    monkeypatch.setattr(
        conversation_module, "validate_function_groups", lambda *_args: []
    )
    monkeypatch.setattr(
        conversation_module, "assemble_function_tools", lambda *_args: assembly
    )
    monkeypatch.setattr(
        conversation_module,
        "assemble_integration_function_tools",
        lambda *_args, **_kwargs: [],
    )

    assert Agent._get_function_tools(agent) == configured
    runtime.record_request.assert_called_once_with(assembly)

    monkeypatch.setattr(
        conversation_module,
        "validate_function_groups",
        Mock(side_effect=InvalidFunction("demo")),
    )
    with pytest.raises(InvalidFunction):
        Agent._get_function_tools(agent)

    monkeypatch.setattr(
        conversation_module,
        "validate_function_groups",
        Mock(side_effect=TypeError("broken")),
    )
    with pytest.raises(FunctionLoadFailed):
        Agent._get_function_tools(agent)


def test_function_group_loader_requires_session_and_routes_active_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _agent()
    assert Agent._load_function_groups(agent, ["group-1"])["status"] == "error"

    configured = [{"spec": {"name": "demo"}}]
    groups = [{"id": "group-1", "functions": ["demo"]}]
    expected = {"status": "loaded"}
    agent._get_configured_function_tools = lambda: configured
    agent._filter_guest_tools_and_groups = lambda tools, value, policy: (tools, value)
    monkeypatch.setattr(
        conversation_module, "validate_function_groups", lambda *_args: groups
    )
    loader = Mock(return_value=expected)
    monkeypatch.setattr(conversation_module, "load_function_groups", loader)
    session = FunctionGroupSession("session", 1.0)
    token = conversation_module._ACTIVE_FUNCTION_GROUP_SESSION.set(session)
    try:
        assert Agent._load_function_groups(agent, ["group-1"]) is expected
    finally:
        conversation_module._ACTIVE_FUNCTION_GROUP_SESSION.reset(token)
    loader.assert_called_once_with(session, ["group-1"], groups, configured)


@pytest.mark.asyncio
async def test_guest_mode_tool_and_permission_matrix() -> None:
    agent = _agent()
    with pytest.raises(RuntimeError, match="Guest Mode is unavailable"):
        await Agent._async_execute_guest_mode_tool(agent, {})

    restrict = AsyncMock(return_value={"status": "restricted"})
    agent._guest_mode = SimpleNamespace(async_restrict=restrict)
    agent.subentry.data[CONF_GUEST_MODE_ENABLED] = True
    result = await Agent._async_execute_guest_mode_tool(
        agent,
        {"active_from": "08:00", "active_until": "18:00", "make_indefinite": True},
    )
    assert result == {"status": "restricted"}
    restrict.assert_awaited_once_with(
        active_from="08:00", active_until="18:00", make_indefinite=True
    )

    policy = GuestCapabilityPolicy(
        True,
        shared_memory_read=True,
        shared_memory_write=False,
        knowledge_access=True,
        archive_access=False,
        temporary_memory=True,
    )
    agent._effective_guest_policy = lambda: policy
    assert Agent._guest_integration_allowed(
        agent, "conversation_lifecycle", "start_fresh"
    )
    assert not Agent._guest_integration_allowed(
        agent, "conversation_lifecycle", "other"
    )
    assert Agent._guest_integration_allowed(agent, "memory", "search")
    assert not Agent._guest_integration_allowed(agent, "memory", "add")
    assert Agent._guest_integration_allowed(agent, "knowledge", "list")
    assert not Agent._guest_integration_allowed(agent, "archive", "search")
    assert Agent._guest_integration_allowed(agent, "temporary_memory", "add")
    assert not Agent._guest_integration_allowed(agent, "unknown", "run")


@pytest.mark.asyncio
async def test_dispatch_converts_guest_mode_errors_and_denials_to_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_input = SimpleNamespace(id="call-1", tool_name="tool", tool_args={})
    agent = _agent()
    agent.entity_id = "agent-1"
    agent._tool_result = lambda _call, result: result
    agent._async_execute_guest_mode_tool = AsyncMock(
        side_effect=ValueError("bad window")
    )
    assert await Agent._execute_function_tool(
        agent, {"function": {"type": "guest_mode"}}, tool_input, None, []
    ) == {"status": "error", "error": "bad window"}

    policy = GuestCapabilityPolicy(True)
    agent._effective_guest_policy = lambda: policy
    monkeypatch.setattr(
        conversation_module,
        "latest_function_tool_for_execution",
        Mock(side_effect=FunctionNotFound("gone")),
    )
    denied = await Agent._execute_function_tool(
        agent,
        {"spec": {"name": "gone"}, "function": {"type": "script"}},
        tool_input,
        None,
        [],
    )
    assert denied["status"] == "denied"

    agent.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda _entry_id: SimpleNamespace(
                subentries={"subentry": SimpleNamespace(data={})}
            )
        )
    )
    agent.entry = SimpleNamespace(entry_id="entry")
    agent.subentry = SimpleNamespace(subentry_id="subentry", data={})
    agent._configured_function_tools_from_data = lambda _data: [
        {"spec": {"name": "filtered"}, "function": {"type": "script"}}
    ]
    agent._filter_guest_tools_and_groups = lambda *_args: ([], [])
    agent._is_guest_unscopable_tool = lambda _tool: False
    monkeypatch.setattr(
        conversation_module,
        "latest_function_tool_for_execution",
        lambda _agent, tool: tool,
    )
    monkeypatch.setattr(
        conversation_module, "validate_function_groups", lambda *_args: []
    )
    denied = await Agent._execute_function_tool(
        agent,
        {"spec": {"name": "filtered"}, "function": {"type": "script"}},
        tool_input,
        None,
        [],
    )
    assert denied["status"] == "denied"

    agent._guest_integration_allowed = lambda *_args: False
    result = await Agent._execute_function_tool(
        agent,
        {"function": {"type": "memory", "operation": "search"}},
        tool_input,
        None,
        [],
    )
    decoded = json.loads(tool_result_data(result)["result"])
    assert decoded == {
        "status": "error",
        "error": conversation_module.GUEST_MODE_UNAVAILABLE,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "arguments", "message"),
    [
        ("search", {"query": 3}, "query, source_ids, or limit"),
        ("list", {"limit": True}, "query, limit, or offset"),
        ("get", {"source_id": 3}, "source_id, start_character, or max_characters"),
        ("unknown", {}, "unknown knowledge operation"),
    ],
)
async def test_knowledge_tool_validation(
    operation: str, arguments: dict, message: str
) -> None:
    agent = _agent()
    agent._knowledge_available = True
    agent._knowledge = SimpleNamespace()
    with pytest.raises(ValueError, match=message):
        await Agent._async_execute_knowledge_tool(agent, operation, arguments)


@pytest.mark.asyncio
async def test_knowledge_tool_requires_available_library() -> None:
    agent = _agent()
    agent._knowledge_available = False
    with pytest.raises(RuntimeError, match="Knowledge Library is unavailable"):
        await Agent._async_execute_knowledge_tool(agent, "list", {})


@pytest.mark.asyncio
async def test_memory_tool_validation_and_shared_automatic_write_guard() -> None:
    agent = _agent(
        data={
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT,
        }
    )
    with pytest.raises(RuntimeError, match="persistent memory is unavailable"):
        await Agent._async_execute_memory_tool(agent, "list", {}, None)

    agent._memory = SimpleNamespace()
    agent._current_readable_memory_scope_ids = lambda _context: []
    with pytest.raises(RuntimeError, match="disabled for this data scope"):
        await Agent._async_execute_memory_tool(agent, "list", {}, None)

    agent._current_readable_memory_scope_ids = lambda _context: [
        SHARED_HOUSEHOLD_SCOPE_ID
    ]
    invalid_cases = [
        ("add", {"content": 3}, "content, category, and source"),
        ("list", {"offset": True}, "category, limit, or offset"),
        ("update", {"memory_id": "one", "clear_fields": [3]}, "clear_fields"),
        ("update", {"memory_id": "one"}, "at least one valid update"),
        ("unknown", {}, "unknown memory operation"),
    ]
    for operation, arguments, message in invalid_cases:
        with pytest.raises(ValueError, match=message):
            await Agent._async_execute_memory_tool(agent, operation, arguments, None)

    agent._current_write_memory_scope_id = Mock()
    token = conversation_module._ACTIVE_SCOPE.set(shared_scope(source="test"))
    try:
        with pytest.raises(
            ValueError, match="automatic shared memory creation is disabled"
        ):
            await Agent._async_execute_memory_tool(
                agent,
                "add",
                {"content": "fact", "category": "general", "source": "implicit"},
                None,
            )
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)
    agent._current_write_memory_scope_id.assert_not_called()


def test_memory_scope_resolution_covers_guest_shared_and_unretained_paths() -> None:
    agent = _agent(data={CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_DISABLED})
    shared = shared_scope(source="test")
    token = conversation_module._ACTIVE_SCOPE.set(shared)
    try:
        assert Agent._current_memory_scope_id(agent) is None
        assert Agent._personal_memory_scope_id(agent) is None
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)

    personal = user_scope("one", source="test")
    token = conversation_module._ACTIVE_SCOPE.set(personal)
    try:
        assert Agent._personal_memory_scope_id(agent) == "one"
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)

    agent.subentry.data[CONF_SHARED_MEMORY_MODE] = SHARED_MEMORY_EXPLICIT
    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(
        True, shared_memory_read=True, shared_memory_write=True
    )
    assert Agent._current_readable_memory_scope_ids(agent) == [
        SHARED_HOUSEHOLD_SCOPE_ID
    ]

    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    token = conversation_module._ACTIVE_SCOPE.set(user_scope("one", source="test"))
    try:
        assert Agent._current_memory_scope_id(agent) == "one"
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)

    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(
        True, shared_memory_read=True, shared_memory_write=True
    )
    token = conversation_module._ACTIVE_SCOPE.set(shared)
    try:
        assert (
            Agent._current_write_memory_scope_id(agent, None, None, source="explicit")
            == SHARED_HOUSEHOLD_SCOPE_ID
        )
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)

    agent._effective_guest_policy = lambda: GuestCapabilityPolicy(
        True, shared_memory_write=False
    )
    with pytest.raises(RuntimeError, match=conversation_module.GUEST_MODE_UNAVAILABLE):
        Agent._current_write_memory_scope_id(agent, None, None, source="explicit")

    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    with pytest.raises(ValueError, match="scope must be personal or household"):
        Agent._current_write_memory_scope_id(agent, "other", None, source="explicit")
    with pytest.raises(ValueError, match="household scope requires"):
        Agent._current_write_memory_scope_id(
            agent, "household", None, source="explicit"
        )

    token = conversation_module._ACTIVE_SCOPE.set(unretained_scope())
    try:
        with pytest.raises(RuntimeError, match="disabled for this data scope"):
            Agent._current_write_memory_scope_id(agent, None, None, source="explicit")
    finally:
        conversation_module._ACTIVE_SCOPE.reset(token)


@pytest.mark.asyncio
async def test_temporary_and_archive_argument_validation() -> None:
    agent = _agent(data={CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False})
    agent._temporary_memory = SimpleNamespace()
    execute_temporary = unwrap(Agent._async_execute_temporary_memory_tool)
    with pytest.raises(RuntimeError, match="unavailable for this request"):
        await execute_temporary(agent, "update", {})
    token = conversation_module._ACTIVE_TEMPORARY_SCOPE.set("request:one")
    try:
        with pytest.raises(ValueError, match="memory_id is required"):
            await execute_temporary(agent, "update", {})
    finally:
        conversation_module._ACTIVE_TEMPORARY_SCOPE.reset(token)

    agent._archive = SimpleNamespace()
    execute_archive = unwrap(Agent._async_execute_archive_tool)
    scope_token = conversation_module._ACTIVE_SCOPE.set(
        user_scope("one", source="test")
    )
    archive_token = conversation_module._ACTIVE_ARCHIVE.set(("key", "session"))
    try:
        with pytest.raises(RuntimeError, match="model archive search is disabled"):
            await execute_archive(agent, "search", {"query": "hello"})

        agent.subentry.data[CONF_ARCHIVE_MODEL_SEARCH_ENABLED] = True
        with pytest.raises(ValueError, match="query is required"):
            await execute_archive(agent, "search", {})
        with pytest.raises(ValueError, match="session_id is required"):
            await execute_archive(agent, "get", {})
    finally:
        conversation_module._ACTIVE_ARCHIVE.reset(archive_token)
        conversation_module._ACTIVE_SCOPE.reset(scope_token)


@pytest.mark.asyncio
async def test_trusted_function_resolution_and_execution_failures_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity = object.__new__(Agent)
    entity._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    tool = {"spec": {"name": "demo"}, "function": {"type": "script"}}
    call = SimpleNamespace(id="call", tool_name="demo", tool_args={})

    monkeypatch.setattr(
        conversation_module,
        "latest_function_tool_for_execution",
        Mock(side_effect=FunctionNotFound("demo")),
    )
    with pytest.raises(FunctionNotFound):
        await entity._execute_function_tool(tool, call, None, [])

    monkeypatch.setattr(
        conversation_module,
        "latest_function_tool_for_execution",
        lambda _agent, value: value,
    )
    monkeypatch.setattr(
        conversation_module.ExtendedOpenAIBaseLLMEntity,
        "_execute_function_tool",
        AsyncMock(side_effect=RuntimeError("execution failed")),
    )
    with pytest.raises(RuntimeError, match="execution failed"):
        await entity._execute_function_tool(tool, call, None, [])
