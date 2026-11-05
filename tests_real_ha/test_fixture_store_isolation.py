"""Core stores and fresh disk readers share the per-instance test directory."""

import asyncio
import errno
import json
from pathlib import Path
import threading

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.strict_store import (
    PropagatingWriteStore,
)
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from tests.ha_store_test_helpers import (
    invalidate_store_cache,
    prepared_disk_write_boundary,
)
from tests_real_ha.native_storage import hass_storage  # noqa: F401
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.usefixtures("real_store_io")
async def test_core_config_store_and_fresh_reader_share_physical_path(hass, tmp_path):
    """Early-created Core storage must persist where restore verification reads."""
    store = hass.config_entries._store
    path = tmp_path / ".storage" / "core.config_entries"
    assert Path(hass.config.config_dir) == tmp_path
    assert Path(store.path) == path

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fixture storage owner",
        data={"fixture_marker": "entry-data"},
        subentries_data=[
            {
                "subentry_type": "conversation",
                "title": "Fixture storage subentry",
                "data": {"fixture_marker": "subentry-data"},
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    await store.async_save(hass.config_entries._data_to_save())

    # Read the actual file independently of Store's pending-data/cache paths.
    envelope = json.loads(await hass.async_add_executor_job(path.read_bytes))
    assert envelope["key"] == store.key == "core.config_entries"
    assert envelope["version"] == store.version
    persisted_entry = next(
        item
        for item in envelope["data"]["entries"]
        if item["entry_id"] == entry.entry_id
    )
    assert persisted_entry["title"] == entry.title
    assert persisted_entry["data"] == {"fixture_marker": "entry-data"}
    assert persisted_entry["subentries"] == [
        subentry.as_dict() for subentry in entry.subentries.values()
    ]

    verifier = Store(hass, store.version, store.key, minor_version=store.minor_version)
    assert Path(verifier.path) == path
    assert await verifier.async_load() == envelope["data"]

    await store.async_remove()
    with pytest.raises(FileNotFoundError):
        await hass.async_add_executor_job(path.read_bytes)
    after_removal = Store(
        hass, store.version, store.key, minor_version=store.minor_version
    )
    assert await after_removal.async_load() is None


@pytest.mark.parametrize("serialize_in_event_loop", [False, True])
@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("after_write", [False, True])
@pytest.mark.usefixtures("real_store_io")
async def test_prepared_write_fault_preserves_native_disk_semantics(
    hass, monkeypatch, serialize_in_event_loop, atomic, after_write
):
    """Failure timing is proved by physical bytes and a fresh native reader."""
    store = PropagatingWriteStore(
        hass,
        1,
        "prepared-fault",
        atomic_writes=atomic,
        serialize_in_event_loop=serialize_in_event_loop,
    )
    sibling = PropagatingWriteStore(hass, 1, "unrelated-writer")
    await store.async_save({"marker": "original"})
    await sibling.async_save({"marker": "sibling"})
    path = Path(store.path)
    original = path.read_bytes()
    writer, name = prepared_disk_write_boundary(store)
    native = getattr(writer, name)
    reached = []

    def fail(mode, data):
        envelope = json.loads(data)
        assert envelope["key"] == store.key
        assert envelope["version"] == 1
        reached.append(envelope["data"])
        if after_write:
            native(mode, data)
        raise WriteError("Injected disk failure") from OSError(errno.EIO, "injected")

    with monkeypatch.context() as fault:
        fault.setattr(writer, name, fail)
        await sibling.async_save({"marker": "unaffected"})
        with pytest.raises(OSError, match="Private storage write failed") as error:
            await store.async_save({"marker": "attempted λ"})
        assert error.value.errno == errno.EIO
    assert reached == [{"marker": "attempted λ"}]
    expected = {"marker": "attempted λ" if after_write else "original"}
    assert json.loads(path.read_bytes())["data"] == expected
    assert (path.read_bytes() == original) is (not after_write)
    assert json.loads(Path(sibling.path).read_bytes())["data"] == {
        "marker": "unaffected"
    }
    assert await PropagatingWriteStore(hass, 1, store.key).async_load() == expected
    await store.async_save({"marker": "recovered"})
    assert await PropagatingWriteStore(hass, 1, store.key).async_load() == {
        "marker": "recovered"
    }


@pytest.mark.parametrize("serialize_in_event_loop", [False, True])
@pytest.mark.usefixtures("real_store_io")
async def test_prepared_write_gate_waits_for_native_disk_settlement(
    hass, monkeypatch, serialize_in_event_loop
):
    """Holding the actual writer cannot acknowledge or change its file early."""
    store = PropagatingWriteStore(
        hass,
        1,
        "prepared-held",
        atomic_writes=True,
        serialize_in_event_loop=serialize_in_event_loop,
    )
    sibling = PropagatingWriteStore(hass, 1, "prepared-held-sibling")
    await store.async_save({"marker": "original"})
    path = Path(store.path)
    original = path.read_bytes()
    writer, name = prepared_disk_write_boundary(store)
    native = getattr(writer, name)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    reached = []

    def held(mode, data):
        reached.append(json.loads(data)["data"])
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10), "Native write was not released"
        return native(mode, data)

    with monkeypatch.context() as fault:
        fault.setattr(writer, name, held)
        task = asyncio.create_task(store.async_save({"marker": "released"}))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert not task.done()
            assert path.read_bytes() == original
            await sibling.async_save({"marker": "unaffected"})
            assert json.loads(Path(sibling.path).read_bytes())["data"] == {
                "marker": "unaffected"
            }
            release.set()
            await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert reached == [{"marker": "released"}]
    assert json.loads(path.read_bytes())["data"] == {"marker": "released"}
    assert await PropagatingWriteStore(hass, 1, store.key).async_load() == {
        "marker": "released"
    }
    await store.async_save({"marker": "next"})
    assert json.loads(path.read_bytes())["data"] == {"marker": "next"}


@pytest.mark.usefixtures("real_store_io")
async def test_store_cache_invalidation_uses_native_owner_and_only_target_key(hass):
    """A primed native cache must not hide the read fault's physical boundary."""
    from homeassistant.helpers.storage import get_internal_store_manager

    target = Store(hass, 1, "cache-fault-target")
    sibling = Store(hass, 1, "cache-fault-sibling")

    def seed(store, marker):
        path = Path(store.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "key": store.key,
                    "version": 1,
                    "minor_version": 1,
                    "data": {"marker": marker},
                }
            ),
            encoding="utf-8",
        )

    seed(target, "cached")
    seed(sibling, "sibling-cached")
    cache = get_internal_store_manager(hass)
    await cache.async_initialize()
    await cache.async_preload([target.key, sibling.key])
    cached = cache.async_fetch(target.key)
    assert cached and cached[0] and cached[1]["data"] == {"marker": "cached"}
    await cache.async_preload([target.key])
    seed(target, "disk")
    seed(sibling, "sibling-disk")
    invalidate_store_cache(target)
    assert await Store(hass, 1, target.key).async_load() == {"marker": "disk"}
    assert await Store(hass, 1, sibling.key).async_load() == {
        "marker": "sibling-cached"
    }
