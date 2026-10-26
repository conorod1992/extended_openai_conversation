"""Core stores and fresh disk readers share the per-instance test directory."""

import json
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.helpers.storage import Store
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
