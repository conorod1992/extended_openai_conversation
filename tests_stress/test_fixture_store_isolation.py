"""Apply the physical-store contract with stress-suite fixture discovery."""

from pathlib import Path

from homeassistant.helpers.storage import Store
from tests_real_ha.test_fixture_store_isolation import (  # noqa: F401
    test_core_config_store_and_fresh_reader_share_physical_path,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def test_normal_storage_cases_retain_the_in_memory_fixture(hass, hass_storage):
    """The native opt-in must not silently turn all stress tests into disk tests."""
    store = Store(hass, 1, "fixture-memory-control")
    await store.async_save({"marker": "in-memory"})
    assert hass_storage[store.key]["data"] == {"marker": "in-memory"}
    assert not Path(store.path).exists()
    assert await Store(hass, 1, store.key).async_load() == {"marker": "in-memory"}
