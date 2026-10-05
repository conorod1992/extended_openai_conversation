"""Residual deterministic coverage sweep after reaching 98.24% stable coverage."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError


def test_request_rule_runtime_expiry_set_reset_and_request_reset(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    clock = iter([0.0, 1.0, 2.0, 500.0, 501.0, 502.0])
    monkeypatch.setattr(request_rules, "monotonic", lambda: next(clock))
    runtime = request_rules.RequestRuleRuntime()

    runtime.set("session", {request_rules.CONF_CHAT_MODEL: "model-a"}, timeout_minutes=1)
    assert runtime.get("session", timeout_minutes=1) == {
        request_rules.CONF_CHAT_MODEL: "model-a"
    }

    # A later access prunes the expired conversation override.
    assert runtime.get("session", timeout_minutes=1) == {}

    runtime.set("session-2", {request_rules.CONF_CHAT_MODEL: "model-b"}, timeout_minutes=1)
    runtime.reset("session-2")
    assert runtime._conversation_overrides == {}

    runtime._conversation_overrides["session-3"] = (
        {request_rules.CONF_CHAT_MODEL: "old"},
        502.0,
        10,
    )
    defaults = {
        request_rules.CONF_CHAT_MODEL: "configured",
        request_rules.CONF_REASONING_EFFORT: "medium",
    }
    result = runtime.effective_options(
        defaults,
        "session-3",
        {
            request_rules._REQUEST_RESET_SENTINEL: "1",
            request_rules.CONF_REASONING_EFFORT: "high",
        },
    )
    assert result == {
        request_rules.CONF_CHAT_MODEL: "configured",
        request_rules.CONF_REASONING_EFFORT: "high",
    }


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"fuzzy": "yes"},
        {"fuzzy_threshold": True},
        {"fuzzy_threshold": 69},
        {"fuzzy_threshold": 101},
        {"unknown": True},
    ],
)
def test_request_rule_matching_settings_reject_invalid_shapes(value) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    with pytest.raises(ValueError):
        request_rules.validate_matching_settings(value)


def test_request_rule_matching_settings_accept_valid_custom_values() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    result = request_rules.validate_matching_settings(
        {
            "word_forms": False,
            "wording_alternatives": False,
            "fuzzy": True,
            "fuzzy_threshold": 75,
        }
    )

    assert result == {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy": True,
        "fuzzy_threshold": 75,
    }


@pytest.mark.parametrize(
    "value",
    [
        "not-a-list",
        [{"id": "only"}],
        [{"id": "a", "name": "A", "extra": True}],
        [{"id": "", "name": "A"}],
    ],
)
def test_request_rule_groups_reject_invalid_shapes(value) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    with pytest.raises(ValueError):
        request_rules.validate_rule_groups(value)


def test_request_rule_session_id_prefers_continuity_identity() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    assert (
        request_rules.request_rule_session_id("device:one", "conversation")
        == "continuity:device:one"
    )
    assert (
        request_rules.request_rule_session_id(None, "conversation")
        == "conversation:conversation"
    )


def test_request_rule_restore_without_committed_snapshot_is_noop() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules
    from tests.test_request_rules import MemoryStore

    manager = request_rules.RequestRules(MemoryStore())
    manager._committed_state = None
    before = manager._defaults

    manager._restore_committed_state()

    assert manager._defaults is before


@pytest.mark.asyncio
async def test_temporary_memory_reconcile_rejects_invalid_store_shapes() -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    for payload, message in [
        (["invalid"], "invalid structure"),
        ({"records": {}}, "records have invalid structure"),
    ]:
        manager = temporary_memory.TemporaryMemory(
            SimpleNamespace(async_load=AsyncMock(return_value=payload))
        )
        with pytest.raises(ValueError, match=message):
            await manager._async_reconcile_failed_save()


def test_temporary_memory_restore_without_committed_state_is_noop() -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    manager = temporary_memory.TemporaryMemory(SimpleNamespace())
    manager._records = {"sentinel": object()}
    manager._committed_state = None

    manager._restore_committed_state()

    assert set(manager._records) == {"sentinel"}


def test_temporary_memory_record_from_storage_backfills_owner_scope() -> None:
    from custom_components.extended_openai_conversation_responses import temporary_memory

    raw = {
        "memory_id": "m1",
        "scope_id": "user:alice",
        "content": "fact",
        "category": "general",
        "source": "manual",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    record = temporary_memory._record_from_storage(raw)
    assert record.owner_scope_id == "user:alice"

    raw["scope_id"] = "conversation:one"
    record = temporary_memory._record_from_storage(raw)
    assert record.owner_scope_id is None


def test_model_catalog_rejects_invalid_conditional_function_calling_efforts() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    model = deepcopy(model_catalog.BUNDLED_CATALOG["models"][0])
    api = next(iter(model["function_calling"]))
    efforts = model["reasoning"]["efforts"]
    model["function_calling"][api] = {
        "support": "conditional",
        "allowed_reasoning_efforts": [],
    }

    with pytest.raises(ValueError, match="conditional function-calling efforts"):
        model_catalog._validate_metadata(model, model_entry=True)

    model = deepcopy(model_catalog.BUNDLED_CATALOG["models"][0])
    api = next(iter(model["function_calling"]))
    model["function_calling"][api] = {
        "support": "conditional",
        "allowed_reasoning_efforts": [efforts[0], efforts[0]] if efforts else ["low", "low"],
    }
    with pytest.raises(ValueError, match="conditional function-calling efforts"):
        model_catalog._validate_metadata(model, model_entry=True)


@pytest.mark.parametrize("value", [None, {}, {"schema_version": 2}])
def test_model_catalog_v1_migration_rejects_wrong_document(value) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    with pytest.raises(ValueError, match="v1"):
        model_catalog.migrate_catalog_v1(value)


def test_model_catalog_v1_migration_advances_old_catalog_version() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    old = {"schema_version": 1, "catalog_version": 999}
    migrated = model_catalog.migrate_catalog_v1(old)

    assert migrated["schema_version"] == model_catalog.BUNDLED_CATALOG["schema_version"]
    assert migrated["catalog_version"] >= 1000


@pytest.mark.parametrize(
    ("function_name", "schema_version"),
    [
        ("migrate_catalog_v2", 1),
        ("migrate_catalog_v3", 2),
        ("migrate_catalog_v4", 3),
    ],
)
def test_model_catalog_migrators_reject_wrong_schema(function_name: str, schema_version: int) -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    with pytest.raises(ValueError):
        getattr(model_catalog, function_name)(
            {"schema_version": schema_version, "catalog_version": 7}
        )


def test_model_catalog_validation_rejects_bad_version_and_duplicate_model() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    document = deepcopy(model_catalog.BUNDLED_CATALOG)
    document["catalog_version"] = 6
    with pytest.raises(ValueError, match="at least 7"):
        model_catalog.prepare_catalog(document)

    document = deepcopy(model_catalog.BUNDLED_CATALOG)
    document["models"].append(deepcopy(document["models"][0]))
    with pytest.raises(ValueError, match="duplicate model ID"):
        model_catalog.prepare_catalog(document)


def test_model_catalog_validation_rejects_snapshot_cycle() -> None:
    from custom_components.extended_openai_conversation_responses import model_catalog

    document = deepcopy(model_catalog.BUNDLED_CATALOG)
    base = deepcopy(document["models"][0])
    base["id"] = "coverage-cycle-a"
    base["kind"] = "snapshot"
    base["alias_of"] = "coverage-cycle-b"
    other = deepcopy(base)
    other["id"] = "coverage-cycle-b"
    other["alias_of"] = "coverage-cycle-a"
    document["models"] = [base, other]

    with pytest.raises(ValueError, match="inheritance cycle"):
        model_catalog.prepare_catalog(document)


@pytest.mark.asyncio
async def test_service_skill_source_ref_rejects_whitespace_version_fallback(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import services

    monkeypatch.setattr(
        services,
        "async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version=None)),
    )
    assert await services.async_skill_source_ref(hass) == services.GITHUB_SKILLS_BRANCH


@pytest.mark.asyncio
async def test_service_admin_rejects_missing_user(hass) -> None:
    from custom_components.extended_openai_conversation_responses import services

    hass.auth.async_get_user = AsyncMock(return_value=None)
    call = SimpleNamespace(context=SimpleNamespace(user_id="missing"))

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await services._async_require_service_admin(hass, call)


def test_guest_mode_policy_custom_groups_ignores_malformed_groups() -> None:
    from custom_components.extended_openai_conversation_responses import guest_mode

    tools = [
        {
            "spec": {"name": "safe"},
            "function": {"type": "native", "name": "execute_service"},
            "enabled": True,
        }
    ]
    options = {
        guest_mode.CONF_GUEST_FUNCTION_POLICY: "custom",
        guest_mode.CONF_GUEST_ALLOWED_GROUP_IDS: ["allowed"],
        guest_mode.CONF_FUNCTION_GROUPS: [
            "invalid",
            {"id": "other", "functions": ["safe"]},
            {"id": "allowed", "functions": [123]},
        ],
    }

    policy = guest_mode.resolve_guest_policy(
        SimpleNamespace(),
        options,
        SimpleNamespace(is_active=Mock(return_value=True)),
        tools,
    )

    assert "safe" not in policy.function_tools


def test_knowledge_chunk_split_single_short_chunk() -> None:
    from custom_components.extended_openai_conversation_responses import knowledge

    assert knowledge._split_chunks("short text") == [(0, "short text")]
    assert knowledge._split_chunks("") == []



@pytest.mark.asyncio
async def test_model_catalog_manager_load_invalid_saved_state_falls_back(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    manager = manager_module.ModelCatalogManager(hass)
    manager.store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={
                "catalog": None,
                "available_catalog": None,
                "last_checked": -1,
                "etag": None,
                "incompatible_catalog": None,
            }
        )
    )
    activate = Mock()
    log = Mock()
    monkeypatch.setattr(manager_module, "activate_catalog", activate)
    monkeypatch.setattr(manager_module, "log_handled_failure", log)

    await manager.async_load()

    assert manager.catalog is None
    assert manager.available_catalog is None
    assert manager.last_checked == 0.0
    assert manager.last_error == "Stored model data could not be loaded; using bundled data."
    activate.assert_called_once_with(None)
    log.assert_called()


@pytest.mark.asyncio
async def test_model_catalog_manager_apply_without_available_update(hass) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    manager = manager_module.ModelCatalogManager(hass)

    status = await manager.async_apply_update()

    assert status["update_available"] is False
    assert status["last_error"] == "No model data update is available."


@pytest.mark.asyncio
async def test_model_catalog_manager_failed_check_keeps_current_state_when_save_fails(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    manager = manager_module.ModelCatalogManager(hass)
    manager.catalog = None
    manager.available_catalog = None
    manager.etag = "etag"
    manager.incompatible_catalog = {"schema_version": 99, "catalog_version": 100}
    manager._save = AsyncMock(side_effect=OSError("disk full"))
    log = Mock()
    monkeypatch.setattr(manager_module, "log_handled_failure", log)

    error = manager_module._TransientCatalogUpdateError(503)
    await manager._record_failed_check(123.0, transient=True, error=error)

    assert manager.last_checked == 123.0
    assert manager.last_error == "Model data check failed; the current catalogue was kept."
    assert log.call_count == 2


@pytest.mark.asyncio
async def test_model_catalog_manager_reset_blocked_by_saved_configuration(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    manager = manager_module.ModelCatalogManager(hass)
    monkeypatch.setattr(
        manager,
        "_bundled_reset_would_invalidate_saved_reasoning",
        AsyncMock(return_value=True),
    )
    preserves = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_candidate_preserves_saved_requests", preserves)

    status = await manager.async_reset()

    assert "reset was blocked" in status["last_error"]
    preserves.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_catalog_manager_reset_keeps_newer_active_as_available(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    manager = manager_module.ModelCatalogManager(hass)
    newer = deepcopy(manager_module.BUNDLED_CATALOG)
    newer["catalog_version"] = manager_module.BUNDLED_CATALOG["catalog_version"] + 1
    manager.catalog = newer
    manager.available_catalog = None
    manager.etag = "new-etag"
    manager.last_checked = 100.0
    manager._save = AsyncMock()
    monkeypatch.setattr(
        manager,
        "_bundled_reset_would_invalidate_saved_reasoning",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        manager,
        "_candidate_preserves_saved_requests",
        AsyncMock(return_value=True),
    )
    activate = Mock()
    sync = Mock()
    monkeypatch.setattr(manager_module, "activate_catalog", activate)
    monkeypatch.setattr(manager_module, "sync_all_model_lifecycles", sync)

    status = await manager.async_reset()

    assert status["source"] == "bundled"
    assert status["update_available"] is True
    assert manager.available_catalog is newer
    assert manager.etag == "new-etag"
    activate.assert_called_once_with(None)
    sync.assert_called_once_with(hass)


@pytest.mark.asyncio
async def test_model_catalog_candidate_rejects_invalid_saved_agent_request(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        model_catalog_manager as manager_module,
    )

    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="ai_task_data",
        data={manager_module.CONF_CHAT_MODEL: manager_module.DEFAULT_CHAT_MODEL},
    )
    entry = SimpleNamespace(
        entry_id="entry",
        data={},
        subentries={"agent": subentry},
    )
    hass.config_entries.async_entries = Mock(return_value=[entry])
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.request.build_provider_request_snapshot",
        Mock(side_effect=HomeAssistantError("invalid saved request")),
    )
    manager = manager_module.ModelCatalogManager(hass)

    assert (
        await manager._candidate_preserves_saved_requests(
            manager_module.BUNDLED_CATALOG
        )
        is False
    )



def test_agent_config_function_groups_none_is_empty() -> None:
    from custom_components.extended_openai_conversation_responses import agent_config

    assert agent_config._validate_function_groups(None, []) == []


def test_agent_config_reasoning_default_is_removed_when_model_has_no_recommendation(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import agent_config

    base = agent_config.agent_config_defaults()
    base[agent_config.CONF_CHAT_MODEL] = "coverage-model"
    base.pop(agent_config.CONF_REASONING_EFFORT, None)
    monkeypatch.setattr(
        agent_config,
        "get_reasoning_effort_options",
        Mock(return_value=["low"]),
    )
    monkeypatch.setattr(
        agent_config,
        "get_model_config",
        Mock(return_value={"recommended_profile": {"reasoning_effort": None}}),
    )

    result = agent_config.normalize_agent_config(base)

    assert agent_config.CONF_REASONING_EFFORT not in result


@pytest.mark.asyncio
async def test_runtime_configuration_second_fast_path_after_refresh(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        agent_configuration,
    )

    options = {}
    entity = SimpleNamespace(
        hass=SimpleNamespace(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent", data=options),
    )
    setattr(entity, agent_configuration._RUNTIME_CONFIG_DATA, options)
    setattr(entity, agent_configuration._RUNTIME_CONFIG_RETRY, False)
    monkeypatch.setattr(agent_configuration, "_runtime_needs_recovery", Mock(side_effect=[True, False]))
    monkeypatch.setattr(agent_configuration, "_gate_disabled_subsystems", Mock())
    monkeypatch.setattr(agent_configuration, "_refresh_non_manager_state", Mock())

    await agent_configuration.async_reconcile_runtime_configuration(entity)

    agent_configuration._gate_disabled_subsystems.assert_called_once_with(entity, options)
    agent_configuration._refresh_non_manager_state.assert_called_once_with(entity, options)


def test_debug_memory_retrieval_records_active_trace(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import debug

    trace = SimpleNamespace(phases_ms={}, memory={})
    monkeypatch.setattr(debug, "current_debug_trace", Mock(return_value=trace))

    debug.record_memory_retrieval("temporary", 0.0, [{"id": 1}, {"id": 2}])

    assert trace.memory["temporary_count"] == 2
    assert trace.memory["temporary_records"] == [{"id": 1}, {"id": 2}]
    assert "temporary_memory_retrieval" in trace.phases_ms


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"error": {"code": "model_not_found"}}, "model_unavailable"),
        ({"error": {"type": "insufficient_quota"}}, "insufficient_quota"),
        ({"code": "context_length_exceeded"}, "context_length"),
        ({"type": "unsupported_parameter"}, "unsupported_parameter"),
    ],
)
def test_provider_failure_category_reads_structured_error_body(body, expected) -> None:
    from custom_components.extended_openai_conversation_responses import provider_errors

    error = RuntimeError("provider")
    error.body = body

    assert provider_errors.provider_failure_category(error) == expected


@pytest.mark.parametrize(
    "pattern",
    [
        "hello\\",
        "{name:one|two\\}",
    ],
)
def test_request_rule_pattern_rejects_trailing_escape(pattern: str) -> None:
    from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
        SentencePatternError,
        compile_sentence_pattern,
    )

    with pytest.raises(SentencePatternError, match="escape"):
        compile_sentence_pattern(pattern)


def test_request_rule_pattern_rejects_empty_constrained_choice() -> None:
    from custom_components.extended_openai_conversation_responses.request_rule_patterns import (
        SentencePatternError,
        compile_sentence_pattern,
    )

    with pytest.raises(SentencePatternError, match="empty choice"):
        compile_sentence_pattern("{room:kitchen|}")


@pytest.mark.asyncio
async def test_voice_identity_device_mapping_without_device_or_mapping_returns_empty() -> None:
    from custom_components.extended_openai_conversation_responses import (
        voice_identity_runtime as voice,
    )

    agent = SimpleNamespace(
        hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock())),
        subentry=SimpleNamespace(
            data={voice.CONF_VOICE_SCOPE_POLICY: voice.VOICE_POLICY_DEVICE_MAPPING}
        ),
    )
    user_input = SimpleNamespace(context=None, device_id=None, satellite_id=None)
    assert await voice._active_configured_users(agent, user_input) == frozenset()

    agent.subentry.data[voice.CONF_VOICE_DEVICE_MAPPINGS] = "invalid"
    user_input.device_id = "device"
    assert await voice._active_configured_users(agent, user_input) == frozenset()



def test_management_function_field_unchanged_contract() -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    safe = {
        repair.CONF_FUNCTION_TOOLS: [{"spec": {"name": "one"}}],
        repair.CONF_FUNCTION_GROUPS: [{"id": "g", "functions": ["one"]}],
    }
    assert repair._function_fields_unchanged({}, safe)
    assert repair._function_fields_unchanged(
        {repair.CONF_FUNCTION_TOOLS: deepcopy(safe[repair.CONF_FUNCTION_TOOLS])},
        safe,
    )
    assert not repair._function_fields_unchanged(
        {repair.CONF_FUNCTION_TOOLS: []},
        safe,
    )
    assert not repair._function_fields_unchanged(
        {repair.CONF_FUNCTION_GROUPS: []},
        safe,
    )


def test_management_group_function_rename_and_remove_helpers() -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    groups = [
        {"id": "valid", "functions": ["old", "keep"]},
        "malformed",
        {"id": "invalid-functions", "functions": "old"},
    ]

    renamed = repair._replace_group_function_name(groups, "old", "new")
    assert renamed[0]["functions"] == ["new", "keep"]
    assert groups[0]["functions"] == ["old", "keep"]

    assert repair._replace_group_function_name(groups, None, "new") == groups
    assert repair._replace_group_function_name(groups, "same", "same") == groups
    assert repair._replace_group_function_name("not-a-list", "old", "new") == "not-a-list"

    removed = repair._remove_group_function_name(groups, "old")
    assert removed[0]["functions"] == ["keep"]
    assert repair._remove_group_function_name(groups, None) == groups
    assert repair._remove_group_function_name("not-a-list", "old") == "not-a-list"


def test_management_revision_guards_accept_none_and_reject_invalid_or_stale(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    subentry = SimpleNamespace(data={}, title="Agent")
    repair.require_agent_config_revision(subentry, None)

    with pytest.raises(HomeAssistantError, match="revision must be a string"):
        repair.require_agent_config_revision(subentry, 123)

    monkeypatch.setattr(
        repair,
        "persisted_config_projection",
        Mock(return_value=SimpleNamespace(revision="current")),
    )
    with pytest.raises(HomeAssistantError, match="Configuration changed"):
        repair.require_agent_config_revision(subentry, "stale")

    repair.require_agent_config_revision(subentry, "current")

    monkeypatch.setattr(repair, "repair_revision", Mock(return_value="repair-current"))
    with pytest.raises(HomeAssistantError):
        repair.require_repair_revision(subentry, None)
    with pytest.raises(HomeAssistantError):
        repair.require_repair_revision(subentry, "stale")
    repair.require_repair_revision(subentry, "repair-current")


def test_management_projection_cache_hit_and_discard() -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    subentry = SimpleNamespace(data={"x": 1}, title="Agent")
    diagnostics = {}
    first = repair.persisted_config_projection(subentry, diagnostics)
    assert diagnostics["projection_cache_hit"] is False

    diagnostics = {}
    second = repair.persisted_config_projection(subentry, diagnostics)
    assert second is first
    assert diagnostics["projection_cache_hit"] is True

    repair.discard_persisted_config_projection(subentry)
    third = repair.persisted_config_projection(subentry)
    assert third is not first


def test_management_normalized_projection_reuses_cached_snapshot(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    snapshot = {"value": [1, 2]}
    projection = SimpleNamespace(
        snapshot=deepcopy(snapshot),
        data={},
    )
    diagnostics = {}

    result, hit = repair.normalized_persisted_config_snapshot(
        projection,
        diagnostics,
    )

    assert hit is True
    assert result == snapshot
    assert result is not projection.snapshot
    assert diagnostics["default_snapshot_reused"] is False
    assert "snapshot_copy_ms" in diagnostics


def test_management_seed_projection_preserves_existing_repair_state(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    data = repair.agent_config_defaults()
    subentry = SimpleNamespace(subentry_id="agent", data=data, title="Agent")
    entry = SimpleNamespace(subentries={"agent": subentry})
    old_state = object()
    previous = repair._PersistedProjection(
        subentry,
        data,
        "Agent",
        None,
        "old",
        {
            key: deepcopy(data[key])
            for key in repair._RETENTION_FIELDS
        },
        repair_state=old_state,
    )
    repair._persisted_projections[id(subentry)] = previous
    snapshot = repair.agent_config_snapshot(data)

    repair.seed_persisted_config_projection(entry, subentry, snapshot, "new")

    seeded = repair._persisted_projections[id(subentry)]
    assert seeded.repair_state is old_state
    assert seeded.snapshot is None
    assert seeded.repair_snapshot == snapshot


def test_management_saved_revision_uses_supplied_data_for_lightweight_double(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        management_function_repair as repair,
    )

    subentry = SimpleNamespace(data={"a": 1}, title="Old")
    expected = repair.agent_config_revision({"a": 2}, "New")

    assert repair.saved_agent_config_revision(subentry, {"a": 2}, "New") == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"error": {"code": "deployment_not_found"}}, "model_unavailable"),
        ({"error": {"code": "billing_limit_reached"}}, "insufficient_quota"),
        ({"error": {"code": "max_context_length_exceeded"}}, "context_length"),
        ({"error": {"code": "unsupported_value"}}, "unsupported_parameter"),
    ],
)
def test_provider_failure_category_additional_body_codes(body, expected) -> None:
    from custom_components.extended_openai_conversation_responses import provider_errors

    error = RuntimeError("provider")
    error.body = body
    assert provider_errors.provider_failure_category(error) == expected



@pytest.mark.asyncio
async def test_continuity_default_claim_lifecycle_and_unknown_release() -> None:
    from custom_components.extended_openai_conversation_responses.continuity import (
        ConversationContinuity,
    )

    manager = ConversationContinuity("agent")
    token = await manager._async_claim_ha_default_conversation("conversation")

    assert token in manager._ha_default_claims
    assert manager._ha_default_lock_users["conversation"] == 1
    assert manager._release_ha_default_claim("missing") is False
    assert manager._release_ha_default_claim(token) is True
    assert "conversation" not in manager._ha_default_locks
    assert "conversation" not in manager._ha_default_lock_users


@pytest.mark.asyncio
async def test_continuity_default_claim_cancellation_drops_waiter_reference() -> None:
    from custom_components.extended_openai_conversation_responses.continuity import (
        ConversationContinuity,
    )

    manager = ConversationContinuity("agent")
    first = await manager._async_claim_ha_default_conversation("conversation")
    waiter = asyncio.create_task(
        manager._async_claim_ha_default_conversation("conversation")
    )
    await asyncio.sleep(0)
    assert manager._ha_default_lock_users["conversation"] == 2

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert manager._ha_default_lock_users["conversation"] == 1
    assert manager._release_ha_default_claim(first) is True
    assert "conversation" not in manager._ha_default_locks


def test_continuity_new_conversation_id_namespaces_are_inspectable() -> None:
    from custom_components.extended_openai_conversation_responses.continuity import (
        ConversationContinuity,
    )

    manager = ConversationContinuity("agent")
    plain = manager._new_conversation_id(None)
    guest = manager._new_conversation_id("guest")

    assert plain.startswith("extended-openai-agent-")
    assert guest.startswith("extended-openai-guest-agent-")
    assert plain != guest


@pytest.mark.asyncio
async def test_archive_record_turn_missing_session_returns_none() -> None:
    from custom_components.extended_openai_conversation_responses import conversation_archive

    archive = conversation_archive.ConversationArchive(SimpleNamespace(), "agent")
    archive._initialized = True

    result = await archive.async_record_turn(
        "missing",
        run_id=None,
        user_text="hello",
        assistant_text="world",
        successful=True,
    )

    assert result is None


@pytest.mark.asyncio
async def test_archive_delete_date_range_requires_confirmation_and_string_dates() -> None:
    from custom_components.extended_openai_conversation_responses import conversation_archive

    archive = conversation_archive.ConversationArchive(SimpleNamespace(), "agent")
    archive._initialized = True

    with pytest.raises(ValueError, match="confirmation"):
        await archive.async_delete_date_range(
            "user:one",
            "2026-01-01",
            "2026-01-02",
            confirm=False,
        )

    with pytest.raises(ValueError, match="valid start_date"):
        await archive.async_delete_date_range(
            "user:one",
            123,
            "2026-01-02",
            confirm=True,
        )


def test_archive_invalidate_clears_all_live_state() -> None:
    from custom_components.extended_openai_conversation_responses import conversation_archive

    archive = conversation_archive.ConversationArchive(SimpleNamespace(), "agent")
    archive._sessions["s"] = object()
    archive._turns["s"].append(object())
    archive._active["scope"] = "s"
    archive._partitions.add("2026-10")
    archive._pending_partitions.add("2026-10")
    archive._initialized = True

    archive._invalidate_after_unreadable_metadata()

    assert archive._sessions == {}
    assert dict(archive._turns) == {}
    assert archive._active == {}
    assert archive._partitions == set()
    assert archive._pending_partitions == set()
    assert archive._initialized is False


def test_archive_ensure_initialized_rejects_uninitialized_library() -> None:
    from custom_components.extended_openai_conversation_responses import conversation_archive

    archive = conversation_archive.ConversationArchive(SimpleNamespace(), "agent")
    with pytest.raises(RuntimeError, match="has not been initialized"):
        archive._ensure_initialized()



class _SharedGate:
    @asynccontextmanager
    async def shared(self):
        yield


@pytest.mark.asyncio
async def test_service_function_tool_enable_rejects_missing_entry_and_unknown_tool(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import services

    monkeypatch.setattr(
        services, "resolve_memory_agent", Mock(return_value=("entry", "agent"))
    )
    monkeypatch.setattr(
        services, "get_agent_maintenance_gate", Mock(return_value=_SharedGate())
    )
    hass.config_entries.async_get_entry = Mock(return_value=None)

    with pytest.raises(HomeAssistantError, match="Config entry not found"):
        await services.async_set_function_tools_enabled(
            hass, "entry", "agent", ["missing"], True
        )

    subentry = SimpleNamespace(subentry_id="agent", data={})
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry})
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    monkeypatch.setattr(
        services,
        "validate_function_tools",
        Mock(return_value=[{"spec": {"name": "known"}, "enabled": True}]),
    )

    with pytest.raises(HomeAssistantError, match="Function Tool not found: missing"):
        await services.async_set_function_tools_enabled(
            hass, "entry", "agent", ["missing", "missing"], False
        )


@pytest.mark.asyncio
async def test_service_function_tool_enable_updates_requested_tool(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import services

    monkeypatch.setattr(
        services, "resolve_memory_agent", Mock(return_value=("entry", "agent"))
    )
    monkeypatch.setattr(
        services, "get_agent_maintenance_gate", Mock(return_value=_SharedGate())
    )
    configured = [
        {"spec": {"name": "one"}, "enabled": True},
        {"spec": {"name": "two"}, "enabled": True},
    ]
    subentry = SimpleNamespace(subentry_id="agent", data={})
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry})
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    monkeypatch.setattr(
        services, "validate_function_tools", Mock(return_value=deepcopy(configured))
    )
    monkeypatch.setattr(
        services, "merge_agent_config", Mock(side_effect=lambda _base, update: update)
    )
    update = Mock()
    monkeypatch.setattr(services, "update_live_subentry", update)

    await services.async_set_function_tools_enabled(
        hass, "entry", "agent", ["two", "two"], False
    )

    persisted = update.call_args.kwargs["data"][services.CONF_FUNCTION_TOOLS]
    assert persisted[0]["enabled"] is True
    assert persisted[1]["enabled"] is False


@pytest.mark.asyncio
async def test_service_function_group_enable_missing_and_success_paths(
    hass, monkeypatch
) -> None:
    from custom_components.extended_openai_conversation_responses import services

    monkeypatch.setattr(
        services, "resolve_memory_agent", Mock(return_value=("entry", "agent"))
    )
    monkeypatch.setattr(
        services, "get_agent_maintenance_gate", Mock(return_value=_SharedGate())
    )
    subentry = SimpleNamespace(subentry_id="agent", data={})
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry})
    hass.config_entries.async_get_entry = Mock(return_value=entry)
    monkeypatch.setattr(services, "validate_function_tools", Mock(return_value=[]))
    groups = [
        {"id": "one", "name": "One", "functions": [], "enabled": True},
        {"id": "two", "name": "Two", "functions": [], "enabled": True},
    ]
    monkeypatch.setattr(
        services, "validate_function_groups", Mock(return_value=deepcopy(groups))
    )

    with pytest.raises(HomeAssistantError, match="Function Group not found: missing"):
        await services.async_set_function_groups_enabled(
            hass, "entry", "agent", ["missing"], False
        )

    monkeypatch.setattr(
        services, "merge_agent_config", Mock(side_effect=lambda _base, update: update)
    )
    update = Mock()
    monkeypatch.setattr(services, "update_live_subentry", update)

    await services.async_set_function_groups_enabled(
        hass, "entry", "agent", ["two", "two"], False
    )

    persisted = update.call_args.kwargs["data"][services.CONF_FUNCTION_GROUPS]
    assert persisted[0]["enabled"] is True
    assert persisted[1]["enabled"] is False



@pytest.mark.parametrize(
    "value",
    [
        "not-a-list",
        [{"canonical": "on"}],
        [{"canonical": "on", "alternatives": "enable"}],
        [{"canonical": "on", "alternatives": []}],
        [{"canonical": "!!!", "alternatives": ["enable"]}],
        [{"canonical": "turn on", "alternatives": ["turn on"]}],
        [
            {"canonical": "turn on", "alternatives": ["enable"]},
            {"canonical": "enable", "alternatives": ["activate"]},
        ],
    ],
)
def test_request_rule_wording_groups_reject_invalid_or_ambiguous_shapes(value) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    with pytest.raises(ValueError):
        request_rules.validate_wording_groups(value)


def test_request_rule_wording_groups_deduplicate_exact_alternatives() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    result = request_rules.validate_wording_groups(
        [
            {
                "canonical": "turn on",
                "alternatives": ["enable", "enable", "activate"],
            }
        ]
    )

    assert result == [
        {
            "canonical": "turn on",
            "alternatives": ["enable", "activate"],
        }
    ]


def test_request_rule_match_cursor_ranks_one_fuzzy_result_per_rule(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import request_rules

    rule = {
        "id": "rule",
        "name": "Rule",
        "order": 0,
        "match_type": "equals",
    }
    settings = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy_threshold": 0,
    }
    snapshot = request_rules._MatchingSnapshot(
        phrases=(),
        wording_groups=(),
        deterministic=(),
        fuzzy=(
            (
                rule,
                settings,
                request_rules.CompiledPhrase("first", normalized="first"),
            ),
            (
                rule,
                settings,
                request_rules.CompiledPhrase("second", normalized="second"),
            ),
        ),
    )
    scores = iter([50.0, 90.0])
    monkeypatch.setattr(request_rules, "_fuzzy_score", lambda *_args: next(scores))

    cursor = request_rules._MatchCursor(snapshot, "input")
    match = cursor.next_match()

    assert match is not None
    assert match.rule["id"] == "rule"
    assert match.score == 90.0
    assert cursor.next_match() is None


def test_request_rule_direct_match_excludes_all_candidates() -> None:
    from custom_components.extended_openai_conversation_responses import request_rules
    from tests.test_request_rules import MemoryStore

    rule = {
        "id": "one",
        "name": "One",
        "order": 0,
        "match_type": "equals",
    }
    settings = {
        "word_forms": False,
        "wording_alternatives": False,
        "fuzzy_threshold": 0,
    }
    manager = request_rules.RequestRules(MemoryStore())
    manager._committed_matching_snapshot = request_rules._MatchingSnapshot(
        phrases=(),
        wording_groups=(),
        deterministic=(
            (
                rule,
                settings,
                request_rules.CompiledPhrase("hello", normalized="hello"),
            ),
        ),
        fuzzy=(
            (
                rule,
                settings,
                request_rules.CompiledPhrase("hello", normalized="hello"),
            ),
        ),
    )

    assert manager.match("hello", frozenset({"one"})) is None
