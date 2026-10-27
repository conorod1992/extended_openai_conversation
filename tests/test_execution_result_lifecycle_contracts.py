"""Combined guarantees for execution provenance, local dataflow and durable cleanup."""

from dataclasses import replace

from dataclasses import asdict, dataclass
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup_transfer as bt,
    request_rules as rr,
    transfer_staging as staging,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
    execution_completed,
    execution_prepared,
    execution_rejected,
    execution_started,
)
from custom_components.extended_openai_conversation_responses.functions import (
    CompositeFunction,
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.model_tool_results import (
    OwnedJSONPayload,
    local_function_results,
    project_tool_result,
    serialize_model_tool_result,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryRecord,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    clear_unacknowledged_calls,
    remember_unacknowledged_calls,
    was_unacknowledged_equivalent,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _entity,
    _result,
    _retain_calls,
    _tool,
)
from tests.test_knowledge import FakeStorage
from tests.test_temporary_memory import Storage, stored_record


@pytest.mark.parametrize("depth", [0, 1, 3])
async def test_native_names_rejected_recursively_before_execution(hass, depth):
    invalid = {"type": "native", "name": "not_an_implementation"}
    for _ in range(depth):
        invalid = {
            "type": "composite",
            "sequence": [
                {
                    "type": "native",
                    "name": "execute_service_single",
                    "response_variable": "response",
                },
                invalid,
            ],
        }
    backend = NativeFunction() if depth == 0 else CompositeFunction()
    with pytest.raises(HomeAssistantError, match="not_an_implementation"):
        backend.validate_schema(invalid)
    if depth:
        with pytest.raises(HomeAssistantError, match="not_an_implementation"):
            await backend.execute(hass, invalid, {}, None, [])
    hass.services.async_call.assert_not_called()
    from custom_components.extended_openai_conversation_responses.agent_config import (
        AgentConfigError,
        validate_function_tools,
    )

    with pytest.raises(AgentConfigError, match="not_an_implementation"):
        validate_function_tools([_tool("invalid", function=invalid)])


@pytest.mark.parametrize("state", ["completed", "uncertain", "partial", "rejected"])
def test_receipts_do_not_depend_on_result_history_or_business_payload(hass, state):
    entity, log = _entity(hass), _chat_log(hass)
    call = _call("initial", "effect", {"value": 1})
    tool = _tool("effect", parameters={"type": "object"})
    _retain_calls(log, [call])
    with execution_attempt(entity, call, tool):
        execution_prepared(call.tool_args)
        execution_started()
        if state in {"completed", "partial"}:
            execution_completed()
        if state == "partial":
            execution_started()
        if state == "rejected":
            execution_rejected()
    # No ToolResultContent exists: normalization/serialization may have failed.
    remember_unacknowledged_calls(entity, log, set())
    assert was_unacknowledged_equivalent(
        entity, log, _call("retry", "effect", {"value": 1})
    ) is (state != "rejected")
    assert was_unacknowledged_equivalent(
        entity, log, _call("changed", "effect", {"value": 2})
    ) is (state in {"uncertain", "partial"})
    clear_unacknowledged_calls(entity, log.conversation_id)
    assert not was_unacknowledged_equivalent(entity, log, call)


@pytest.mark.parametrize(
    "value",
    [False, 0, None, "", [], {}, {"status": "error"}, '{\n "value": [1, 2]\n}\n'],
)
async def test_local_results_and_model_text_preserve_type_and_value(hass, value):
    entity = _entity(hass)
    call = _call("result", "produce")
    content = _result(entity, call, value)
    entity._async_dispatch_function_tool = AsyncMock(return_value=content)
    tool = _tool("produce", function={"type": "template"})
    with local_function_results():
        local = await ExtendedOpenAIAgentEntity._execute_function_tool(
            entity, tool, call, None, []
        )
    model = await ExtendedOpenAIAgentEntity._execute_function_tool(
        entity, tool, call, None, []
    )
    for output in (local, model):
        actual = tool_result_data(output)["result"]
        assert type(actual) is type(value)
        assert actual == value


@pytest.mark.parametrize("value", [{"answer": False}, {"large": "x" * 33000}])
async def test_owned_projection_survives_ha_dataclass_serialization(hass, value):
    @dataclass
    class Envelope:
        data: dict

    entity = _entity(hass)
    content = make_tool_result_content(
        agent_id=entity.entity_id,
        tool_call_id="owned",
        tool_name="produce",
        tool_result=OwnedJSONPayload(value),
    )
    projected = await project_tool_result(content)
    payload = tool_result_data(projected)
    assert isinstance(payload, dict)
    assert json.loads(json.dumps(asdict(Envelope(payload))))["data"] == payload
    assert json.loads(payload["result"]) == value
    actual = json.loads(json.loads(serialize_model_tool_result(projected))["result"])
    assert (
        actual == value
        if len(json.dumps(value)) < 32000
        else actual["execution_completed"] is True
    )
    assert tool_result_data(content).value == value


async def test_projection_accepts_the_function_normalizers_serializable_types(hass):
    from datetime import UTC, datetime
    from uuid import UUID

    import orjson

    from custom_components.extended_openai_conversation_responses.entity import (
        _normalize_function_result,
    )

    @dataclass
    class Record:
        answer: int

    value = {
        "record": Record(42),
        "timestamp": datetime(2026, 10, 13, tzinfo=UTC),
        "id": UUID(int=1),
        "text": "é" * 20000,
    }
    assert _normalize_function_result(value) is value
    entity = _entity(hass)
    content = _result(entity, _call("typed", "produce"), value)
    projected = await project_tool_result(content)
    assert tool_result_data(projected)["result"] is value
    assert orjson.loads(orjson.dumps(value))["record"] == {"answer": 42}


async def test_structured_overflow_is_complete_and_does_not_mutate_local_result(hass):
    entity = _entity(hass)
    call = _call("large", "produce")
    value = {"nested": ['\\"\n' * 16000]}
    content = _result(entity, call, value)
    entity._async_dispatch_function_tool = AsyncMock(return_value=content)
    tool = _tool("produce")
    with local_function_results():
        local = await ExtendedOpenAIAgentEntity._execute_function_tool(
            entity, tool, call, None, []
        )
    projected = await ExtendedOpenAIAgentEntity._execute_function_tool(
        entity, tool, call, None, []
    )
    assert tool_result_data(local)["result"] == value
    assert tool_result_data(content)["result"] == value
    assert tool_result_data(projected)["result"] is value
    overflow = json.loads(serialize_model_tool_result(projected))["result"]
    assert overflow["execution_completed"] is True
    assert len(json.dumps(overflow)) < 32000
    with pytest.raises(HomeAssistantError, match="too large"):
        rr._bounded_function_result(value)


def test_alias_copy_uses_current_producer_and_leaves_authored_response_untouched():
    binding = rr._ResultAliasVariables(
        rr.ScriptVariables({"second": "{{ shared.result }}"}), "second", "shared"
    )
    shared = {"shared": {"result": "from A"}}
    token = rr._ACTIVE_RULE_ACTION.set(
        replace(rr._ACTIVE_RULE_ACTION.get(), function_results={"first": "from A"})
    )
    try:
        assert binding.async_simple_render(shared) == {}
        rr._ACTIVE_RULE_ACTION.get().function_results["second"] = False
        assert binding.async_simple_render(shared) == {"second": False}
        assert shared == {"shared": {"result": "from A"}}
    finally:
        rr._ACTIVE_RULE_ACTION.reset(token)


def test_unrelated_result_alias_fields_are_not_function_metadata():
    action = {"action": "script.ordinary", "data": {"result_alias": "input"}}
    assert rr._native_result_sequence([action], {}) == [action]
    rr._validate_result_dependencies(
        {
            "actions": [{"sequence": [action]}],
            "success_response": "Done",
            "failure_response": "Failed",
        },
        set(),
    )


@pytest.mark.parametrize(
    "selector", ["light.{room}", "{{ request.slots.entity }}", ["light.{room}"]]
)
def test_condition_capture_save_runtime_schema_parity(hass, selector):
    action = {
        "if": [{"condition": "state", "entity_id": selector, "state": "on"}],
        "then": [{"stop": "Done"}],
    }
    saved = rr._validate_script_sequence([action])
    runtime = rr._native_result_sequence(
        saved, {"room": "kitchen", "entity": "light.kitchen"}
    )
    validated = cv.SCRIPT_SCHEMA(runtime)
    assert "light.kitchen" in str(validated)
    with pytest.raises(ValueError, match="concrete selector"):
        rr._validate_script_sequence(
            [
                {
                    "condition": "state",
                    "entity_id": "{{ states('sensor.id') }}",
                    "state": "on",
                }
            ]
        )


async def test_canonical_knowledge_survives_storage_reload_and_export():
    raw = {
        "source_id": "guide",
        "title": "  Title\n here  ",
        "description": " Description\n here ",
        "content": "  One\r\nTwo  \r\n",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "enabled": True,
    }
    records = KnowledgeLibrary.validate_backup_data({"sources": [raw]})
    storage = FakeStorage({"sources": [raw]})
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    expected = {
        **raw,
        "title": "Title here",
        "description": "Description here",
        "content": "One\nTwo",
    }
    assert asdict(records[0]) == expected
    assert storage.data == {"sources": [expected]}
    fresh = KnowledgeLibrary(storage)
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == {"sources": [expected]}
    assert (await fresh.async_get_section("guide"))["returned_characters"] > 0


async def test_canonical_temporary_memory_and_first_record_budget_survive_reload():
    raw = stored_record(0)
    raw.update(content=" Fact\n  here ", category=" Category\n here ")
    storage = Storage({"records": [raw]})
    expected = {**raw, "content": "Fact here", "category": "Category here"}
    assert asdict(TemporaryMemory.validate_backup_data(storage.data)[0]) == expected
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    assert storage.data == {"records": [expected]}
    fresh = TemporaryMemory(storage)
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == {"records": [expected]}
    oversized = TemporaryMemoryRecord(**{**expected, "content": "x" * 6001})
    assert (
        TemporaryMemory.select_active_snapshot(
            [oversized], raw["scope_id"], raw["owner_scope_id"]
        )
        == []
    )


@pytest.mark.parametrize("failed_index", [0, 1])
async def test_all_retired_files_attempted_accounted_and_retried(
    hass, tmp_path, monkeypatch, failed_index
):
    sessions = []
    for index in range(3):
        path = tmp_path / f"private-{index}"
        path.write_bytes(b"private")
        sessions.append(
            bt.ExportSession(
                str(index), "entry", "agent", str(path), "backup.zip", 7, "hash", 0
            )
        )
        bt._exports(hass)[str(index)] = sessions[-1]
    original = bt._remove_file
    attempted = []

    def remove(path):
        attempted.append(path)
        if path == sessions[failed_index].path:
            raise OSError("injected")
        original(path)

    monkeypatch.setattr(bt, "_remove_file", remove)
    with pytest.raises(OSError):
        await bt.async_remove_agent_transfers(hass, "entry", "agent")
    assert len(attempted) == 3
    assert not bt._exports(hass)
    assert bt._temporary_bytes(hass) == 7
    assert list(bt._pending(hass)) == [sessions[failed_index].path]
    monkeypatch.setattr(bt, "_remove_file", original)
    bt._pending(hass)[sessions[failed_index].path].retry_at = 0
    await bt._async_cleanup_expired(hass)
    assert bt._temporary_bytes(hass) == 0
    assert not any(tmp_path.glob("private-*"))


async def test_successful_restore_reports_cleanup_pending_without_repeating(
    hass, tmp_path, monkeypatch
):
    path = tmp_path / "upload"
    path.write_text("private")
    session = bt.ImportSession(
        "upload", "entry", "agent", str(path), "backup.json", 7, "legacy_json", 10**12
    )
    bt._imports(hass)[session.session_id] = session
    monkeypatch.setattr(bt, "_async_cleanup_expired", AsyncMock())
    monkeypatch.setattr(bt, "_completed_import", lambda *_: session)
    monkeypatch.setattr(
        bt, "_async_load_prepared_restore", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        bt.transfer,
        "async_apply_user_scope_mappings",
        AsyncMock(side_effect=lambda value, *_: value),
    )

    async def restore(*args, **kwargs):
        bt._imports(hass).pop(session.session_id)
        return {"restored": True}

    operation = AsyncMock(side_effect=restore)
    monkeypatch.setattr(bt.transfer, "async_restore_transfer", operation)
    monkeypatch.setattr(
        bt, "_remove_file", lambda _: (_ for _ in ()).throw(OSError("injected"))
    )
    result = await bt._restore_import(
        hass,
        SimpleNamespace(entry_id="entry"),
        SimpleNamespace(subentry_id="agent"),
        {"session_id": "upload", "preview_token": "token"},
    )
    assert result == {"restored": True, "cleanup_pending": True}
    operation.assert_awaited_once()
    assert path.exists() and str(path) in bt._pending(hass)


def test_staging_owner_remains_retryable_after_failed_close(tmp_path, monkeypatch):
    directory = tmp_path / "owner"
    directory.mkdir()
    (directory / "private").write_text("private")
    owner = staging.TransferStagingOwner(directory, BytesIO())
    original = staging.shutil.rmtree
    monkeypatch.setattr(
        staging.shutil, "rmtree", lambda _: (_ for _ in ()).throw(OSError("injected"))
    )
    with pytest.raises(OSError):
        owner.close()
    assert not owner.lock_handle.closed
    monkeypatch.setattr(staging.shutil, "rmtree", original)
    owner.close()
    assert owner.lock_handle.closed and not directory.exists()


async def test_ranked_knowledge_pages_reconstruct_all_matches_with_escaping(hass):
    from custom_components.extended_openai_conversation_responses.guest_mode import (
        GuestCapabilityPolicy,
    )
    from custom_components.extended_openai_conversation_responses.model_tool_results import (
        compact_tool_json,
    )

    library = KnowledgeLibrary(FakeStorage())
    await library.async_initialize()
    for index in range(17):
        await library.async_create(
            f"Reference {index}", 'Escaped \\" description', "match " + ('\\"\n' * 700)
        )
    agent = _entity(hass)
    agent.subentry = SimpleNamespace(data={"knowledge_enabled": True})
    agent._knowledge = library
    agent._knowledge_available = True
    agent._effective_guest_policy = GuestCapabilityPolicy.unrestricted
    expected = await library.async_search("match", limit=10)
    expected += await library.async_search("match", limit=10, offset=10)
    all_results = []
    offset = 0
    while True:
        page = await ExtendedOpenAIAgentEntity._async_execute_knowledge_tool(
            agent, "search", {"query": "match", "limit": 10, "offset": offset}
        )
        assert len(compact_tool_json(page)) <= 32000
        all_results.extend(page["results"])
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert [record["source_id"] for record in all_results] == [
        record.source_id for record in expected
    ]
    assert len(all_results) == 17


@pytest.mark.parametrize("backend", ["template", "script"])
def test_composite_observers_and_schema_annotations_do_not_imply_equivalence(
    hass, backend
):
    from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
        _signature,
    )

    native = {"type": "native", "name": "execute_service_single"}
    composite = {"type": "composite", "sequence": [native, {"type": backend}]}
    spec = {
        "parameters": {
            "type": "object",
            "properties": {"note": {"type": "string", "default": "annotation"}},
        },
        "_execution_config": composite,
    }
    first = _call(
        "one",
        "effect",
        {"domain": "light", "service": "turn_on", "data": {"entity_id": "light.x"}},
    )
    other = _call(
        "two",
        "effect",
        {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": ["light.x"]},
        },
    )
    assert _signature(first, spec) != _signature(other, spec)
    assert _signature(first, spec) != _signature(
        _call("three", "effect", {**first.tool_args, "note": "annotation"}), spec
    )


