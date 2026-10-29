"""Cold persisted state and Group-only quarantine regression boundaries."""

import asyncio
from copy import deepcopy
import threading

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import memory, services
from tests.test_management_request_boundaries import _tool
from tests.test_memory import FakeStorage


async def test_catalogue_accepts_recoverable_null_groups(hass):
    from tests.test_management_loading_performance import (
        _hass_with_agent,
        _persisted_invalid_function_tools,
    )
    from custom_components.extended_openai_conversation_responses.management_loading_performance import (
        async_agent_catalog,
    )

    fake, entry, subentry = _hass_with_agent()
    subentry.data = {
        **subentry.data,
        "functions": _persisted_invalid_function_tools(),
        "function_groups": None,
    }
    result = await async_agent_catalog(fake, "admin", True)
    assert result["agents"][0]["function_group_count"] == 0
    assert subentry.data["function_groups"] is None


@pytest.mark.parametrize("kind", ["tools", "groups"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_toggles_preserve_group_only_damage(hass, monkeypatch, kind, enabled):
    from tests.test_save_validation_reuse import _hass_with_agent

    fake, entry, subentry = _hass_with_agent()
    healthy = _tool()
    healthy["enabled"] = not enabled
    group = {
        "id": "healthy-group",
        "name": "Healthy",
        "description": "Healthy",
        "loading_mode": "always",
        "enabled": not enabled,
        "functions": ["healthy"],
    }
    broken = {
        **group,
        "id": "damaged",
        "name": "Damaged",
        "enabled": False,
        "functions": ["removed-old-tool"],
    }
    subentry.data = {
        **subentry.data,
        "functions": yaml.safe_dump([healthy]),
        "function_groups": [group, broken],
    }
    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda *_: (entry.entry_id, subentry.subentry_id),
    )
    action = (
        services.async_set_function_tools_enabled
        if kind == "tools"
        else services.async_set_function_groups_enabled
    )
    await action(
        fake,
        entry.entry_id,
        subentry.subentry_id,
        ["healthy" if kind == "tools" else "healthy-group"],
        enabled,
    )
    assert subentry.data["function_groups"][1] == broken
    if kind == "tools":
        assert yaml.safe_load(subentry.data["functions"])[0]["enabled"] is enabled
    else:
        assert subentry.data["function_groups"][0]["enabled"] is enabled


def records(count):
    return [
        {
            "memory_id": f"memory-{i}",
            "user_id": "user:admin",
            "content": "Prefers blue rooms. " * 30,
            "category": "Preferences",
            "source": "explicit",
            "created_at": "2026-10-20T12:00:00+00:00",
            "updated_at": "2026-10-20T12:00:00+00:00",
            "key": f"key-{i}",
        }
        for i in range(count)
    ]


@pytest.mark.parametrize("count", [1, 500])
async def test_memory_cold_preparation_uses_worker(monkeypatch, count):
    main = threading.get_ident()
    calls, vectors = [], []
    validate, clean = memory._validate_persistent_memory_record, memory._clean_embedding

    def check(raw):
        calls.append(threading.get_ident())
        return validate(raw)

    def vector(raw):
        vectors.append(threading.get_ident())
        return clean(raw)

    monkeypatch.setattr(memory, "_validate_persistent_memory_record", check)
    monkeypatch.setattr(memory, "_clean_embedding", vector)
    raw = records(count)
    storage = FakeStorage({"memories": [*raw, deepcopy(raw[0]), {"bad": True}]})
    cache = FakeStorage(
        {
            "embeddings": {
                r["memory_id"]: {
                    "model": "default",
                    "fingerprint": "test",
                    "vector": [0.5] * 128,
                }
                for r in raw
            }
        }
    )
    manager = memory.PersistentMemory(storage, embedding_cache_storage=cache)
    await manager.async_initialize()
    assert len(manager._memories) == count
    assert len(manager._key_index) == count
    assert len(manager._embedding_cache) == count
    assert len(storage.data["memories"]) == count + 2
    assert calls and main not in calls
    assert vectors and main not in vectors
    await manager.async_initialize()
    assert len(vectors) == count


async def test_first_assist_prepares_yaml_before_guest_lookup(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import agent_config
    from tests.test_conversation_orchestration import _assist_fixture

    entity, invoke, _logs, _seen = _assist_fixture(monkeypatch)
    entity.hass.async_add_executor_job = asyncio.to_thread
    entity.subentry.data["functions"] = yaml.safe_dump([_tool("runtime_cold_unique")])
    entity.subentry.data["max_function_calls_per_conversation"] = 0
    entity._configured_function_tools_from_data.side_effect = (
        agent_config.configured_function_tools_from_data
    )
    policy = entity._resolve_live_guest_policy.return_value

    def resolve():
        assert (
            entity._get_configured_function_tools()[0]["spec"]["name"]
            == "runtime_cold_unique"
        )
        return policy

    entity._resolve_live_guest_policy.side_effect = resolve
    agent_config._cached_configured_tools.cache_clear()
    if hasattr(agent_config, "_decoded_function_tools"):
        agent_config._decoded_function_tools.cache_clear()
    raw = entity.subentry.data["functions"]
    main, calls = threading.get_ident(), []
    original = yaml.safe_load

    def load(value):
        if value == raw:
            calls.append(threading.get_ident())
        return original(value)

    monkeypatch.setattr(yaml, "safe_load", load)
    await invoke()
    await invoke()
    assert len(calls) == 1
    assert main not in calls


async def test_current_startup_migration_decodes_in_worker(hass, monkeypatch):
    from types import SimpleNamespace
    from custom_components.extended_openai_conversation_responses import (
        async_migrate_integration,
    )
    from custom_components.extended_openai_conversation_responses.const import (
        CONFIG_ENTRY_VERSION,
    )

    raw = yaml.safe_dump([_tool("migration_unique")])
    subentry = SimpleNamespace(data={"functions": raw}, subentry_type="conversation")
    entry = SimpleNamespace(
        version=CONFIG_ENTRY_VERSION, disabled_by=None, subentries={"agent": subentry}
    )
    fake = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _: [entry]),
        async_add_executor_job=asyncio.to_thread,
    )
    main, calls = threading.get_ident(), []
    original = yaml.safe_load

    def load(value):
        if value == raw:
            calls.append(threading.get_ident())
        return original(value)

    monkeypatch.setattr(yaml, "safe_load", load)
    await async_migrate_integration(fake)
    assert calls and main not in calls
    assert subentry.data["functions"] == raw


