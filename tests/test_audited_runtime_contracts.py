"""Regressions for the fourteen audited execution and persistence contracts."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    management_ui,
    model_tool_results,
    quiet_hours,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
    bind_tool_recovery_state,
)
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.runtime_hardening import (
    MAX_MODEL_TOOL_RESULT_CHARACTERS,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    record_dispatch,
    remember_unacknowledged_calls,
)
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryChange
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from tests.test_function_security import _execution_entity
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _entity,
    _result,
    _retain_calls,
    _tool,
)
from tests.test_knowledge import FakeStorage, _library
from tests.test_quiet_hours_runtime import _stateful_public_manager
from tests.test_request_rules_management import MemoryStore, local_rule


@pytest.mark.parametrize("retry_target", [["light.x"], " light.x "])
async def test_failed_continuation_blocks_normalized_service_retry(
    hass, monkeypatch, retry_target
):
    entity = _entity(hass)
    chat = _chat_log(hass)
    tool = _tool(
        "control",
        function={"type": "native", "name": "execute_service_single"},
        parameters={"type": "object", "additionalProperties": True},
    )
    native = NativeFunction()
    monkeypatch.setattr(
        native, "validate_service_targets", lambda *args, **kwargs: None
    )
    hass.services.async_call.return_value = None

    async def execute(selected, call, *_):
        record_dispatch(entity, call, selected)
        await native.execute(hass, selected["function"], call.tool_args, None, [])
        return _result(entity, call)

    entity._execute_function_tool.side_effect = execute
    first = _call(
        "first",
        "control",
        {"domain": "light", "service": "turn_on", "entity_id": "light.x"},
    )
    before = {id(content) for content in chat.content}
    _retain_calls(chat, [first])
    await async_execute_tool_exchange(
        entity, chat, [first], [tool], FunctionCallBudget(10), None, []
    )
    remember_unacknowledged_calls(entity, chat, before)
    retry = _call(
        "retry",
        "control",
        {
            "domain": "light",
            "service": "turn_on",
            "service_data": {"entity_id": retry_target},
        },
    )
    _retain_calls(chat, [retry])
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(
            entity, chat, [retry], [tool], FunctionCallBudget(10), None, []
        )
    hass.services.async_call.assert_awaited_once()


async def test_case_distinct_temporary_facts_survive_reload():
    store = MemoryStore()
    memory = TemporaryMemory(store)
    await memory.async_initialize()
    contents = ["/config/Reports.csv", "/config/reports.csv"]
    for content in contents:
        await memory.async_add(
            "device:kitchen",
            content,
            (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
            owner_scope_id="user:alice",
        )
    restored = TemporaryMemory(store)
    await restored.async_initialize()
    assert sorted(record.content for record in restored._records.values()) == sorted(
        contents
    )
    assert sorted(record["content"] for record in store.data["records"]) == sorted(
        contents
    )


async def test_reserved_captured_ai_input_is_retained_inactive_and_can_be_disabled():
    rule = local_rule()
    rule.update(
        match_type="sentence_pattern",
        phrases=["ask {request}"],
        ai_input_mode="capture",
        ai_input_capture="request",
    )
    rule["action"]["continue_to_ai"] = True
    store = MemoryStore({"rules": [rule]})
    for _ in range(2):
        manager = rr.RequestRules(store)
        await manager.async_initialize()
        saved = manager.snapshot()["rules"][0]
        assert saved["ai_input_capture"] == "request"
        assert saved["id"] in manager.snapshot()["diagnostics"]
        assert manager.match("ask hello") is None
        await manager.async_update(saved["id"], {**saved, "enabled": False})
    with pytest.raises(ValueError):
        await manager.async_update(saved["id"], {**saved, "enabled": True})


@pytest.mark.parametrize(
    "target", ["light.{room}", {"value_from": "slot", "slot": "room"}]
)
async def test_legacy_captured_target_survives_migration_and_reload(hass, target):
    rule = local_rule()
    rule.update(match_type="sentence_pattern", phrases=["turn off {room}"])
    rule["action"]["actions"] = [
        {"domain": "light", "service": "turn_off", "target": {"entity_id": target}}
    ]
    store = MemoryStore({"rules": [rule]})
    for _ in range(2):
        manager = rr.RequestRules(store)
        await manager.async_initialize()
        assert manager.snapshot()["diagnostics"] == {}
        assert manager.match("turn off kitchen") is not None
        assert manager.snapshot()["rules"][0]["action"]["actions"][0]["target"][
            "entity_id"
        ] in ["light.{room}", "{room}"]


async def test_invalid_legacy_action_is_retained_for_repair():
    rule = local_rule()
    rule["action"]["actions"] = [
        {"action": "invalid-service", "target": {"entity_id": "light.x"}}
    ]
    store = MemoryStore({"rules": [rule]})
    for _ in range(2):
        manager = rr.RequestRules(store)
        await manager.async_initialize()
        saved = manager.snapshot()["rules"][0]
        assert saved["action"]["actions"] == rule["action"]["actions"]
        assert saved["id"] in manager.snapshot()["diagnostics"]
        assert manager.match("good night") is None
        await manager.async_update(saved["id"], {**saved, "enabled": False})
    repaired = deepcopy(saved)
    repaired.update(enabled=True)
    repaired["action"]["actions"][0]["action"] = "light.turn_off"
    await manager.async_update(saved["id"], repaired)
    assert manager.match("good night") is not None


async def test_inactive_legacy_action_survives_backup_restore_and_reload():
    rule = local_rule()
    rule["action"]["actions"] = [{"action": "invalid-service"}]
    original = rr.RequestRules(MemoryStore({"rules": [rule]}))
    await original.async_initialize()
    backup = await original.async_backup_data()
    store = MemoryStore()
    restored = rr.RequestRules(store)
    await restored.async_replace_backup(backup)
    for manager in (restored, rr.RequestRules(store)):
        await manager.async_initialize()
        snapshot = manager.snapshot()
        assert snapshot["rules"][0]["action"]["actions"] == [
            {"action": "invalid-service"}
        ]
        assert rule["id"] in snapshot["diagnostics"]
        assert manager.match("good night") is None


async def test_unrelated_save_returns_supported_model_reasoning(
    hass, management_agent, management_message, monkeypatch
):
    _, subentry = management_agent
    subentry.data.update(chat_model="gpt-5.2-pro", service_tier="default")
    subentry.data.pop("reasoning_effort")
    manager = rr.RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=manager)
    )
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "configuration",
            "save",
            revision=management_ui._agent_config_revision(
                subentry.data, subentry.title
            ),
            config={"max_tokens": 750},
        ),
    )
    assert result["valid"] is True, result
    assert result["config"].get("reasoning_effort") in {None, "medium", "high", "xhigh"}
    from custom_components.extended_openai_conversation_responses.agent_config import (
        normalize_agent_config,
    )

    normalize_agent_config(result["config"])


async def test_guest_partial_execution_remains_terminal_during_safe_recovery(
    monkeypatch,
):
    tool = _tool("control", function={"type": "native", "name": "execute_service"})
    entity = _execution_entity(
        tool,
        GuestCapabilityPolicy(
            True,
            readable_entity_ids=frozenset({"light.guest"}),
            controllable_entity_ids=frozenset({"light.guest"}),
            configured_tool_names=frozenset({"control"}),
        ),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.conversation.get_exposed_entities",
        lambda _: [],
    )
    effects = []

    async def partial(*_):
        effects.append("completed")
        raise HomeAssistantError("private path /config/private")

    monkeypatch.setattr(ExtendedOpenAIBaseLLMEntity, "_execute_function_tool", partial)
    with (
        bind_tool_recovery_state(ToolRecoveryState(enabled=True)),
        pytest.raises(HomeAssistantError) as error,
    ):
        await entity._execute_function_tool(
            tool,
            _call(
                "batch",
                "control",
                {
                    "list": [
                        {
                            "domain": "light",
                            "service": "turn_on",
                            "service_data": {"entity_id": "light.guest"},
                        }
                    ]
                },
            ),
            None,
            [],
        )
    assert "private" not in str(error.value)
    assert error.value.__suppress_context__
    assert effects == ["completed"]


async def test_unloaded_deletion_observer_survives_provider_lifetime(hass, monkeypatch):
    callbacks = {}
    monkeypatch.setattr(
        agent_deletion,
        "async_dispatcher_connect",
        lambda _hass, signal, callback: callbacks.setdefault(signal, callback),
    )
    reconcile = AsyncMock()
    monkeypatch.setattr(agent_deletion, "async_reconcile_agent_storage", reconcile)
    agent_deletion.async_setup_agent_deletion(hass)
    agent_deletion.async_setup_agent_deletion(hass)
    assert len(callbacks) == 1
    await callbacks[SIGNAL_CONFIG_ENTRY_CHANGED](
        ConfigEntryChange.UPDATED, SimpleNamespace(domain=DOMAIN, state="not_loaded")
    )
    reconcile.assert_awaited_once_with(hass)


async def test_quiet_entity_registry_changes_publish_and_final_removal_cleans_registry(
    hass, monkeypatch
):
    manager = _stateful_public_manager(hass)
    identity = {
        "entity_id": "binary_sensor.extended_openai_quiet_hours",
        "disabled_by": None,
    }
    registry = SimpleNamespace(
        async_get_entity_id=lambda *_: identity["entity_id"],
        async_get=lambda _: SimpleNamespace(disabled_by=identity["disabled_by"]),
        async_remove=Mock(),
    )
    monkeypatch.setattr(quiet_hours.er, "async_get", lambda _: registry)
    manager._publish_state(None)
    original = identity["entity_id"]
    await manager.async_setup()
    event_name, callback = hass.bus.async_listen.call_args.args
    assert event_name == quiet_hours.er.EVENT_ENTITY_REGISTRY_UPDATED
    identity["entity_id"] = "binary_sensor.renamed_quiet_hours"
    await callback(SimpleNamespace(data={"entity_id": original}))
    assert hass.states.get(original) is None
    assert hass.states.get(identity["entity_id"]) is not None
    identity["disabled_by"] = "user"
    await callback(SimpleNamespace(data={"entity_id": identity["entity_id"]}))
    assert hass.states.get(identity["entity_id"]) is None
    hass.data[DOMAIN] = {"quiet_hours_manager": manager}
    hass.config_entries.async_entries.return_value = []
    monkeypatch.setattr(agent_deletion, "async_delete_entry_data", AsyncMock())
    await integration.async_remove_entry(hass, SimpleNamespace(entry_id="last"))
    registry.async_remove.assert_called_once_with(identity["entity_id"])


async def test_failed_provider_cleanup_is_discovered_after_restart(
    hass, tmp_path, monkeypatch
):
    root = tmp_path / ".storage"
    root.mkdir()
    orphan = root / f"{DOMAIN}.memory.gone.agent"
    sibling = root / f"{DOMAIN}.memory.live.agent"
    unrelated = root / "other.memory.gone.agent"
    for path in (orphan, sibling, unrelated):
        path.write_text("private", encoding="utf-8")
    live = SimpleNamespace(entry_id="live", subentries={"agent": object()})
    hass.config_entries.async_entries.return_value = [live]
    failure = True

    async def remove(store):
        if failure:
            raise OSError("transient filesystem error")
        Path(store.path).unlink(missing_ok=True)

    monkeypatch.setattr(Store, "async_remove", remove)
    with pytest.raises(OSError):
        await agent_deletion.async_delete_entry_data(
            hass, SimpleNamespace(entry_id="gone", subentries={"agent": object()})
        )
    hass.data.clear()  # A fresh process has neither known agents nor old managers.
    failure = False
    await agent_deletion.async_reconcile_agent_storage(hass)
    assert not orphan.exists()
    assert sibling.exists() and unrelated.exists()


async def test_quarantined_tool_does_not_block_native_rule_management(
    hass, management_agent, management_message, monkeypatch
):
    _, subentry = management_agent
    broken = _tool("broken")
    broken["spec"]["parameters"] = {"type": "object", "required": "invalid"}
    subentry.data["functions"] = [broken]
    manager = rr.RequestRules(MemoryStore())
    await manager.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=manager)
    )
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("request_rules", "create", rule=local_rule()),
    )
    disabled = {**result["rule"], "enabled": False}
    saved = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message(
            "request_rules", "update", rule_id=disabled["id"], rule=disabled
        ),
    )
    assert saved["rule"]["enabled"] is False
    referenced = local_rule(rule_id="broken-dependency")
    referenced["action"]["actions"] = [
        {
            "action": f"{DOMAIN}.call_function",
            "data": {"function": "broken", "arguments": {}},
        }
    ]
    with pytest.raises(HomeAssistantError):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            management_message("request_rules", "create", rule=referenced),
        )


@pytest.mark.parametrize(
    "content",
    ['"\\\n' * 15_000, "\x01" * 60_000],
    ids=["escaped", "control-characters"],
)
async def test_escaped_knowledge_pages_reconstruct_complete_content(hass, content):
    storage = FakeStorage()
    library = await _library(storage)
    source = await library.async_create("Escaped source", "", content)
    library = await _library(storage)
    expected = (await library.async_get(source.source_id)).content
    offset, pieces = 0, []
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    class ProjectionEntity(ExtendedOpenAIAgentEntity):
        async def _async_dispatch_function_tool(self, *_):
            return self.projected

    # Use the production dispatch/projection path, including generic truncation.
    while True:
        page = await library.async_get_section(source.source_id, offset, 20_000)
        projected = ExtendedOpenAIAgentEntity._tool_result(
            SimpleNamespace(entity_id="conversation.audit"),
            _call("page", "knowledge_get"),
            page,
        )
        entity = object.__new__(ProjectionEntity)
        entity.projected = projected
        bounded = await ExtendedOpenAIAgentEntity._execute_function_tool(
            entity, _tool("knowledge_get"), _call("page", "knowledge_get"), None, []
        )
        wire = tool_result_data(bounded)["result"]
        assert len(wire) <= MAX_MODEL_TOOL_RESULT_CHARACTERS
        decoded = json.loads(wire)
        pieces.append(decoded["content"])
        if not decoded["has_more"]:
            break
        assert (
            decoded["next_start_character"] == offset + len(decoded["content"]) > offset
        )
        offset = decoded["next_start_character"]
    assert "".join(pieces) == expected


async def test_chained_wording_matches_independently_of_catalog_order():
    groups = [
        {"canonical": "tv", "alternatives": ["television"]},
        {"canonical": "turn off television", "alternatives": ["bedtime"]},
    ]
    for ordered in (groups, list(reversed(groups))):
        manager = rr.RequestRules(MemoryStore())
        await manager.async_initialize()
        await manager.async_set_wording_groups(ordered)
        await manager.async_create({**local_rule(), "phrases": ["turn off tv"]})
        assert manager.match("bedtime") is not None
        assert (
            rr.normalize_text("bedtime bedtime", rr.DEFAULT_MATCHING, ordered)
            == "turn off tv turn off tv"
        )
    with pytest.raises(ValueError, match=r"expand|cycle|limit"):
        rr.validate_wording_groups([{"canonical": "x x", "alternatives": ["x"]}])


@pytest.mark.parametrize(
    "groups, utterance, expected",
    [
        (
            [
                {"canonical": "a c", "alternatives": ["c c"]},
                {"canonical": "b b c", "alternatives": ["a a a"]},
            ],
            "c c c c",
            "a c a c",
        ),
        (
            [
                {"canonical": "b c", "alternatives": ["b b"]},
                {"canonical": "b b c", "alternatives": ["c a"]},
            ],
            "c a a a",
            "b c b c b c c",
        ),
        (
            [
                {"canonical": "c b c", "alternatives": ["c a"]},
                {"canonical": "a a c", "alternatives": ["b c b"]},
            ],
            "a c a a",
            "a c a a",
        ),
    ],
    ids=["adjacent-overlap", "context-needs-more-passes", "context-does-not-converge"],
)
async def test_contextual_wording_stays_bounded_without_aborting_matching(
    groups, utterance, expected
):
    manager = rr.RequestRules(MemoryStore())
    await manager.async_initialize()
    await manager.async_set_wording_groups(groups)
    await manager.async_create({**local_rule(), "phrases": [expected]})
    assert rr.normalize_text(utterance, rr.DEFAULT_MATCHING, groups) == expected
    assert manager.match(utterance) is not None


async def test_large_wording_catalog_compiles_once_across_validation_and_reload(
    monkeypatch,
):
    store = MemoryStore()
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    groups = [
        {
            "canonical": f"canonical{i}",
            "alternatives": [f"alternative{i}item{j}" for j in range(25)],
        }
        for i in range(100)
    ]
    rr._compiled_wording_replacements.cache_clear()
    compile_pattern = Mock(wraps=rr.re.compile)
    monkeypatch.setattr(rr.re, "compile", compile_pattern)
    await manager.async_set_wording_groups(groups)
    assert (
        rr.normalize_text("alternative99item24", rr.DEFAULT_MATCHING, groups)
        == "canonical99"
    )
    reloaded = rr.RequestRules(store)
    await reloaded.async_initialize()
    assert reloaded.snapshot()["wording_groups"] == groups
    assert compile_pattern.call_count == 2500


@pytest.mark.parametrize("key", ["service_template", "service", "action"])
async def test_native_service_name_aliases_validate_consistently(hass, key):
    actions = [{key: "{{ 'light.turn_off' }}", "target": {"entity_id": "light.x"}}]
    cv.SCRIPT_SCHEMA(deepcopy(actions))
    rule = local_rule()
    rule["action"]["actions"] = actions
    assert (
        rr.validate_rule(rule)["action"]["actions"][0].get("service", "")
        or key == "action"
    )


async def test_oversized_rest_json_is_bounded_without_full_parsing(monkeypatch):
    value = json.dumps({"content": "x" * 9_000_000})
    content = _result(
        SimpleNamespace(entity_id="conversation.audit"), _call("rest", "rest"), value
    )
    monkeypatch.setattr(
        model_tool_results.json,
        "loads",
        Mock(side_effect=AssertionError("oversized parsing blocks the event loop")),
    )
    assert model_tool_results._compact_json_result_content(content) is content
    assert tool_result_data(content)["result"] == value
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    class RestResultEntity(ExtendedOpenAIAgentEntity):
        async def _async_dispatch_function_tool(self, *_):
            return content

    projected = await object.__new__(RestResultEntity)._execute_function_tool(
        {}, {}, None, []
    )
    assert (
        len(tool_result_data(projected)["result"]) == MAX_MODEL_TOOL_RESULT_CHARACTERS
    )
