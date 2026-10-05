"""Focused branch coverage for storage, schema, and debug lifecycle edges."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import debug
from custom_components.extended_openai_conversation_responses.entity import (
    _adjust_schema,
    _make_schema_nullable,
    _normalize_function_result,
    _normalize_url_citation,
    _schema_explicitly_allows_null,
)
from custom_components.extended_openai_conversation_responses.strict_store import (
    RecoveryGuardedStore,
    _async_settle_store_io,
    async_storage_lock,
)


@pytest.mark.asyncio
async def test_settle_store_io_propagates_native_task_cancellation() -> None:
    async def cancelled_operation() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _async_settle_store_io(cancelled_operation())


@pytest.mark.asyncio
async def test_settle_store_io_finishes_native_io_before_caller_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def operation() -> str:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True
        return "saved"

    task = asyncio.create_task(_async_settle_store_io(operation()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished


@pytest.mark.asyncio
async def test_settle_store_io_preserves_native_exception_after_shield() -> None:
    async def operation() -> None:
        raise OSError("disk unavailable")

    with pytest.raises(OSError, match="disk unavailable"):
        await _async_settle_store_io(operation())


def test_recovery_guarded_store_availability_delegates_only_when_bound() -> None:
    store = RecoveryGuardedStore.__new__(RecoveryGuardedStore)
    store._recovery_gate = None
    store.require_available()
    assert store.recovery_pending is False

    gate = SimpleNamespace(recovery_required=True, require_available=Mock())
    store._recovery_gate = gate
    assert store.recovery_pending is True
    store.require_available()
    gate.require_available.assert_called_once_with()


@pytest.mark.asyncio
async def test_storage_lock_uses_recovery_gate_when_store_is_bound() -> None:
    entered: list[str] = []

    class Gate:
        recovery_required = False

        @asynccontextmanager
        async def shared(self, *, maintenance: bool):
            assert maintenance is True
            entered.append("gate")
            try:
                yield
            finally:
                entered.append("gate-exit")

    store = RecoveryGuardedStore.__new__(RecoveryGuardedStore)
    store._recovery_gate = Gate()
    lock = asyncio.Lock()

    async with async_storage_lock(store, lock):
        assert lock.locked()
        entered.append("body")

    assert entered == ["gate", "body", "gate-exit"]
    assert not lock.locked()


@pytest.mark.asyncio
async def test_storage_lock_falls_back_for_non_guarded_storage() -> None:
    lock = asyncio.Lock()
    async with async_storage_lock(SimpleNamespace(_store=object()), lock):
        assert lock.locked()
    assert not lock.locked()


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"nullable": True}, True),
        ({"type": "null"}, True),
        ({"type": ["string", "null"]}, True),
        ({"enum": ["a", None]}, True),
        ({"const": None}, True),
        ({"type": "string"}, False),
        ({"enum": ["a"]}, False),
        ({"const": "a"}, False),
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, True),
        ({"anyOf": [{"type": "string"}, {"type": "number"}]}, False),
        ({"oneOf": [{"type": "string"}, {"type": "null"}]}, True),
        ({"oneOf": [{"type": "null"}, {"const": None}]}, False),
        ({"allOf": [{"type": "null"}, {"const": None}]}, True),
        ({"allOf": [{"type": "null"}, {"type": "string"}]}, False),
        ({"allOf": []}, False),
    ],
)
def test_schema_nullability_matrix(schema: dict, expected: bool) -> None:
    assert _schema_explicitly_allows_null(schema) is expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, {"type": ["string", "null"]}),
        ({"type": ["string", "number"]}, {"type": ["string", "number", "null"]}),
        (
            {"enum": ["a", "b"]},
            {"anyOf": [{"enum": ["a", "b"]}, {"type": "null"}]},
        ),
        (
            {"const": "a"},
            {"anyOf": [{"const": "a"}, {"type": "null"}]},
        ),
        (
            {"anyOf": [{"type": "string"}]},
            {
                "anyOf": [
                    {"anyOf": [{"type": "string"}]},
                    {"type": "null"},
                ]
            },
        ),
    ],
)
def test_make_schema_nullable_variants(schema: dict, expected: dict) -> None:
    _make_schema_nullable(schema)
    assert schema == expected


def test_make_schema_nullable_leaves_already_nullable_schema_unchanged() -> None:
    schema = {"type": ["string", "null"]}
    _make_schema_nullable(schema)
    assert schema == {"type": ["string", "null"]}


def test_adjust_schema_recurses_objects_arrays_and_compositions() -> None:
    schema = {
        "type": "object",
        "properties": {
            "already_required": {"type": "integer"},
            "optional": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nested": {"type": "string"}},
                },
            },
            "choice": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object", "properties": {"flag": {"type": "boolean"}}},
                ]
            },
            "ignored": "not-a-schema",
        },
        "required": ["already_required"],
    }

    _adjust_schema(schema)

    assert schema["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["already_required", "optional", "choice"]
    optional = schema["properties"]["optional"]
    assert optional["type"] == ["array", "null"]
    nested = optional["items"]
    assert nested["strict"] is True
    assert nested["required"] == ["nested"]
    assert nested["properties"]["nested"]["type"] == ["string", "null"]
    choice = schema["properties"]["choice"]
    assert _schema_explicitly_allows_null(choice)
    original_choice = choice["anyOf"][0]
    object_variant = original_choice["anyOf"][1]
    assert object_variant["strict"] is True
    assert object_variant["required"] == ["flag"]


def test_adjust_schema_honours_legacy_nullable_keyword() -> None:
    schema = {"type": "string", "nullable": True}
    _adjust_schema(schema)
    assert schema == {"type": ["string", "null"]}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"ok": True}, {"ok": True}),
        ([1, 2, 3], [1, 2, 3]),
        ({1, 2}, "{1, 2}"),
    ],
)
def test_normalize_function_result_serializable_and_fallback(value, expected) -> None:
    assert _normalize_function_result(value) == expected


def test_normalize_url_citation_accepts_objects_and_rejects_invalid_indexes() -> None:
    citation = SimpleNamespace(
        type="url_citation",
        start_index=2,
        end_index=8,
        title="Docs",
        url="https://example.invalid",
    )
    assert _normalize_url_citation(citation) == {
        "type": "url_citation",
        "start_index": 2,
        "end_index": 8,
        "title": "Docs",
        "url": "https://example.invalid",
    }
    assert _normalize_url_citation({"type": "other"}) is None
    assert (
        _normalize_url_citation(
            {"type": "url_citation", "start_index": "2", "end_index": 8}
        )
        is None
    )


class _Request:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.finished: list[tuple[bool, BaseException | None]] = []

    def add_event(self, event: object) -> None:
        self.events.append(event)

    def finish(
        self, *, successful: bool, error: BaseException | None = None
    ) -> None:
        self.finished.append((successful, error))


class _AsyncIterator:
    def __init__(self, events: list[object], error: BaseException | None = None) -> None:
        self.events = list(events)
        self.error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.events:
            return self.events.pop(0)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_debug_stream_records_events_and_finishes_on_exhaustion() -> None:
    request = _Request()
    stream = debug._DebugAsyncStream(_AsyncIterator(["one", "two"]), request)

    assert [event async for event in stream] == ["one", "two"]
    assert request.events == ["one", "two"]
    assert request.finished == [(True, None)]


@pytest.mark.asyncio
async def test_debug_stream_records_iteration_failure() -> None:
    request = _Request()
    error = RuntimeError("stream failed")
    stream = debug._DebugAsyncStream(_AsyncIterator([], error), request)

    with pytest.raises(RuntimeError, match="stream failed"):
        await stream.__anext__()
    assert request.finished == [(False, error)]


@pytest.mark.asyncio
@pytest.mark.parametrize("close_kind", ["missing", "sync", "async"])
async def test_debug_stream_close_surfaces_all_delegate_close_shapes(close_kind: str) -> None:
    request = _Request()
    delegate = _AsyncIterator([])

    if close_kind == "sync":
        delegate.close = Mock(return_value="closed")
    elif close_kind == "async":
        delegate.close = AsyncMock(return_value="closed")

    stream = debug._DebugAsyncStream(delegate, request)
    result = await stream.close()

    if close_kind == "missing":
        assert result is None
    else:
        assert result == "closed"
    assert request.finished == [(True, None)]


@pytest.mark.asyncio
async def test_debug_stream_context_exit_delegates_and_records_failure() -> None:
    request = _Request()
    delegate = _AsyncIterator([])
    delegate.__aexit__ = AsyncMock()
    stream = debug._DebugAsyncStream(delegate, request)
    error = ValueError("consumer failed")

    await stream.__aexit__(ValueError, error, None)

    delegate.__aexit__.assert_awaited_once_with(ValueError, error, None)
    assert request.finished == [(False, error)]


@pytest.mark.asyncio
async def test_debug_endpoint_without_trace_is_transparent(monkeypatch) -> None:
    delegate = SimpleNamespace(create=AsyncMock(return_value={"ok": True}), marker=42)
    monkeypatch.setattr(debug, "current_debug_trace", lambda: None)
    proxy = debug._DebugEndpointProxy(delegate, "responses")

    assert await proxy.create(model="test") == {"ok": True}
    assert proxy.marker == 42
    delegate.create.assert_awaited_once_with(model="test")



@pytest.mark.asyncio
async def test_diagnostics_survives_provider_usage_and_sdk_metadata_failures(
    hass, monkeypatch
) -> None:
    from importlib.metadata import PackageNotFoundError

    from custom_components.extended_openai_conversation_responses import diagnostics

    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        data={},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={},
        subentries={"agent-1": subentry},
    )

    monkeypatch.setattr(
        diagnostics,
        "build_provider_request_snapshot",
        Mock(side_effect=ValueError("invalid provider configuration")),
    )
    monkeypatch.setattr(diagnostics, "conversation_tools_required", Mock(return_value=False))
    monkeypatch.setattr(
        diagnostics,
        "async_get_continuity",
        Mock(return_value=SimpleNamespace(stats=lambda: {"continuity_sessions": 0})),
    )
    monkeypatch.setattr(diagnostics, "_configured_function_tools", Mock(return_value=[]))
    monkeypatch.setattr(diagnostics, "validate_function_groups", Mock(return_value=[]))
    monkeypatch.setattr(diagnostics, "get_function_group_runtime", Mock(return_value=None))
    guest = SimpleNamespace(status=lambda: {"enabled": False})
    monkeypatch.setattr(diagnostics, "async_get_guest_mode", AsyncMock(return_value=guest))
    monkeypatch.setattr(
        diagnostics,
        "resolve_guest_policy",
        Mock(return_value=SimpleNamespace(as_diagnostics=lambda: {"enabled": False})),
    )

    stats_manager = SimpleNamespace(stats=lambda: {"count": 0})
    monkeypatch.setattr(
        diagnostics, "async_get_temporary_memory", AsyncMock(return_value=stats_manager)
    )
    monkeypatch.setattr(diagnostics, "async_get_memory", AsyncMock(return_value=stats_manager))
    monkeypatch.setattr(
        diagnostics, "async_get_knowledge", AsyncMock(return_value=stats_manager)
    )
    monkeypatch.setattr(
        diagnostics,
        "async_get_archive",
        AsyncMock(return_value=SimpleNamespace(stats=lambda: {"count": 0})),
    )
    monkeypatch.setattr(
        diagnostics,
        "async_get_usage",
        AsyncMock(side_effect=OSError("usage store unavailable")),
    )
    monkeypatch.setattr(
        diagnostics,
        "version",
        Mock(side_effect=PackageNotFoundError("openai")),
    )

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    agent = result["conversation_agents"][0]
    assert agent["provider_configuration_error"] == "ValueError"
    assert agent["usage_storage_error"] == "OSError"
    assert result["openai_sdk_version"] is None
    assert result["provider_category"] == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subsystem_key", "error_field"),
    [
        ("temporary_memory", "temporary_memory_storage_error"),
        ("persistent_memory", "storage_error"),
        ("knowledge", "knowledge_storage_error"),
        ("archive", "archive_storage_error"),
    ],
)
async def test_diagnostics_reports_previously_failed_optional_subsystems_without_loading(
    hass, monkeypatch, subsystem_key: str, error_field: str
) -> None:
    from custom_components.extended_openai_conversation_responses import diagnostics
    from custom_components.extended_openai_conversation_responses.const import (
        SUBSYSTEM_STATUS_KEY,
    )

    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        data={},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={},
        subentries={"agent-1": subentry},
    )
    hass.data[SUBSYSTEM_STATUS_KEY] = {
        ("entry-1", "agent-1"): {subsystem_key: {"status": "failed"}}
    }

    snapshot = SimpleNamespace(
        api_mode="responses",
        api_kwargs={"stream": True},
        structured_outputs=True,
    )
    monkeypatch.setattr(
        diagnostics, "build_provider_request_snapshot", Mock(return_value=snapshot)
    )
    monkeypatch.setattr(diagnostics, "conversation_tools_required", Mock(return_value=False))
    monkeypatch.setattr(
        diagnostics,
        "async_get_continuity",
        Mock(return_value=SimpleNamespace(stats=lambda: {})),
    )
    monkeypatch.setattr(diagnostics, "_configured_function_tools", Mock(return_value=[]))
    monkeypatch.setattr(diagnostics, "validate_function_groups", Mock(return_value=[]))
    monkeypatch.setattr(diagnostics, "get_function_group_runtime", Mock(return_value=None))
    monkeypatch.setattr(
        diagnostics,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {})),
    )
    monkeypatch.setattr(
        diagnostics,
        "resolve_guest_policy",
        Mock(return_value=SimpleNamespace(as_diagnostics=lambda: {})),
    )

    temporary = AsyncMock(return_value=SimpleNamespace(stats=lambda: {}))
    memory = AsyncMock(return_value=SimpleNamespace(stats=lambda: {}))
    knowledge = AsyncMock(return_value=SimpleNamespace(stats=lambda: {}))
    archive = AsyncMock(return_value=SimpleNamespace(stats=lambda: {}))
    usage = AsyncMock(
        return_value=SimpleNamespace(
            as_dict=lambda: {},
            persistence_status=lambda: {},
        )
    )
    monkeypatch.setattr(diagnostics, "async_get_temporary_memory", temporary)
    monkeypatch.setattr(diagnostics, "async_get_memory", memory)
    monkeypatch.setattr(diagnostics, "async_get_knowledge", knowledge)
    monkeypatch.setattr(diagnostics, "async_get_archive", archive)
    monkeypatch.setattr(diagnostics, "async_get_usage", usage)

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    agent = result["conversation_agents"][0]
    assert agent[error_field] == "RuntimeError"
    loader = {
        "temporary_memory": temporary,
        "persistent_memory": memory,
        "knowledge": knowledge,
        "archive": archive,
    }[subsystem_key]
    loader.assert_not_awaited()



def test_effective_skill_loader_status_success_carries_loadable_skills() -> None:
    from custom_components.extended_openai_conversation_responses.skill_availability import (
        CANONICAL_SKILL_LOADER_PATH,
        effective_skill_loader_status,
    )

    tools = [
        {
            "enabled": True,
            "spec": {"name": "load_skill"},
            "function": {
                "type": "read_file",
                "path": CANONICAL_SKILL_LOADER_PATH,
            },
        }
    ]

    status = effective_skill_loader_status(
        ["weather", "weather", "missing"],
        ["weather"],
        tools,
        [],
        max_function_calls=1,
    )

    assert status.available is True
    assert status.loadable_skills == ("weather",)
    assert status.reason is None


def test_effective_skill_loader_status_rejects_unsupported_function_tools() -> None:
    from custom_components.extended_openai_conversation_responses.skill_availability import (
        effective_skill_loader_status,
    )

    status = effective_skill_loader_status(
        ["weather"],
        ["weather"],
        [],
        [],
        function_tools_supported=False,
    )

    assert status.available is False
    assert status.loadable_skills == ("weather",)
    assert status.reason == "Function Tools are unavailable for this runtime"


def test_effective_skill_loader_status_rejects_unavailable_on_demand_group_loader() -> None:
    from custom_components.extended_openai_conversation_responses.const import (
        FUNCTION_GROUP_LOADING_ON_DEMAND,
    )
    from custom_components.extended_openai_conversation_responses.skill_availability import (
        CANONICAL_SKILL_LOADER_PATH,
        effective_skill_loader_status,
    )

    tools = [
        {
            "spec": {"name": "load_skill"},
            "function": {
                "type": "read_file",
                "path": CANONICAL_SKILL_LOADER_PATH,
            },
        }
    ]
    groups = [
        {
            "id": "skills",
            "enabled": True,
            "loading_mode": FUNCTION_GROUP_LOADING_ON_DEMAND,
            "functions": ["load_skill"],
        }
    ]

    status = effective_skill_loader_status(
        ["weather"],
        ["weather"],
        tools,
        groups,
        group_loader_supported=False,
    )

    assert status.available is False
    assert status.group_id == "skills"
    assert status.on_demand is True
    assert status.loadable_skills == ("weather",)


def test_model_tool_result_compaction_returns_original_for_already_compact_json(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_tool_results,
    )

    result = object()
    data = {"result": '{"ok":true}'}
    monkeypatch.setattr(
        model_tool_results, "tool_result_data", Mock(return_value=data)
    )

    assert model_tool_results._compact_json_result_content(result) is result
    assert data["result"] == '{"ok":true}'


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"result": 42},
        {"result": "not-json"},
    ],
)
def test_model_tool_result_compaction_preserves_non_json_shapes(
    monkeypatch, value
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_tool_results,
    )

    result = object()
    monkeypatch.setattr(
        model_tool_results, "tool_result_data", Mock(return_value=value)
    )

    assert model_tool_results._compact_json_result_content(result) is result


def test_model_payload_preserves_malformed_user_owned_tool_entries() -> None:
    from custom_components.extended_openai_conversation_responses.model_payload import (
        prepare_model_function_tools,
    )

    tools = [
        {"function": {"type": "native"}, "spec": None},
        {"function": {"type": "native"}, "spec": {"name": 123}},
    ]

    result = prepare_model_function_tools(tools)

    assert result == tools
    assert result is not tools
    assert result[0] is not tools[0]
    assert result[1] is not tools[1]


def test_model_payload_loader_keeps_non_string_description_and_non_mapping_properties() -> None:
    from custom_components.extended_openai_conversation_responses.model_payload import (
        prepare_model_function_tools,
    )

    tool = {
        "function": {"type": "function_group_loader"},
        "spec": {
            "name": "load_groups",
            "description": None,
            "parameters": {"properties": []},
        },
    }

    assert prepare_model_function_tools([tool]) == [tool]


def test_model_payload_property_compaction_ignores_non_mapping_schema() -> None:
    from custom_components.extended_openai_conversation_responses.model_payload import (
        prepare_model_function_tools,
    )

    tool = {
        "function": {"type": "knowledge"},
        "spec": {
            "name": "knowledge_search",
            "description": "long description",
            "parameters": {
                "properties": {
                    "query": "not-a-schema",
                    "source_ids": {"description": "old"},
                }
            },
        },
    }

    result = prepare_model_function_tools([tool])[0]

    assert result["spec"]["parameters"]["properties"]["query"] == "not-a-schema"
    assert (
        result["spec"]["parameters"]["properties"]["source_ids"]["description"]
        == "Exact IDs returned by Knowledge tools."
    )


def test_template_working_directory_absolute_path_is_not_rebased(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import template

    monkeypatch.setattr(template, "DEFAULT_WORKING_DIRECTORY", "/absolute/eoai")
    manager = template.ExtendedOpenAITemplateManager(hass)

    assert manager._get_working_directory() == "/absolute/eoai"


def test_template_skill_dir_requires_initialized_manager(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import template

    monkeypatch.setattr(template.SkillManager, "_instance", None)
    manager = template.ExtendedOpenAITemplateManager(hass)

    with pytest.raises(ValueError, match="SkillManager not initialized"):
        manager._get_skill_dir("missing")



@pytest.mark.asyncio
async def test_recovery_guarded_store_remove_without_gate_uses_native_store(
    monkeypatch,
) -> None:
    from homeassistant.helpers.storage import Store

    native_remove = AsyncMock()
    monkeypatch.setattr(Store, "async_remove", native_remove)
    store = RecoveryGuardedStore.__new__(RecoveryGuardedStore)
    store._recovery_gate = None

    await store.async_remove()

    native_remove.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_native_edit_settlement_defers_caller_cancellation_until_writer_finishes() -> None:
    from custom_components.extended_openai_conversation_responses.functions.file import (
        _async_settle_native_edit,
    )

    started = asyncio.Event()
    release = asyncio.Event()
    completed = False

    async def operation() -> int:
        nonlocal completed
        started.set()
        await release.wait()
        completed = True
        return 3

    task = asyncio.create_task(_async_settle_native_edit(operation()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed


@pytest.mark.asyncio
async def test_native_edit_settlement_propagates_writer_cancellation() -> None:
    from custom_components.extended_openai_conversation_responses.functions.file import (
        _async_settle_native_edit,
    )

    async def operation() -> int:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _async_settle_native_edit(operation())


@pytest.mark.asyncio
async def test_automation_update_settlement_propagates_native_failure() -> None:
    from custom_components.extended_openai_conversation_responses.functions.native import (
        _async_settle_automation_update,
    )

    async def operation() -> str:
        raise OSError("automation reload failed")

    with pytest.raises(OSError, match="automation reload failed"):
        await _async_settle_automation_update(operation())


@pytest.mark.asyncio
async def test_automation_update_settlement_propagates_native_cancellation() -> None:
    from custom_components.extended_openai_conversation_responses.functions.native import (
        _async_settle_automation_update,
    )

    async def operation() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _async_settle_automation_update(operation())


def test_function_entity_validation_rejects_unavailable_participant(hass) -> None:
    from custom_components.extended_openai_conversation_responses.functions.base import (
        Function,
    )
    from homeassistant.exceptions import HomeAssistantError

    class DummyFunction(Function):
        async def execute(self, *args, **kwargs):
            return None

    hass.states.get = Mock(return_value=SimpleNamespace(state="unavailable"))
    function = DummyFunction()
    exposed = [{"entity_id": "light.kitchen"}]

    with pytest.raises(HomeAssistantError, match="Entity is unavailable"):
        function.validate_entity_ids(hass, ["light.kitchen"], exposed)

    function.validate_entity_ids(
        hass,
        ["light.kitchen"],
        exposed,
        require_available=False,
    )


def test_exposed_attribute_enrichment_tolerates_non_mapping_options(hass) -> None:
    from custom_components.extended_openai_conversation_responses.exposed_attributes import (
        enrich_exposed_entities,
    )

    exposed = [{"entity_id": "sensor.temperature", "state": "20"}]

    assert enrich_exposed_entities(hass, object(), exposed) is exposed


def test_skill_loader_status_returns_structural_failure_from_missing_loader() -> None:
    from custom_components.extended_openai_conversation_responses.skill_availability import (
        effective_skill_loader_status,
    )

    status = effective_skill_loader_status(
        ["weather"],
        ["weather"],
        [],
        [],
        max_function_calls=1,
    )

    assert status.available is False
    assert status.loadable_skills == ()
    assert "load_skill" in str(status.reason)


def test_template_relative_working_directory_is_rebased_to_config(hass, monkeypatch) -> None:
    from pathlib import Path

    from custom_components.extended_openai_conversation_responses import template

    monkeypatch.setattr(template, "DEFAULT_WORKING_DIRECTORY", "eoai-work")
    manager = template.ExtendedOpenAITemplateManager(hass)

    assert manager._get_working_directory() == str(
        Path(hass.config.config_dir) / "eoai-work"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parent_data", "expected"),
    [
        ({"api_provider": "azure"}, "azure"),
        (
            {
                "api_provider": "compatible",
                "base_url": "https://example.invalid/v1",
            },
            "compatible",
        ),
    ],
)
async def test_diagnostics_provider_category_branches(
    hass, monkeypatch, parent_data: dict, expected: str
) -> None:
    from custom_components.extended_openai_conversation_responses import diagnostics

    entry = SimpleNamespace(
        entry_id="entry-1",
        data=parent_data,
        subentries={},
    )
    monkeypatch.setattr(diagnostics, "version", Mock(return_value="test"))

    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)

    assert result["provider_category"] == expected


def test_summary_diagnostics_is_best_effort_when_trace_is_malformed() -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_diagnostics,
    )

    class BrokenTrace:
        @property
        def provider_requests(self):
            raise RuntimeError("broken trace")

    data = {"kept": True}

    assert request_diagnostics.summary_diagnostics(BrokenTrace(), data) is data
    assert data == {"kept": True}


def test_record_tool_assembly_records_function_group_runtime(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        request_diagnostics,
    )

    trace = SimpleNamespace(memory={})
    runtime = SimpleNamespace(stats=Mock(return_value={"loaded_groups": 2}))
    monkeypatch.setattr(request_diagnostics, "_debug_trace", Mock(return_value=trace))
    monkeypatch.setattr(
        request_diagnostics,
        "get_function_group_runtime",
        Mock(return_value=runtime),
    )
    agent = SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )

    request_diagnostics.record_tool_assembly(agent, [{"spec": {}}], 0.0)

    preparation = trace.memory[request_diagnostics._INTERNAL_PREPARATION]
    assert preparation["function_tool_assembly"]["last_count"] == 1
    assert preparation["function_groups"] == {"loaded_groups": 2}


def test_runtime_failure_token_length_can_be_recorded_as_local_reply() -> None:
    from custom_components.extended_openai_conversation_responses.exceptions import (
        TokenLengthExceededError,
    )
    from custom_components.extended_openai_conversation_responses.runtime_failure_hardening import (
        _conversation_error_result,
    )

    logger = Mock()
    usage = SimpleNamespace(mark_current_run_failed=Mock())
    entity = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent", data={}),
        _usage=usage,
        entity_id="conversation.agent",
        _fire_conversation_finished=Mock(),
        hass=SimpleNamespace(),
    )
    user_input = SimpleNamespace(language="en", conversation_id="conversation-1")
    chat_log = SimpleNamespace(content=[])
    error = TokenLengthExceededError(64)

    result = _conversation_error_result(
        entity,
        user_input,
        chat_log,
        error,
        logger=logger,
        handled_locally=True,
    )

    logger.warning.assert_called_once()
    usage.mark_current_run_failed.assert_called_once_with("TokenLengthExceededError")
    assert len(chat_log.content) == 1
    assert chat_log.content[0].agent_id == "conversation.agent"
    assert result.conversation_id == "conversation-1"
    entity._fire_conversation_finished.assert_called_once()



def test_request_rule_groups_reject_duplicate_ids() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    with pytest.raises(ValueError, match="duplicate"):
        request_rules.validate_rule_groups(
            [
                {"id": "same", "name": "First"},
                {"id": "same", "name": "Second"},
            ]
        )


def test_request_rule_result_step_ids_are_assigned_only_at_result_boundaries() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    rule = {
        "action": {
            "actions": [
                "ignored",
                {
                    "type": "function",
                    "name": "demo",
                    "result_alias": "first",
                },
                {
                    "type": "function",
                    "name": "demo",
                    "result_alias": "existing",
                    "step_id": "already-set",
                },
                {
                    "action": (
                        f"{request_rules.DOMAIN}."
                        f"{request_rules.SERVICE_CALL_FUNCTION}"
                    ),
                    "data": {"result_alias": "second"},
                },
                {
                    "action": "light.turn_on",
                    "data": {"result_alias": "not-a-function-result"},
                },
            ]
        }
    }

    result = request_rules._assign_missing_result_step_ids(rule)
    actions = result["action"]["actions"]

    assert "step_id" in actions[1]
    assert actions[2]["step_id"] == "already-set"
    assert "step_id" in actions[3]["data"]
    assert "step_id" not in actions[4]["data"]
    assert "step_id" not in rule["action"]["actions"][1]


@pytest.mark.parametrize(
    "value",
    [
        None,
        "plain text",
        {"action": "nothing"},
    ],
)
def test_request_rule_result_step_id_assignment_handles_non_action_shapes(value) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    result = request_rules._assign_missing_result_step_ids(value)

    if isinstance(value, dict):
        assert result == value
        assert result is not value
    else:
        assert result is value


def test_request_rule_result_references_recurse_but_ignore_identity_fields() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    value = {
        "message": "Value {{ results.first.answer }}",
        "nested": [
            "Other {{ results.second }}",
            {"deep": "{{ results.third.value }}"},
        ],
        "result_alias": "{{ results.ignored }}",
        "step_id": "{{ results.also_ignored }}",
    }

    assert request_rules._result_references(value) == {
        "first",
        "second",
        "third",
    }


def test_request_rule_result_paths_collect_nested_references() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    paths = request_rules._result_paths_by_alias(
        {
            "message": "{{ results.first.answer }} / {{ results.first.name }}",
            "nested": [{"value": "{{ results.second }}"}],
        }
    )

    assert paths == {
        "first": {
            "{{ results.first.answer }}",
            "{{ results.first.name }}",
        },
        "second": {"{{ results.second }}"},
    }


@pytest.mark.parametrize(
    "value",
    [
        "{{ slots.missing }}",
        {"service": "light.{{ slots.missing }}"},
        ["{{ slots.missing }}"],
        "{{ unknown_template }}",
        "{% if true %}light.turn_on{% endif %}",
        "{# comment #}light.turn_on",
    ],
)
def test_guest_slot_resolution_fails_closed_for_unknown_or_dynamic_templates(
    value,
) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    with pytest.raises(request_rules.GuestModeDenied):
        request_rules._resolve_guest_slot_templates(value, {"known": "kitchen"})


def test_guest_slot_resolution_recurses_through_supported_shapes() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    value = {
        "action": "light.turn_on",
        "target": {
            "entity_id": [
                "light.{{ slots.room }}",
                "switch.{{ slots.room }}",
            ]
        },
        "count": 2,
    }

    assert request_rules._resolve_guest_slot_templates(
        value, {"room": "kitchen"}
    ) == {
        "action": "light.turn_on",
        "target": {
            "entity_id": [
                "light.kitchen",
                "switch.kitchen",
            ]
        },
        "count": 2,
    }
