"""Bounded bulk expiry and owner-isolation stress for Temporary Memory."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    TemporaryMemory,
)
from homeassistant.util import dt as dt_util
from tests_stress.conftest import record


class MemoryStore:
    def __init__(self, data: dict):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


async def test_bulk_expiry_keeps_unrelated_live_owners(
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    assert MAX_ACTIVE_RECORDS >= 100
    now = dt_util.utcnow()
    for batch in range(stress_scale):
        raw = []
        for number in range(100):
            owner = f"user:owner-{number // 5}"
            live = number % 5 >= 3
            raw.append(
                {
                    "memory_id": f"batch-{batch}-record-{number}",
                    "scope_id": owner,
                    "owner_scope_id": owner,
                    "content": f"temporary marker {batch} {number} 東京",
                    "category": "nightly",
                    "source": "automatic",
                    "expires_at": (
                        now + (timedelta(hours=2) if live else -timedelta(hours=2))
                    ).isoformat(),
                    "created_at": (now - timedelta(days=1)).isoformat(),
                    "updated_at": (now - timedelta(days=1)).isoformat(),
                }
            )
        store = MemoryStore({"records": raw})
        temporary = TemporaryMemory(store)
        await temporary.async_initialize()
        assert temporary.stats()["active_temporary_memory_count"] == 40
        assert temporary.stats()["expired_temporary_memories_pruned"] == 60
        for owner_number in range(20):
            owner = f"user:owner-{owner_number}"
            selected = await temporary.async_active(owner, owner_scope_id=owner)
            assert len(selected) == 2
            assert all(
                item.owner_scope_id == owner and item.expires_at > now.isoformat()
                for item in selected
            )
            assert all("temporary marker" in item.content for item in selected)
        assert len((await temporary.async_backup_data())["records"]) == 40
        restarted = TemporaryMemory(MemoryStore(store.data))
        await restarted.async_initialize()
        assert len((await restarted.async_backup_data())["records"]) == 40
        record(stress_trace, "bulk_expiry", batch=batch, expired=60, live=40, owners=20)
    record(
        stress_trace,
        "summary",
        total_expired=60 * stress_scale,
        total_live=40 * stress_scale,
        owners=20 * stress_scale,
    )
