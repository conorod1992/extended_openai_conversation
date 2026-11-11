"""6.8.3 fuzzy settings retire without losing rules or changing other contracts."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    export_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRuleRuntime,
    RequestRules,
    RequestRuleStore,
    async_evaluate_rule,
    validate_rule,
)
from tests.test_request_rules import (
    FakeServices,
    MemoryStore,
    fake_ha_script as fake_ha_script,
    local_rule,
)


@pytest.mark.parametrize("behavior", ["defaults", "custom"])
@pytest.mark.parametrize(
    "match_type,prefix,suffix",
    [
        ("equals", "", ""),
        ("starts_with", "", " please"),
        ("ends_with", "please ", ""),
        ("contains", "please ", " now"),
    ],
)
@pytest.mark.parametrize(
    "trigger,utterance",
    [
        ("turn on study lights", "turn on studio lights"),
        ("turn on office lights", "turn on offsite lights"),
        ("turn on study lights", "turn off study lights"),
        ("turn on study lights", "turn on stuy lights"),
    ],
)
async def test_legacy_fuzzy_never_substitutes_target_or_action(
    hass, behavior, match_type, prefix, suffix, trigger, utterance
):
    legacy = {**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 70}
    rule = local_rule(
        phrases=[trigger], match_type=match_type, behavior=behavior, matching=legacy
    )
    manager = RequestRules(MemoryStore({"defaults": legacy, "rules": [rule]}))
    await manager.async_initialize()
    services = FakeServices()
    hass.services = services
    assert (
        await async_evaluate_rule(
            hass, manager, RequestRuleRuntime(), prefix + utterance + suffix, "session"
        )
        is None
    )
    assert services.calls == []
    outcome = await async_evaluate_rule(
        hass, manager, RequestRuleRuntime(), prefix + trigger + suffix, "session"
    )
    assert outcome is not None and outcome.consume
    assert len(services.calls) == 1


async def test_legacy_load_backup_import_export_and_reopen_preserve_rule():
    legacy = {**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 83}
    rule = local_rule(
        phrases=["turn on study lights", "study lighting"],
        behavior="custom",
        matching=legacy,
    )
    rule["continue_matching"] = True
    rule["conditions"] = [
        {"condition": "state", "entity_id": "light.study", "state": "off"}
    ]
    original = {"defaults": legacy, "rules": [rule], "future_metadata": {"keep": True}}
    store = MemoryStore(original)
    manager = RequestRules(store)
    await manager.async_initialize()
    expected = validate_rule({**rule, "matching": DEFAULT_MATCHING})
    assert manager.snapshot()["rules"] == [expected]
    assert store.data["future_metadata"] == original["future_metadata"]
    assert store.data["defaults"] == DEFAULT_MATCHING
    assert store.data["rules"][0]["matching"] == DEFAULT_MATCHING
    assert store.saves == 1
    reloaded = RequestRules(store)
    await reloaded.async_initialize()
    assert reloaded.snapshot() == manager.snapshot()
    assert store.saves == 1

    # Import a pre-retirement backup directly, rather than only its new export.
    imported = RequestRules.validate_backup_data(
        {"storage_version": 4, "defaults": legacy, "rules": [rule]}
    )
    assert imported["rules"] == [expected]
    assert imported["defaults"] == DEFAULT_MATCHING
    backup = await manager.async_backup_data()
    assert RequestRules.validate_backup_data(backup)["rules"] == [expected]
    pack = export_rule_pack(manager, "all", reviewed=True)
    old_pack = deepcopy(pack)
    old_pack["rules"][0]["matching"] = legacy
    prepared = validate_rule_pack(old_pack)
    assert prepared["rules"][0]["matching"] == DEFAULT_MATCHING
    assert pack["rules"][0]["matching"] == DEFAULT_MATCHING


async def test_legacy_defaults_only_migration_is_persisted_once():
    store = MemoryStore(
        {
            "defaults": {**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 90},
            "rules": [],
        }
    )
    manager = RequestRules(store)
    await manager.async_initialize()
    assert store.data["defaults"] == DEFAULT_MATCHING
    assert store.saves == 1


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
async def test_old_store_versions_normalize_legacy_settings_after_envelope_migration(
    version,
):
    legacy = {**DEFAULT_MATCHING, "fuzzy": True, "fuzzy_threshold": 90}
    raw = {"defaults": legacy, "rules": [local_rule(matching=legacy)]}
    # Published 6.8.3 uses v4; the existing envelope migration is sufficient.
    migrated = await RequestRuleStore._async_migrate_func(None, version, 0, raw)
    store = MemoryStore(migrated)
    manager = RequestRules(store)
    await manager.async_initialize()
    assert manager.match("good night") is not None
    assert manager.snapshot()["defaults"] == DEFAULT_MATCHING
    assert manager.snapshot()["rules"][0]["matching"] == DEFAULT_MATCHING
