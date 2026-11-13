"""Memory management pagination follows instants while retaining stable ties."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.extended_openai_conversation_responses import memory as module
from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    PersistentMemory,
)
from tests.test_memory import FakeStorage


@pytest.mark.parametrize("reader", ["async_list", "async_list_page", "async_browse"])
async def test_mixed_offset_naive_and_microsecond_sort_with_stable_pagination(reader):
    stamps = [
        "2026-10-25T02:10:00+02:00",  # 00:10 UTC, sorts lexically too high
        "2026-10-25T00:20:00Z",
        "2026-10-25T01:15:00+01:00",  # equal to the next two, preserve insertion order
        "2026-10-25T00:15:00",  # legacy naive is UTC, regardless of HA timezone
        "2026-10-24T19:15:00-05:00",
        "2026-10-25T00:20:00.000001+00:00",
    ]
    base = MemoryRecord("0", "alice", "Tea.", "home", "explicit", stamps[0], stamps[0])
    storage = FakeStorage(
        {
            "memories": [
                asdict(replace(base, memory_id=str(i), updated_at=value))
                for i, value in enumerate(stamps)
            ]
        }
    )
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    before, saved = deepcopy(storage.data), storage.save_count
    ids = []
    for offset in (0, 2, 4):
        result = await getattr(memory, reader)(
            "alice",
            **({"query": ""} if reader == "async_browse" else {}),
            limit=2,
            offset=offset,
        )
        rows = result[0] if isinstance(result, tuple) else result
        ids.extend(row.memory_id for row in rows)
        if reader == "async_list_page":
            assert result[1] is (offset < 4)
        elif reader == "async_browse":
            assert result[1] == 6
    assert ids == ["5", "1", "2", "3", "4", "0"]
    assert [row.updated_at for row in memory._memories.values()] == stamps
    assert storage.data == before and storage.save_count == saved


async def test_sort_parsing_is_bounded_and_reused_at_memory_limit(monkeypatch):
    stamps = [
        (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=i)).isoformat()
        for i in range(10_000)
    ]
    base = MemoryRecord("0", "alice", "Tea.", "home", "explicit", stamps[0], stamps[0])
    memory = PersistentMemory(
        FakeStorage(
            {
                "memories": [
                    asdict(replace(base, memory_id=str(i), updated_at=value))
                    for i, value in enumerate(stamps)
                ]
            }
        )
    )
    await memory.async_initialize()
    module._updated_at_instant.cache_clear()
    parsed = []
    original = module.dt_util.parse_datetime

    def parse(value):
        parsed.append(value)
        return original(value)

    monkeypatch.setattr(module.dt_util, "parse_datetime", parse)
    assert (await memory.async_list("alice"))[0].memory_id == "9999"
    assert (await memory.async_list_page("alice", offset=100))[0][0].memory_id == "9899"
    assert (await memory.async_browse("alice", ""))[1] == 10_000
    assert len(parsed) == 10_000
    assert module._updated_at_instant.cache_info().maxsize == 20_000


@pytest.mark.parametrize("reader", ["async_list", "async_list_page", "async_browse"])
async def test_valid_extreme_offset_timestamps_do_not_overflow_listing(reader):
    stamps = [
        "0001-01-01T00:00:00Z",
        "9999-12-31T23:59:59-14:00",
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59Z",
    ]
    base = MemoryRecord("0", "alice", "Tea.", "home", "explicit", stamps[0], stamps[0])
    storage = FakeStorage(
        {
            "memories": [
                asdict(replace(base, memory_id=str(i), updated_at=value))
                for i, value in enumerate(stamps)
            ]
        }
    )
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    result = await getattr(memory, reader)(
        "alice", **({"query": ""} if reader == "async_browse" else {})
    )
    rows = result[0] if isinstance(result, tuple) else result
    assert [row.memory_id for row in rows] == ["1", "3", "0", "2"]
    assert [row.updated_at for row in memory._memories.values()] == stamps
    assert storage.save_count == 0