@pytest.mark.parametrize(
    "operation", ["expiry", "cancel_export", "cancel_import", "shutdown"]
)
async def test_retired_cleanup_paths_remain_owned_until_success(
    hass, tmp_path, monkeypatch, operation
):
    path = tmp_path / "private"
    path.write_bytes(b"private")
    if operation == "cancel_import":
        session = bt.ImportSession(
            "session", "entry", "agent", str(path), "backup.json", 7, "legacy_json", 0
        )
        bt._imports(hass)["session"] = session
    else:
        session = bt.ExportSession(
            "session", "entry", "agent", str(path), "backup.zip", 7, "hash", 0
        )
        bt._exports(hass)["session"] = session
    original = bt._remove_file
    monkeypatch.setattr(
        bt, "_remove_file", lambda _: (_ for _ in ()).throw(OSError("injected"))
    )
    close = AsyncMock(side_effect=OSError("owner cleanup failed"))
    monkeypatch.setattr(staging, "async_close_transfer_staging", close)
    action = {
        "expiry": lambda: bt._async_cleanup_expired(hass),
        "cancel_export": lambda: bt._discard_export(hass, "session"),
        "cancel_import": lambda: bt._discard_import(hass, "session"),
        "shutdown": lambda: bt._async_cleanup_all(hass),
    }[operation]
    with pytest.raises(OSError):
        await action()
    assert not bt._exports(hass) and not bt._imports(hass)
    assert bt._temporary_bytes(hass) == 7 and path.exists()
    if operation == "shutdown":
        close.assert_awaited_once()
    monkeypatch.setattr(bt, "_remove_file", original)
    bt._pending(hass)[str(path)].retry_at = 0
    await bt._async_cleanup_expired(hass)
    assert bt._temporary_bytes(hass) == 0 and not path.exists()


