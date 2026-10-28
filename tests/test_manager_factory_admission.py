"""Cold public factory admission precedes private initialization locks."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation_archive as archive,
    guest_mode,
    usage,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.fixture
def factory_probe(monkeypatch):
    def create(kind):
        hass = SimpleNamespace(
            data={}, config_entries=SimpleNamespace(async_get_entry=lambda _: None)
        )
        gate = get_agent_maintenance_gate(hass, "entry", "agent")
        entered = asyncio.Event()
        release = asyncio.Event()
        reads = []

        class Store:
            def __init__(self, *args, **kwargs):
                pass

            def bind_agent(self, *args):
                return self

            async def async_load(self):
                async with gate.shared(maintenance=True):
                    reads.append("load")
                    if len(reads) == 1:
                        entered.set()
                        await release.wait()
                    return {}

            async def async_load_metadata(self):
                await self.async_load()
                return {"partitions": ["2026-01"]}

            async def async_load_partition(self, _):
                return await self.async_load()

        monkeypatch.setattr(archive, "HomeAssistantArchiveStorage", Store)
        monkeypatch.setattr(usage, "_UsageStore", Store)
        monkeypatch.setattr(usage, "_register_usage_maintenance", lambda *_: None)
        monkeypatch.setattr(guest_mode, "PropagatingWriteStore", Store)
        getter = {
            "archive": archive.async_get_archive,
            "usage": usage.async_get_usage,
            "durable": usage.async_get_durable_usage,
            "guest": guest_mode.async_get_guest_mode,
        }[kind]
        return (
            hass,
            gate,
            entered,
            release,
            reads,
            lambda: getter(hass, "entry", "agent"),
        )

    return create


@pytest.mark.parametrize("kind", ["archive", "usage", "durable", "guest"])
async def test_cold_factory_and_export_complete(factory_probe, kind):
    _hass, gate, entered, release, reads, get = factory_probe(kind)
    first = asyncio.create_task(get())
    await entered.wait()

    async def export():
        async with gate.exclusive():
            return await get()

    second = asyncio.create_task(export())
    await asyncio.sleep(0)
    release.set()
    try:
        one, two = await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert one is two
        assert one._initialized
        assert reads
    finally:
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.parametrize("kind", ["archive", "usage", "durable", "guest"])
@pytest.mark.parametrize("state", ["deleted", "recovery_required"])
async def test_admission_denial_is_not_usage_fallback(factory_probe, kind, state):
    hass, gate, _entered, _release, reads, get = factory_probe(kind)
    setattr(gate, state, True)
    with pytest.raises(HomeAssistantError):
        await get()
    assert reads == []
    assert not hass.data.get(usage._VOLATILE_USAGE_MANAGERS)


@pytest.mark.parametrize("kind", ["archive", "usage", "durable", "guest"])
async def test_cancelled_initialization_releases_factory_admission(factory_probe, kind):
    _hass, gate, entered, release, _reads, get = factory_probe(kind)
    task = asyncio.create_task(get())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()

    async def recovery():
        async with gate.exclusive():
            gate.recovery_required = True
            with gate.recovery_work():
                return await get()

    manager = await asyncio.wait_for(recovery(), 1)
    assert manager._initialized


@pytest.mark.parametrize("kind", ["archive", "usage", "durable", "guest"])
async def test_exclusive_owner_can_initialize_a_waiting_cold_getter(
    factory_probe, kind
):
    from contextvars import Context

    _hass, gate, _entered, release, _reads, get = factory_probe(kind)
    cold = None
    try:
        async with gate.exclusive():
            cold = asyncio.create_task(get(), context=Context())
            await asyncio.sleep(0)
            release.set()
            owned = await asyncio.wait_for(get(), 0.5)
        assert await asyncio.wait_for(cold, 0.5) is owned
    finally:
        if cold:
            cold.cancel()
            await asyncio.gather(cold, return_exceptions=True)


@pytest.mark.parametrize("kind", ["archive", "usage", "durable", "guest"])
async def test_factory_order_oracle_detects_removed_admission(
    factory_probe, monkeypatch, kind
):
    import ast
    import inspect

    module, name = {
        "archive": (archive, "async_get_archive"),
        "usage": (usage, "async_get_usage"),
        "durable": (usage, "async_get_durable_usage"),
        "guest": (guest_mode, "async_get_guest_mode"),
    }[kind]
    tree = ast.parse(inspect.getsource(getattr(module, name)))
    function = tree.body[0]
    assert isinstance(function.body[-1], ast.AsyncWith)
    function.body = [*function.body[:-1], *function.body[-1].body]
    namespace = dict(vars(module))
    exec(
        compile(ast.fix_missing_locations(tree), "<removed-factory-admission>", "exec"),
        namespace,
    )
    monkeypatch.setattr(module, name, namespace[name])

    def mutated_probe(selected):
        probe = factory_probe(selected)
        namespace.update(vars(module))
        return probe

    with pytest.raises(TimeoutError):
        await test_exclusive_owner_can_initialize_a_waiting_cold_getter(
            mutated_probe, kind
        )
