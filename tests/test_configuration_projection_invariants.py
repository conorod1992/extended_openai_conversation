"""Effective configuration projections must agree without rewriting their sources."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_config as config,
    backup,
    management_function_repair as cache,
    management_loading_performance as loading,
)
from custom_components.extended_openai_conversation_responses.const import (
    LEGACY_DEFAULT_PROMPTS,
)
from homeassistant.config_entries import ConfigEntryState

CASES = [
    pytest.param({}, id="missing-fields"),
    pytest.param(
        {"memory_mode": "manual", "memory_enabled": False, "memory_auto_create": True},
        id="manual-overrides-legacy-aliases",
    ),
    pytest.param(
        {"memory_mode": "off", "memory_enabled": True, "memory_auto_create": True},
        id="off-overrides-legacy-aliases",
    ),
    pytest.param(
        {"memory_mode": "automatic", "memory_enabled": False},
        id="automatic-overrides-legacy-aliases",
    ),
    pytest.param(
        {"exposed_entity_attributes": None}, id="nullable-attribute-preferences"
    ),
    pytest.param(
        {"max_tokens": 1234.0, "archive_retention_days": 7.0},
        id="legacy-integral-floats",
    ),
    pytest.param({"memory_mode": "manual"}, id="manual-memory-original-regression"),
    pytest.param(
        {"memory_enabled": True, "memory_auto_create": True}, id="legacy-memory"
    ),
    pytest.param({"prompt": next(iter(LEGACY_DEFAULT_PROMPTS))}, id="legacy-prompt"),
    pytest.param(
        {"max_tokens": "1234", "temperature": "0.25", "archive_retention_days": "7"},
        id="legacy-numbers",
    ),
    pytest.param(
        {"local_intent_exclusions": [" light.test ", "light.test"]},
        id="selector-canonicalization",
    ),
    pytest.param(
        {
            "exposed_entity_attributes": {
                "registry:probe": ["z", "a", "a"],
                "registry:empty": [],
            }
        },
        id="attribute-canonicalization",
    ),
]


@pytest.mark.parametrize("overrides", CASES)
@pytest.mark.parametrize("validated", [False, True])
async def test_fast_and_cold_projection_equivalence(overrides, validated):
    raw = {"functions": "[]", **deepcopy(overrides)}
    original = deepcopy(raw)
    assert loading._snapshot_normalized_configuration(
        raw, validated=validated
    ) == config.agent_config_snapshot(raw)
    assert raw == original


@pytest.mark.parametrize(
    "saved", ["null", "~", " ", "\n# empty document\n", "", None, [], "[]"]
)
async def test_prewarm_preserves_persisted_function_semantics(hass, saved):
    raw = {"functions": saved}
    subentry = SimpleNamespace(
        subentry_id="projection-agent", title="Projection", data=raw
    )
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED, subentries={subentry.subentry_id: subentry}
    )
    expected = config.parse_configured_function_tools(raw) or []
    cold = config.agent_config_snapshot(raw)
    cache.discard_persisted_config_projection(subentry)
    await cache.async_prewarm_persisted_config_projection(hass, entry, subentry)
    warm, _ = cache.normalized_persisted_config_snapshot(
        cache.persisted_config_projection(subentry)
    )
    assert cold["functions"] == expected
    assert warm["functions"] == expected
    assert raw == {"functions": saved}


async def test_legacy_empty_collection_recovery_preserves_effective_functions(hass):
    raw = {"functions": []}
    expected = config.parse_configured_function_tools(raw)
    exported = backup.export_configuration_snapshot(raw)
    private = backup.private_configuration_snapshot(raw)
    assert exported["functions"]
    assert isinstance(private["functions"], str)
    for snapshot in (exported, private):
        restored = backup.recoverable_configuration_snapshot(snapshot)
        assert config.parse_configured_function_tools(restored) == expected
    assert raw == {"functions": []}


async def test_retention_projection_uses_canonical_numeric_values():
    subentry = SimpleNamespace(
        subentry_id="retention-agent",
        title="Projection",
        data={
            "functions": "[]",
            "usage_request_retention_days": "7",
            "usage_run_retention_days": "30",
        },
    )
    projection = cache.persisted_config_projection(subentry)
    snapshot, _ = cache.normalized_persisted_config_snapshot(projection)
    assert projection.retention == {key: snapshot[key] for key in projection.retention}


@pytest.fixture
async def projection_rules(monkeypatch):
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses import (
        management_ui as ui,
    )
    from custom_components.extended_openai_conversation_responses.request_rules import (
        RequestRules,
    )
    from tests.test_request_rules import MemoryStore

    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    monkeypatch.setattr(
        ui,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(total_source_count=0)),
    )
    monkeypatch.setattr(
        ui, "decorate_configuration_result", lambda _h, _d, result, **kw: result
    )
    return rules


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize(
    "action", ["group", "function", "knowledge", "save", "settings"]
)
@pytest.mark.parametrize(
    "variant",
    ["memory", "legacy-values", "legacy-empty-functions", "yaml-null-functions"],
)
async def test_scoped_save_seed_reconstructs_the_same_effective_projection(
    hass, management_agent, management_message, projection_rules, warm, action, variant
):
    from custom_components.extended_openai_conversation_responses import (
        management_ui as ui,
    )
    from custom_components.extended_openai_conversation_responses.memory import (
        memory_enabled,
    )

    _entry, subentry = management_agent
    raw = {
        "functions": "[]",
        "memory_mode": "manual",
        "chat_model": "gpt-5.6",
        "future_extension": {"keep": [None, False]},
    }
    if variant == "legacy-values":
        raw.update(
            prompt=next(iter(LEGACY_DEFAULT_PROMPTS)),
            max_tokens="1234",
            usage_request_retention_days="7",
            local_intent_exclusions=[" light.test ", "light.test"],
            exposed_entity_attributes={
                "registry:probe": ["z", "a", "a"],
                "registry:empty": [],
            },
        )
    elif variant == "legacy-empty-functions":
        raw["functions"] = []
    elif variant == "yaml-null-functions":
        raw["functions"] = "null"
    subentry.data = deepcopy(raw)
    cache.discard_persisted_config_projection(subentry)
    if warm:
        await ui.async_management_command(
            hass, "admin", True, management_message("configuration", "get")
        )
    revision = cache.persisted_config_projection(subentry).revision
    fields = {"revision": revision}
    if action == "group":
        names = [
            tool["spec"]["name"]
            for tool in config.parse_configured_function_tools(raw) or []
        ]
        section, command = "tools", "save_group"
        fields["group"] = {
            "id": "projection-group",
            "name": "Projection",
            "description": "Projection group",
            "loading_mode": "always",
            "functions": names[:1],
        }
        owned = {"functions", "function_groups"}
    elif action == "function":
        section, command = "tools", "save"
        fields["tool"] = {
            "spec": {
                "name": "projection_probe",
                "description": "Probe",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "get_user_from_user_id"},
        }
        owned = {"functions", "function_groups"}
    elif action == "knowledge":
        section, command = "knowledge", "set_enabled"
        fields["enabled"] = True
        owned = {"knowledge_enabled"}
    elif action == "settings":
        section, command = "settings", "update"
        fields["settings"] = {"archive_retention_days": 7}
        owned = {"archive_retention_days"}
    else:
        section, command = "configuration", "save"
        fields["config"] = {"archive_enabled": True}
        owned = {"archive_enabled"}
    saved = await ui.async_management_command(
        hass, "admin", True, management_message(section, command, **fields)
    )
    assert saved.get("valid", True), saved
    assert {key: value for key, value in subentry.data.items() if key not in owned} == {
        key: value for key, value in raw.items() if key not in owned
    }
    immediate = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    expected = config.agent_config_snapshot(dict(subentry.data))
    assert immediate["config"] == expected
    assert immediate["config"]["memory_enabled"] == memory_enabled(subentry.data)
    assert immediate["config"]["memory_mode"] == "manual"
    cache.discard_persisted_config_projection(subentry)
    reconstructed = await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "get")
    )
    assert reconstructed["config"] == immediate["config"]
    if "config" in saved:
        assert saved["config"] == expected


@pytest.mark.parametrize("saved", [None, "", [], "[]", "null", "~", "# empty YAML\n"])
async def test_function_semantics_survive_duplicate_export_and_recovery(
    hass, management_agent, management_message, saved
):
    from custom_components.extended_openai_conversation_responses import (
        management_ui as ui,
        restore_recovery,
    )
    from tests.test_backup import _document

    _entry, subentry = management_agent
    subentry.data = {
        "functions": saved,
        "memory_enabled": True,
        "memory_auto_create": False,
    }
    original = deepcopy(subentry.data)
    expected = config.parse_configured_function_tools(original) or []
    await ui.async_management_command(
        hass, "admin", True, management_message("configuration", "duplicate")
    )
    duplicate = hass.config_entries.async_add_subentry.call_args.args[1]
    exported = ui._export_agent(subentry)
    imported = ui._parse_import_document(exported)["config"]
    snapshots = [duplicate.data, imported]
    for snapshot in (
        backup.export_configuration_snapshot(original),
        backup.private_configuration_snapshot(original),
    ):
        snapshots.append(backup.recoverable_configuration_snapshot(snapshot))
    document = _document()
    document["agent"]["config"] = backup.private_configuration_snapshot(original)
    prepared = backup.inspect_backup(
        document, subentry.subentry_id, private_journal=True
    )
    journal = restore_recovery._new_journal(
        "entry", subentry.subentry_id, prepared, prepared
    )
    snapshots.append(
        backup.inspect_backup(
            journal["rollback"], subentry.subentry_id, private_journal=True
        ).config
    )
    for source in snapshots:
        assert config.parse_configured_function_tools(source) == expected
        assert config.agent_config_snapshot(dict(source))["memory_enabled"]
    assert subentry.data == original


@pytest.mark.parametrize(
    "model,effort",
    [("gpt-4.1", None), ("gpt-5.6", None), ("gpt-5.6", "high"), ("gpt-5.6-luna", None)],
)
@pytest.mark.parametrize("azure", [False, True])
@pytest.mark.parametrize(
    "sampling",
    [{}, {"temperature": 1.0, "top_p": 1.0}, {"temperature": 0.0, "top_p": 0.9}],
)
async def test_model_dependent_projection_preserves_raw_presence(
    model, effort, azure, sampling
):
    raw = {"functions": "[]", "chat_model": model, **sampling}
    if azure:
        raw.update(chat_model="deployment-name", azure_model=model)
    if effort is not None:
        raw["reasoning_effort"] = effort
    original = deepcopy(raw)
    cold = config.agent_config_snapshot(raw)
    assert loading._snapshot_normalized_configuration(raw, validated=True) == cold
    # A scoped edit cannot pin a currently recommended effort or sampling setting.
    updates = {"knowledge_enabled": True}
    candidate = config.merge_agent_config(raw, updates)
    persisted = config.project_agent_config_updates(raw, updates, candidate)
    assert persisted == {**raw, **updates}
    assert raw == original


@pytest.mark.parametrize("overrides", CASES)
async def test_cached_prewarmed_seeded_and_replaced_sources_agree(hass, overrides):
    raw = {"functions": "[]", **deepcopy(overrides)}
    subentry = SimpleNamespace(
        subentry_id="cache-projection", title="Projection", data=raw
    )
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED, subentries={subentry.subentry_id: subentry}
    )
    original = deepcopy(raw)
    expected = config.agent_config_snapshot(raw)
    projection = cache.persisted_config_projection(subentry)
    cold, hit = cache.normalized_persisted_config_snapshot(projection)
    assert not hit and cold == expected
    hot, hit = cache.normalized_persisted_config_snapshot(projection)
    assert hit and hot == expected
    cache.seed_persisted_config_projection(
        entry,
        subentry,
        loading._snapshot_normalized_configuration(raw, validated=True),
        projection.revision,
    )
    seeded, hit = cache.normalized_persisted_config_snapshot(
        cache.persisted_config_projection(subentry)
    )
    assert hit and seeded == expected
    cache.discard_persisted_config_projection(subentry)
    await cache.async_prewarm_persisted_config_projection(hass, entry, subentry)
    prewarmed, hit = cache.normalized_persisted_config_snapshot(
        cache.persisted_config_projection(subentry)
    )
    assert hit and prewarmed == expected
    subentry.data = deepcopy(raw)
    replaced, hit = cache.normalized_persisted_config_snapshot(
        cache.persisted_config_projection(subentry)
    )
    assert not hit and replaced == expected
    assert raw == original and subentry.data == original


@pytest.mark.parametrize("empty", [None, "", [], "[]", "null"])
async def test_new_explicit_empty_edit_is_distinct_from_legacy_saved_empty(empty):
    raw = {"functions": [], "memory_enabled": True}
    candidate = config.merge_agent_config(raw, {"functions": empty})
    persisted = config.project_agent_config_updates(
        raw, {"functions": empty}, candidate
    )
    assert config.parse_configured_function_tools(persisted) == []
    assert config.agent_config_snapshot(persisted)["functions"] == []
    assert raw == {"functions": [], "memory_enabled": True}


@pytest.mark.parametrize("warm", [False, True])
async def test_retention_seed_and_reconstruction_agree(hass, warm):
    raw = {
        "functions": "[]",
        "usage_request_retention_days": "7",
        "usage_run_retention_days": "30",
    }
    subentry = SimpleNamespace(
        subentry_id="retention-seed", title="Projection", data=raw
    )
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED, subentries={subentry.subentry_id: subentry}
    )
    projection = cache.persisted_config_projection(subentry)
    expected = dict(projection.retention)
    if warm:
        cache.normalized_persisted_config_snapshot(projection)
    snapshot = loading._snapshot_normalized_configuration(raw, validated=True)
    cache.seed_persisted_config_projection(
        entry, subentry, snapshot, projection.revision
    )
    assert cache.persisted_config_projection(subentry).retention == expected
    cache.discard_persisted_config_projection(subentry)
    assert cache.persisted_config_projection(subentry).retention == expected
    assert raw["usage_request_retention_days"] == "7"


async def test_lightweight_settings_and_full_configuration_use_same_numeric_values():
    from custom_components.extended_openai_conversation_responses.management_projections import (
        settings_snapshot,
    )

    raw = {
        "functions": "[]",
        "archive_retention_days": "7",
        "archive_session_timeout_minutes": "30",
        "usage_request_retention_days": "7",
        "usage_run_retention_days": "30",
    }
    original = deepcopy(raw)
    shown = settings_snapshot(raw)
    full = config.agent_config_snapshot(raw)
    for key in raw.keys() - {"functions"}:
        assert shown[key] == full[key]
        assert type(shown[key]) is type(full[key])
    assert raw == original


@pytest.mark.parametrize("saved", ["null", "[]", [], ""])
async def test_rollback_preparation_preserves_function_runtime_interpretation(hass, saved):
    from unittest.mock import AsyncMock

    from tests.test_backup import _document

    base = _document()
    managers = (
        SimpleNamespace(async_backup_data=AsyncMock(return_value=base["memories"])),
        SimpleNamespace(
            async_backup_data=AsyncMock(return_value=base["temporary_memories"])
        ),
        SimpleNamespace(async_backup_data=AsyncMock(return_value=base["knowledge"])),
        SimpleNamespace(
            async_recovery_data=AsyncMock(
                return_value={**base["archive"], "active": {}}
            )
        ),
        SimpleNamespace(async_backup_data=AsyncMock(return_value=base["usage"])),
        SimpleNamespace(async_backup_data=AsyncMock(return_value={"schedule": None})),
        SimpleNamespace(
            async_backup_data=AsyncMock(return_value={"defaults": {}, "rules": []})
        ),
    )
    subentry = SimpleNamespace(
        subentry_id="rollback-projection", title="Projection", data={"functions": saved}
    )
    rollback = await backup._snapshot_for_restore(managers, subentry)
    assert config.parse_configured_function_tools(rollback.config) == (
        config.parse_configured_function_tools(subentry.data) or []
    )
    assert isinstance(rollback.config["functions"], str)


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize(
    "sampling",
    [{}, {"temperature": "0.25", "top_p": "0.9"}, {"temperature": 0.0, "top_p": 1.0}],
)
async def test_shared_runtime_numeric_interpretation_matches_management_without_defaults(
    api, sampling
):
    from custom_components.extended_openai_conversation_responses.request import (
        build_provider_request_snapshot,
    )

    raw = {
        "chat_model": "gpt-4.1",
        "api_mode": api,
        "functions": "[]",
        "max_tokens": "1234",
        **sampling,
    }
    original = deepcopy(raw)
    wire = build_provider_request_snapshot(raw, {}, tools_required=False)
    shown = config.agent_config_snapshot(raw)
    for key in ("temperature", "top_p"):
        if key in raw:
            assert wire.api_kwargs[key] == shown[key]
            assert type(wire.api_kwargs[key]) is type(shown[key])
        else:
            assert key not in wire.api_kwargs
    assert raw == original


async def test_runtime_numeric_interpretation_keeps_capability_suppression():
    from custom_components.extended_openai_conversation_responses.request import (
        build_provider_request_snapshot,
    )

    raw = {
        "chat_model": "gpt-5.6",
        "api_mode": "responses",
        "functions": "[]",
        "reasoning_effort": "high",
        "temperature": "0.25",
        "top_p": "0.9",
    }
    original = deepcopy(raw)
    wire = build_provider_request_snapshot(raw, {}, tools_required=False)
    assert "temperature" not in wire.api_kwargs and "top_p" not in wire.api_kwargs
    assert raw == original


@pytest.mark.parametrize("saved", [None, [], "", "null", "[]"])
@pytest.mark.parametrize("changed", [False, True])
async def test_restore_update_compares_persisted_and_prepared_effective_values(
    hass, monkeypatch, saved, changed
):
    from unittest.mock import AsyncMock, Mock

    from custom_components.extended_openai_conversation_responses import restore_recovery
    from custom_components.extended_openai_conversation_responses.const import DOMAIN

    raw = {"functions": saved, "memory_mode": "manual"}
    subentry = SimpleNamespace(data=raw, title="Unchanged", subentry_id="agent")
    entry = SimpleNamespace(entry_id="entry")
    candidate = backup.private_configuration_snapshot(raw)
    assert isinstance(candidate["functions"], str)
    if changed:
        candidate["archive_enabled"] = True
    monkeypatch.setattr(restore_recovery, "update_live_subentry", Mock())
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    await restore_recovery._update_configuration(
        hass, entry, subentry, SimpleNamespace(config=candidate, title=subentry.title)
    )
    assert (
        entry.entry_id in hass.data.get(f"{DOMAIN}.restore_reload_pending", set())
    ) is changed
    assert subentry.data == raw