async def test_failed_preregistration_allocations_are_retired_without_touching_active_files(
    hass, tmp_path, monkeypatch
):
    directory = tmp_path / "owner"
    directory.mkdir()
    active = directory / "extended-openai-backup-active"
    active.write_bytes(b"active")
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"keep")
    bt._exports(hass)["active"] = bt.ExportSession(
        "active", "entry", "agent", str(active), "active.zip", 6, "hash", 10**12
    )
    monkeypatch.setattr(
        staging, "async_get_transfer_staging", AsyncMock(return_value=directory)
    )

    def fail_allocate(_directory):
        (directory / "extended-openai-backup-orphan").write_bytes(b"orphan")
        raise OSError("allocation failed after creating file")

    monkeypatch.setattr(bt, "_create_upload_file", fail_allocate)
    with pytest.raises(OSError, match="allocation failed"):
        await bt._async_create_upload_file(hass)
    assert bt._temporary_bytes(hass) == 12
    await bt._async_cleanup_expired(hass)
    assert active.exists() and unrelated.exists()
    assert bt._temporary_bytes(hass) == 6
    assert not (directory / "extended-openai-backup-orphan").exists()


async def test_unknown_concrete_jinja_capture_is_rejected_at_save(hass):
    with pytest.raises(ValueError, match="capture available before execution"):
        rr._validate_script_sequence(
            [
                {
                    "condition": "state",
                    "entity_id": "{{ request.slots.missing }}",
                    "state": "on",
                }
            ],
            capture_names={"room"},
        )


