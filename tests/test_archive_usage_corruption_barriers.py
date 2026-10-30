"""Damaged Archive and Usage files survive reads, later writes and restart."""

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    _UsageStore,
)
from tests.test_conversation_archive import _session, _turn


async def test_archive_privacy_purge_preserves_unrelated_damaged_material(hass):
    storage = HomeAssistantArchiveStorage(hass, "entry", "agent")
    private = asdict(_session("private", retention_state="private"))
    damaged = {"session_id": [], "recoverable": "session evidence"}
    metadata = {
        "sessions": [private, damaged],
        "partitions": ["2026-09"],
        "opaque": "metadata evidence",
    }
    raw_turn = {"recoverable": "turn evidence"}
    await storage.async_save_metadata(metadata)
    await storage.async_save_partition(
        "2026-09",
        {"turns": [asdict(_turn("private")), raw_turn], "opaque": "partition evidence"},
    )
    for _ in range(2):
        manager = ConversationArchive(storage, "agent")
        await manager.async_initialize()
        assert manager.stats()["storage_repair_required"]
        loaded = await storage.async_load_metadata()
        assert loaded == {**metadata, "sessions": [{**private, "title": ""}, damaged]}
        assert await storage.async_load_partition("2026-09") == {
            "turns": [raw_turn],
            "opaque": "partition evidence",
        }


async def test_usage_replacement_clears_barrier_only_after_all_authoritative_writes(
    hass, monkeypatch
):
    stores = [
        _UsageStore(hass, 1, f"replace.usage.{name}", atomic_writes=True)
        for name in ["totals", "daily", "details"]
    ]
    await stores[2].async_save({"requests": [{"recoverable": "damaged"}], "runs": []})
    manager = UsageManager(*stores)
    await manager.async_initialize()
    # Validate an independent clean generation, never the salvaged damaged one.
    clean = UsageManager(_UsageStore(hass, 1, "clean.usage", atomic_writes=True))
    await clean.async_initialize()
    replacement = manager.validate_backup_data(await clean.async_backup_data(), "agent")
    evidence = Path(stores[2].path).read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(
            stores[2], "async_save", AsyncMock(side_effect=OSError("details failed"))
        )
        with pytest.raises(OSError, match="details failed"):
            await manager.async_replace_backup(*replacement)
    assert Path(stores[2].path).read_bytes() == evidence
    with pytest.raises(ValueError, match="needs repair"):
        await manager.async_backup_data()
    await manager.async_replace_backup(*replacement)
    assert "storage_repair_required" not in manager.persistence_status()
    assert (await manager.async_backup_data())["requests"] == []


@pytest.mark.parametrize(
    "damage", ["session", "turn", "duplicate", "container", "metadata", "partition"]
)
async def test_archive_native_evidence_survives_mutation_and_restart(hass, damage):
    storage = HomeAssistantArchiveStorage(hass, "entry", "agent")
    metadata = {
        "sessions": [asdict(_session())],
        "active": {},
        "partitions": ["2026-09"],
    }
    payload = {"turns": [asdict(_turn())]}
    if damage == "session":
        metadata["sessions"].append({"recoverable": "session evidence"})
    elif damage == "turn":
        payload["turns"].append({"recoverable": "turn evidence"})
    elif damage == "duplicate":
        payload["turns"].append({**asdict(_turn()), "user_text": "Other fact"})
    elif damage == "container":
        payload["turns"] = {"recoverable": payload["turns"]}
    elif damage == "metadata":
        metadata["active"] = {"recoverable": ["bad binding"]}
    else:
        metadata["partitions"].append("2026-13")
    await storage.async_save_metadata(metadata)
    await storage.async_save_partition("2026-09", payload)
    paths = [
        Path(storage._metadata.path),
        Path(storage._partition_store("2026-09").path),
    ]
    original = [path.read_bytes() for path in paths]
    for _ in range(2):
        manager = ConversationArchive(storage, "agent")
        await manager.async_initialize()
        assert manager.stats()["storage_repair_required"]
        for operation in [manager.async_backup_data, manager.async_recovery_data]:
            with pytest.raises(ValueError, match="needs repair"):
                await operation()
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_delete_session("user:alice", "session-1")
        assert [path.read_bytes() for path in paths] == original
    # A complete validated replacement remains available to journal recovery.
    await manager.async_replace_backup([_session()], [_turn()])
    assert "storage_repair_required" not in manager.stats()
    assert len((await manager.async_backup_data())["turns"]) == 1


@pytest.mark.parametrize(
    "category", ["totals", "days", "request", "duplicate", "container"]
)
async def test_usage_native_evidence_survives_immediate_delayed_and_shutdown_writes(
    hass, monkeypatch, category
):
    stores = [
        _UsageStore(hass, 1, f"corrupt.usage.{name}", atomic_writes=True)
        for name in ["totals", "daily", "details"]
    ]
    totals = {"conversation_count": 3}
    daily = {"days": {}}
    details = {"requests": [], "runs": []}
    if category == "totals":
        totals["conversation_count"] = "recoverable"
    elif category == "days":
        daily["days"] = {"2026-09-01": {"api_request_count": "broken"}}
    elif category == "container":
        details["requests"] = {"recoverable": "details"}
    elif category == "duplicate":
        # Obtain a genuine durable request shape through the production writer.
        from tests.test_usage import _residual_request

        raw = asdict(_residual_request("duplicate", "2026-10-29T00:00:00+00:00"))
        details["requests"] = [raw, deepcopy(raw)]
    else:
        details["requests"] = [{"recoverable": "request evidence"}]
    for store, data in zip(stores, [totals, daily, details], strict=True):
        await store.async_save(data)
    paths = [Path(store.path) for store in stores]
    original = [path.read_bytes() for path in paths]
    for _ in range(2):
        manager = UsageManager(*stores)
        await manager.async_initialize()
        assert manager.persistence_status()["storage_repair_required"]
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_backup_data()
        for operation in [
            manager._async_save_aggregates,
            manager._async_save_details,
        ]:
            with pytest.raises(ValueError, match="needs repair"):
                await operation()
        # Routine telemetry must neither schedule nor perform a destructive write.
        with monkeypatch.context() as fault:
            schedule = AsyncMock()
            fault.setattr(manager, "_schedule_aggregate_snapshots", schedule)
            await manager._async_save_safely(
                "request aggregates", manager._async_save_aggregates
            )
            schedule.assert_not_called()
        with pytest.raises(ValueError, match="needs repair"):
            await manager.async_clear_details(confirm=True)
        await manager.async_record_request(successful=True)
        await manager.async_shutdown()
        assert manager._stopping
        assert not manager.persistence_status()["survives_restart"]
        assert [path.read_bytes() for path in paths] == original