async def test_memory_failed_self_heal_does_not_publish_partial_state():
    legacy = records(2)
    legacy[0]["importance"] = "normal"
    storage = FakeStorage({"memories": legacy})
    manager = memory.PersistentMemory(storage)
    save = storage.async_save

    async def fail(_data):
        raise OSError("disk unavailable")

    storage.async_save = fail
    with pytest.raises(OSError, match="disk unavailable"):
        await manager.async_initialize()
    assert not manager.initialized
    assert not manager._memories and not manager._key_index
    storage.async_save = save
    await manager.async_initialize()
    assert manager.initialized and len(manager._memories) == 2


async def test_startup_worker_does_not_overwrite_a_newer_configuration(
    hass, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import custom_components.extended_openai_conversation_responses as integration
    from custom_components.extended_openai_conversation_responses.const import (
        CONFIG_ENTRY_VERSION,
    )

    entered, release = threading.Event(), threading.Event()
    original = {"functions": yaml.safe_dump([_tool("concurrent_migration")])}
    subentry = SimpleNamespace(data=original, subentry_type="conversation")
    entry = SimpleNamespace(
        version=CONFIG_ENTRY_VERSION, disabled_by=None, subentries={"agent": subentry}
    )
    update = Mock()
    fake = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda _: [entry], async_update_subentry=update
        )
    )

    def migrate(data, kind):
        data["migration_marker"] = "stale candidate"
        entered.set()
        assert release.wait(10)
        return True

    monkeypatch.setattr(integration, "_migrate_saved_agent_defaults", migrate)
    task = asyncio.create_task(integration.async_migrate_integration(fake))
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        replacement = {**original, "prompt": "A newer saved prompt"}
        subentry.data = replacement
    finally:
        release.set()
        await task
    update.assert_not_called()
    assert subentry.data is replacement
