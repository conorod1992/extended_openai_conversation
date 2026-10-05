"""Residual deterministic coverage sweep after reaching 98.24% stable coverage."""

from __future__ import annotations

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
