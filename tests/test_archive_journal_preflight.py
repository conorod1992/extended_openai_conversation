"""Archive journal replay validates its whole candidate before any writes."""

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
)
from tests.test_conversation_archive import _session, _turn


@pytest.mark.parametrize(
    "damage",
    [
        "turn",
        "duplicate",
        "month",
        "owner",
        "count",
        "session",
        "binding",
        "removed",
        "unchanged",
    ],
)
async def test_corrupt_journal_cannot_overwrite_existing_partitions(hass, damage):
    storage = HomeAssistantArchiveStorage(hass, "entry", "agent")
    payload = {"turns": [asdict(_turn(user_text="Replacement"))]}
    metadata = {
        "sessions": [asdict(_session())],
        "active": {},
        "partitions": ["2026-09"],
        "pending_partitions": {"2026-09": payload},
    }
    if damage == "turn":
        payload["turns"].append({"recoverable": "malformed intent"})
    elif damage == "duplicate":
        payload["turns"].append(deepcopy(payload["turns"][0]))
    elif damage == "month":
        payload["turns"][0]["timestamp"] = "2026-08-01T10:00:00+00:00"
    elif damage == "owner":
        payload["turns"][0]["session_id"] = "unknown"
    elif damage == "count":
        metadata["sessions"][0]["turn_count"] = 99
    elif damage == "session":
        metadata["sessions"][0]["agent_subentry_id"] = []
    elif damage == "binding":
        metadata["active"] = {"key": "unknown"}
    elif damage == "removed":
        metadata["partitions"] = []
    else:
        metadata["partitions"].append("2026-10")
        await storage.async_save_partition(
            "2026-10", {"turns": [{"recoverable": "unchanged partition"}]}
        )
    await storage.async_save_partition(
        "2026-09", {"turns": [asdict(_turn(user_text="Original evidence"))]}
    )
    await storage.async_save_metadata(metadata)
    paths = [
        Path(storage._metadata.path),
        Path(storage._partition_store("2026-09").path),
    ]
    original = [path.read_bytes() for path in paths]
    for _ in range(2):
        manager = ConversationArchive(storage, "agent")
        with pytest.raises(ValueError, match="transaction is corrupted"):
            await manager.async_initialize()
        assert not manager.initialized
        assert [path.read_bytes() for path in paths] == original


async def test_valid_journal_replaces_unreadable_destination_without_loading_it(
    hass, monkeypatch
):
    storage = HomeAssistantArchiveStorage(hass, "entry", "agent")
    path = Path(storage._partition_store("2026-09").path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unreadable damaged JSON")
    await storage.async_save_metadata(
        {
            "sessions": [asdict(_session())],
            "active": {},
            "partitions": ["2026-09"],
            "pending_partitions": {"2026-09": {"turns": [asdict(_turn())]}},
        }
    )
    load = storage.async_load_partition

    async def require_replaced(name):
        assert path.read_text() != "unreadable damaged JSON"
        return await load(name)

    monkeypatch.setattr(storage, "async_load_partition", require_replaced)
    manager = ConversationArchive(storage, "agent")
    await manager.async_initialize()
    assert manager.stats()["turn_count"] == 1
    assert "pending_partitions" not in await storage.async_load_metadata()


async def test_preflight_read_failure_retains_journal_and_all_destinations(
    hass, monkeypatch
):
    storage = HomeAssistantArchiveStorage(hass, "entry", "agent")
    await storage.async_save_metadata(
        {
            "sessions": [asdict(_session())],
            "partitions": ["2026-09", "2026-10"],
            "pending_partitions": {"2026-09": {"turns": [asdict(_turn())]}},
        }
    )
    original = Path(storage._metadata.path).read_bytes()
    monkeypatch.setattr(
        storage, "async_load_partition", AsyncMock(side_effect=OSError("read failed"))
    )
    save = AsyncMock()
    monkeypatch.setattr(storage, "async_save_partition", save)
    with pytest.raises(OSError, match="read failed"):
        await ConversationArchive(storage, "agent").async_initialize()
    save.assert_not_called()
    assert Path(storage._metadata.path).read_bytes() == original
