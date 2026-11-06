"""Configured assistants remain navigable despite optional metadata failures."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance as loading,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_management_loading_performance import _hass_with_agent


@pytest.mark.parametrize(
    "failure",
    [
        "groups",
        "knowledge",
        "knowledge-read",
        "guest",
        "guest-uninitialized",
        "unexpected",
    ],
)
async def test_catalogue_retains_unavailable_assistant_and_healthy_sibling(
    monkeypatch, failure
):
    hass, entry, broken = _hass_with_agent()
    healthy = SimpleNamespace(
        subentry_id="healthy",
        subentry_type="conversation",
        title="Healthy",
        data=deepcopy(broken.data),
    )
    entry.subentries[healthy.subentry_id] = healthy
    if failure == "groups":
        broken.data["function_groups"] = 42
    saved = deepcopy(broken.data)
    source_count = Mock(
        side_effect=RuntimeError("Must not read uninitialized Knowledge")
    )

    class Library:
        initialized = failure == "knowledge-read"

        @property
        def source_count(self):
            if self.initialized:
                raise HomeAssistantError("storage unavailable")
            return source_count()

    monkeypatch.setattr(
        loading,
        "get_loaded_knowledge",
        lambda _h, _e, agent: (
            Library()
            if agent == broken.subentry_id and failure.startswith("knowledge")
            else None
        ),
    )
    guest = SimpleNamespace(
        initialized=failure != "guest-uninitialized",
        status=Mock(side_effect=HomeAssistantError("persistence unavailable")),
    )
    monkeypatch.setattr(
        loading,
        "get_loaded_guest_mode",
        lambda _h, _e, agent: (
            guest
            if agent == broken.subentry_id and failure.startswith("guest")
            else None
        ),
    )
    original = loading._agent_snapshot
    if failure == "unexpected":

        def snapshot(hass, entry, subentry, **kwargs):
            if subentry is broken:
                raise RuntimeError("private backend failure")
            return original(hass, entry, subentry, **kwargs)

        monkeypatch.setattr(loading, "_agent_snapshot", snapshot)
    result = await loading.async_agent_catalog(hass, "admin", True)
    damaged, sibling = result["agents"]
    assert damaged["subentry_id"] == broken.subentry_id
    assert damaged["metadata_errors"]
    assert sibling["subentry_id"] == "healthy"
    assert sibling["metadata_errors"] == []
    assert broken.data == saved
    source_count.assert_not_called()
    if failure.startswith("knowledge"):
        assert damaged["knowledge_source_count"] is None
        assert damaged["feature_status"]["knowledge"]["state"] == "unavailable"
        assert not damaged["feature_status"]["knowledge"]["available"]
    if failure == "groups":
        assert damaged["function_group_count"] is None
    if failure.startswith("guest"):
        assert damaged["guest_mode"]["state"] == "unavailable"
        assert damaged["guest_mode"]["currently_active"] is None
        if failure == "guest-uninitialized":
            guest.status.assert_not_called()


async def test_catalogue_does_not_swallow_cancellation(monkeypatch):
    hass, _, _ = _hass_with_agent()
    monkeypatch.setattr(
        loading, "_agent_snapshot", Mock(side_effect=asyncio.CancelledError)
    )
    with pytest.raises(asyncio.CancelledError):
        await loading.async_agent_catalog(hass, "admin", True)


@pytest.mark.parametrize("subsystem", ["knowledge", "guest"])
async def test_failed_native_store_initialization_cannot_block_catalogue(
    tmp_path, monkeypatch, subsystem
):
    from pathlib import Path

    from custom_components.extended_openai_conversation_responses import (
        guest_mode,
        knowledge,
    )
    from homeassistant.core import HomeAssistant

    hass = HomeAssistant(str(tmp_path))
    _, entry, broken = _hass_with_agent()
    healthy = SimpleNamespace(
        subentry_id="healthy",
        subentry_type="conversation",
        title="Healthy",
        data=deepcopy(broken.data),
    )
    entry.subentries[healthy.subentry_id] = healthy
    hass.config_entries = SimpleNamespace(
        async_entries=lambda _domain: [entry],
        async_get_entry=lambda _id: entry,
        async_shutdown=AsyncMock(),
    )
    try:
        if subsystem == "knowledge":
            storage = knowledge.HomeAssistantKnowledgeStorage(
                hass, entry.entry_id, broken.subentry_id
            )
            await storage._store.async_save({"sources": 42})
            with pytest.raises(ValueError, match="sources"):
                await knowledge.async_get_knowledge(
                    hass, entry.entry_id, broken.subentry_id
                )
            assert not knowledge.get_loaded_knowledge(
                hass, entry.entry_id, broken.subentry_id
            ).initialized
        else:
            manager = guest_mode.GuestModeManager(
                hass, entry.entry_id, broken.subentry_id
            )
            await manager._store.async_save({"schedule": {"active_from": "not-a-date"}})
            with pytest.raises(ValueError):
                await guest_mode.async_get_guest_mode(
                    hass, entry.entry_id, broken.subentry_id
                )
        before = {
            path: path.read_bytes()
            for path in Path(hass.config.path(".storage")).iterdir()
            if path.is_file()
        }
        result = await loading.async_agent_catalog(hass, "admin", True)
        assert [row["subentry_id"] for row in result["agents"]] == [
            broken.subentry_id,
            "healthy",
        ]
        assert result["agents"][0]["metadata_errors"][0]["key"] == (
            "guest_mode" if subsystem == "guest" else subsystem
        )
        assert result["agents"][1]["metadata_errors"] == []
        assert all(path.read_bytes() == content for path, content in before.items())
        # An explicit storage repair must also clear prior catalogue warnings.
        if subsystem == "knowledge":
            await storage._store.async_save({"sources": []})
            await knowledge.async_get_knowledge(
                hass, entry.entry_id, broken.subentry_id
            )
        else:
            await manager._store.async_save({"schedule": None})
            await guest_mode.async_get_guest_mode(
                hass, entry.entry_id, broken.subentry_id
            )
        recovered = await loading.async_agent_catalog(hass, "admin", True)
        assert recovered["agents"][0]["metadata_errors"] == []
    finally:
        await hass.async_stop(force=True)


async def test_private_restore_of_quarantined_groups_retains_catalogue_repair_access(
    monkeypatch,
):
    from custom_components.extended_openai_conversation_responses import (
        backup,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.management_function_repair import (
        group_collection_repair_metadata,
    )
    from tests.test_backup import _document
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    document = _document()
    document["agent"]["config"]["function_groups"] = 42
    artifact = backup.finalize_backup_snapshot(document)["document"]
    await transfer.async_restore_transfer(
        tx.hass,
        tx.entry,
        tx.subentry,
        artifact,
        sections=[transfer.SECTION_CONFIGURATION],
    )
    assert tx.subentry.data["function_groups"] == 42
    assert [record["phase"] for record in tx.journal.saved] == ["applying", "committed"]
    tx.entry.title, tx.entry.data = "Provider", {}
    tx.subentry.subentry_type = "conversation"
    sibling = SimpleNamespace(
        subentry_id="healthy", subentry_type="conversation", title="Healthy", data={}
    )
    tx.entry.subentries = {tx.subentry.subentry_id: tx.subentry, "healthy": sibling}
    tx.hass.config_entries.async_entries = lambda _: [tx.entry]
    result = await loading.async_agent_catalog(tx.hass, "admin", True)
    assert len(result["agents"]) == 2
    assert result["agents"][0]["function_group_count"] is None
    assert group_collection_repair_metadata(tx.subentry.data)["group_collection_repair"]