async def test_memory_ranked_pages_reconstruct_after_serialized_budget_reduction(hass):
    from types import MethodType

    from custom_components.extended_openai_conversation_responses.memory import (
        PersistentMemory,
    )
    from tests.test_memory import FakeStorage as MemoryStorage

    memory = PersistentMemory(MemoryStorage())
    await memory.async_initialize()
    for index in range(35):
        await memory.async_add(
            "user:owner", f"match unique-{index} " + '\\"' * 430, "facts", "explicit"
        )
    agent = _entity(hass)
    agent.subentry = SimpleNamespace(data={"memory_mode": "manual"})
    agent._memory = memory
    agent._current_readable_memory_scope_ids = lambda _: ["user:owner"]
    agent._filter_read_scopes = lambda scopes, _: scopes
    agent._personal_memory_scope_id = lambda _: "user:owner"
    agent._async_search_memories = MethodType(
        ExtendedOpenAIAgentEntity._async_search_memories, agent
    )
    expected = await memory.async_search("user:owner", "match", limit=100)
    actual = []
    offset = 0
    while True:
        page = await ExtendedOpenAIAgentEntity._async_execute_memory_tool(
            agent, "search", {"query": "match", "limit": 50, "offset": offset}, None
        )
        actual.extend(record["memory_id"] for record in page["memories"])
        assert len(json.dumps(page, separators=(",", ":"))) <= 32000
        if not page["has_more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert actual == [record.memory_id for record in expected]
    assert len(actual) == 35


async def test_shutdown_cannot_forget_private_files_when_owner_is_unavailable(
    hass, tmp_path, monkeypatch
):
    path = tmp_path / "private"
    path.write_bytes(b"private")
    bt._pending(hass)[str(path)] = bt.PendingDeletion(7)
    monkeypatch.setattr(staging, "async_close_transfer_staging", AsyncMock())
    with pytest.raises(OSError, match="cleanup remains pending"):
        await bt._async_cleanup_all(hass)
    assert bt._temporary_bytes(hass) == 7
    assert path.exists()


@pytest.mark.parametrize("depth", [1, 3])
async def test_nested_hydrated_composites_preserve_source_and_response_metadata(
    hass, depth
):
    from copy import deepcopy

    source = {
        "type": "composite",
        "sequence": [
            {
                "type": "template",
                "value_template": "First {{ value }}",
                "response_variable": "authored",
            },
            {"type": "template", "value_template": "{{ authored }} finished"},
        ],
    }
    for _ in range(depth):
        source = {"type": "composite", "sequence": [source]}
    backend = CompositeFunction()
    hydrated = backend.validate_schema(source)
    assert deepcopy(hydrated) == source
    assert (
        await backend.execute(hass, hydrated, {"value": "result"}, None, [])
        == "First result finished"
    )
    assert deepcopy(hydrated) == source


async def test_failed_allocation_scan_reserves_quota_and_retries_without_hiding_cancellation(
    hass, tmp_path, monkeypatch
):
    directory = tmp_path / "owner"
    directory.mkdir()
    path = directory / "extended-openai-backup-orphan"
    path.write_bytes(b"private")
    original = hass.async_add_executor_job

    async def fail_scan(*_):
        raise OSError("scan unavailable")

    monkeypatch.setattr(hass, "async_add_executor_job", fail_scan)
    await bt._async_own_unregistered_files(hass, directory)
    assert bt._temporary_bytes(hass) >= bt.MAX_TRANSFER_TEMP_BYTES
    with pytest.raises(bt.backup.BackupError, match="quota"):
        bt._ensure_session_capacity(hass, 1)
    assert str(directory) in bt._pending_scans(hass)
    monkeypatch.setattr(hass, "async_add_executor_job", original)
    bt._pending_scans(hass)[str(directory)].retry_at = 0
    await bt._async_cleanup_expired(hass)
    assert not path.exists()
    assert not bt._pending_scans(hass) and bt._temporary_bytes(hass) == 0


async def test_shutdown_waits_for_transfer_allocation_ownership(hass, monkeypatch):
    import asyncio

    close = AsyncMock()
    monkeypatch.setattr(staging, "async_close_transfer_staging", close)
    async with bt._start_lock(hass):
        cleanup = asyncio.create_task(bt._async_cleanup_all(hass))
        await asyncio.sleep(0)
        close.assert_not_awaited()
    await cleanup
    close.assert_awaited_once()
    with pytest.raises(bt.backup.BackupError, match="shutting down"):
        bt._ensure_session_capacity(hass, 1)


@pytest.mark.parametrize("kind", ["knowledge", "temporary"])
async def test_failed_save_reconciliation_decodes_the_actual_durable_generation(kind):
    from copy import deepcopy

    class AmbiguousStorage(FakeStorage):
        replacement = None

        async def async_save(self, data):
            if self.replacement is not None:
                self.data = deepcopy(self.replacement)
                self.replacement = None
                raise OSError("lost save acknowledgement")
            await super().async_save(data)

    if kind == "knowledge":
        stamp = "2026-01-01T00:00:00+00:00"
        raw = {
            "source_id": "retained",
            "title": " Changed\n title ",
            "description": " Changed\n description ",
            "content": " First\r\nSecond \r\n",
            "created_at": stamp,
            "updated_at": stamp,
            "enabled": True,
        }
        payload = {"sources": [raw]}
        expected = {
            "sources": [asdict(KnowledgeLibrary.validate_backup_data(payload)[0])]
        }
        storage = AmbiguousStorage(expected)
        manager = KnowledgeLibrary(storage)
        await manager.async_initialize()
        storage.replacement = payload
        with pytest.raises(OSError, match="lost save"):
            await manager.async_replace_backup(
                KnowledgeLibrary.validate_backup_data(expected)
            )
        assert await manager.async_backup_data() == expected
        fresh = KnowledgeLibrary(storage)
    else:
        raw = {
            **stored_record(0),
            "content": " Changed\n fact ",
            "category": " Changed\n category ",
        }
        payload = {"records": [raw]}
        expected = {
            "records": [asdict(TemporaryMemory.validate_backup_data(payload)[0])]
        }
        storage = AmbiguousStorage(expected)
        manager = TemporaryMemory(storage)
        await manager.async_initialize()
        storage.replacement = payload
        with pytest.raises(OSError, match="lost save"):
            await manager.async_replace_backup(
                TemporaryMemory.validate_backup_data(expected)
            )
        assert await manager.async_backup_data() == expected
        fresh = TemporaryMemory(storage)
    await fresh.async_initialize()
    assert await fresh.async_backup_data() == expected
    assert storage.data == expected
