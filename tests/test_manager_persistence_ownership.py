"""Manager-owned initialization, durable rollback, and stable method identity."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    guest_mode,
    knowledge,
    memory,
    persistence_hardening,
    request_rules,
    runtime_hardening,
    skills,
)
from tests.test_persistence_cancellation_hardening import (
    BlockingStorage,
    _create_manager,
    _mutate,
    _snapshot,
)

DURABLE = {
    "memory": memory.PersistentMemory,
    "knowledge": knowledge.KnowledgeLibrary,
    "request_rules": request_rules.RequestRules,
}
OWNERS = {
    **{cls: ("async_initialize", "_async_save_locked") for cls in DURABLE.values()},
    guest_mode.GuestModeManager: ("async_initialize",),
    skills.SkillManager: (
        "async_initialize",
        "async_load_skills",
        "async_get_instance",
        "get_loaded_instance",
    ),
}


def owner_methods():
    return {
        (cls, name): cls.__dict__[name]
        for cls, names in OWNERS.items()
        for name in names
    }


@pytest.mark.parametrize("kind", DURABLE)
@pytest.mark.parametrize("fail_first", [False, True])
async def test_concurrent_first_initialize_loads_once_or_retries_failure(
    kind, fail_first
):
    store = BlockingStorage()
    manager = DURABLE[kind](store)
    entered, release = asyncio.Event(), asyncio.Event()
    loads = 0

    async def load():
        nonlocal loads
        loads += 1
        if loads == 1:
            entered.set()
            await release.wait()
            if fail_first:
                raise OSError("transient load")
        return None

    store.async_load = load
    first = asyncio.create_task(manager.async_initialize())
    await entered.wait()
    second = asyncio.create_task(manager.async_initialize())
    await asyncio.sleep(0)
    assert loads == 1
    assert not manager._initialized
    release.set()
    if fail_first:
        with pytest.raises(OSError, match="transient load"):
            await first
    else:
        await first
    await second
    await manager.async_initialize()
    assert loads == (2 if fail_first else 1)
    assert manager._initialized


@pytest.mark.parametrize("kind", DURABLE)
async def test_committed_baseline_survives_idempotent_init_and_restart(kind):
    store = BlockingStorage()
    manager = await _create_manager(kind, store)
    await _mutate(kind, manager, "first")
    committed = _snapshot(kind, manager)
    # Repeated initialization is a true no-op, including committed bookkeeping.
    baseline = manager._committed_state
    await manager.async_initialize()
    assert manager._committed_state is baseline
    store.fail_immediately()
    with pytest.raises(RuntimeError, match="simulated Store failure"):
        await _mutate(kind, manager, "second")
    assert _snapshot(kind, manager) == committed
    store.fail_saves = False
    restarted = await _create_manager(kind, store)
    assert _snapshot(kind, restarted) == committed
    await _mutate(kind, restarted, "second")
    next_committed = _snapshot(kind, restarted)
    assert next_committed != committed
    store.fail_immediately()
    with pytest.raises(RuntimeError):
        await _mutate(kind, restarted, "first" if kind == "request_rules" else "third")
    assert _snapshot(kind, restarted) == next_committed


@pytest.mark.parametrize("kind", ["memory", "request_rules"])
@pytest.mark.parametrize("error", [OSError, asyncio.CancelledError])
async def test_failed_initialization_repair_clears_partial_state_and_retries(
    kind, error
):
    store = BlockingStorage()
    seeded = await _create_manager(kind, store)
    await _mutate(kind, seeded, "first")
    expected = _snapshot(kind, seeded)
    if kind == "memory":
        store.data["memories"].append({"invalid": True})
    else:
        store.data["rules"].append({"invalid": True})
    save = store.async_save
    store.async_save = AsyncMock(side_effect=error("repair failed"))
    manager = DURABLE[kind](store)
    with pytest.raises(error):
        await manager.async_initialize()
    assert not manager._initialized
    assert manager._committed_state is None
    if kind == "memory":
        assert manager._memories == manager._token_index == manager._key_index == {}
        assert manager._embedding_cache == {}
        assert not manager._embedding_cache_dirty
    else:
        assert manager._rules == []
        assert manager._defaults == request_rules.DEFAULT_MATCHING
    store.async_save = save
    await manager.async_initialize()
    assert _snapshot(kind, manager) == expected


async def test_knowledge_partial_index_failure_resets_before_retry(monkeypatch):
    store = BlockingStorage()
    seed = await _create_manager("knowledge", store)
    await _mutate("knowledge", seed, "first")
    manager = knowledge.KnowledgeLibrary(store)
    index = manager._index
    monkeypatch.setattr(
        manager,
        "_index",
        lambda source: (_ for _ in ()).throw(RuntimeError("index failed")),
    )
    with pytest.raises(RuntimeError, match="index failed"):
        await manager.async_initialize()
    assert manager._sources == manager._chunks == manager._token_index == {}
    assert not manager._initialized
    assert manager._committed_state is None
    monkeypatch.setattr(manager, "_index", index)
    await manager.async_initialize()
    assert _snapshot("knowledge", manager) == _snapshot("knowledge", seed)


async def test_guest_concurrent_initialize_waits_for_retry(hass):
    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    entered, release = asyncio.Event(), asyncio.Event()
    loads = 0

    async def load():
        nonlocal loads
        loads += 1
        if loads == 1:
            entered.set()
            await release.wait()
            raise OSError("load failed")
        return {
            "schedule": {
                "active_from": "2026-09-19T12:00:00+00:00",
                "active_until": None,
            }
        }

    manager._store = type("Store", (), {"async_load": staticmethod(load)})()
    first = asyncio.create_task(manager.async_initialize())
    await entered.wait()
    second = asyncio.create_task(manager.async_initialize())
    await asyncio.sleep(0)
    assert loads == 1
    release.set()
    with pytest.raises(OSError):
        await first
    await second
    await manager.async_initialize()
    assert loads == 2
    assert manager.schedule.active_from == "2026-09-19T12:00:00+00:00"


async def test_repeated_startup_keeps_manager_method_identity(hass, monkeypatch):
    before = owner_methods()
    # Its downstream tool-assembly cache is outside manager ownership and would
    # otherwise leak into unrelated tests constructing incomplete conversation entities.
    monkeypatch.setattr(integration, "install_guest_policy_fast_path", Mock())
    hass.http.async_register_static_paths = AsyncMock()
    # Exercise real synchronous startup installers; isolate external setup I/O.
    for name in (
        "async_setup_model_catalog",
        "async_get_quiet_hours",
        "async_setup_delayed_tools",
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_management_ui",
        "async_setup_debug_ui",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())
    for _ in range(2):
        assert await integration.async_setup(hass, {})
        assert owner_methods() == before
    for (cls, name), descriptor in before.items():
        method = (
            descriptor.__func__ if isinstance(descriptor, classmethod) else descriptor
        )
        assert method.__module__ == cls.__module__
        assert method.__qualname__ == f"{cls.__name__}.{name}"
        assert not hasattr(method, "__wrapped__")


def test_no_manager_init_or_persistence_reassignment():
    protected = {cls.__name__: set(names) for cls, names in OWNERS.items()}
    violations = []
    for path in Path(integration.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = dict(protected)
        # Include direct aliases of manager classes and class-valued parameters.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = aliases[node.value.id]
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                if isinstance(node.value, ast.Name) and node.attr in aliases.get(
                    node.value.id, set()
                ):
                    violations.append((path.name, node.lineno))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"setattr", "delattr"}
                and len(node.args) > 1
            ):
                target, attr = node.args[:2]
                if (
                    isinstance(target, ast.Name)
                    and isinstance(attr, ast.Constant)
                    and attr.value in aliases.get(target.id, set())
                ):
                    violations.append((path.name, node.lineno))
    assert violations == []
    for module in (persistence_hardening, runtime_hardening):
        assert not hasattr(module, "_INSTALLED")
    assert not hasattr(persistence_hardening, "install_persistence_transactions")
    assert not hasattr(persistence_hardening, "_install_manager_guard")
    assert not hasattr(runtime_hardening, "install_runtime_hardening")
