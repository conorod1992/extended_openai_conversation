"""Bulk index building keeps the loop responsive and publishes complete state."""

import asyncio
from dataclasses import asdict, replace
import threading
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import knowledge
from tests.test_knowledge import FakeStorage


def _sources(count=100):
    content = ("responsive searchable reference material\n" * 2100)[:80_000]
    return [
        knowledge.KnowledgeSource(
            source_id=f"source-{i}",
            title=f"Reference {i}",
            description="Bulk library",
            content=content,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            enabled=i != 0,
        )
        for i in range(count)
    ]


@pytest.mark.parametrize("restore", [False, True])
async def test_bulk_index_heartbeat_and_search_parity(restore, monkeypatch):
    sources = _sources()
    storage = FakeStorage(
        {"sources": [asdict(source) for source in sources]} if not restore else None
    )
    library = knowledge.KnowledgeLibrary(storage)
    if restore:
        await library.async_initialize()
    ticks = []
    running = True
    building = threading.Event()
    worker_threads = []
    original = knowledge._build_index

    def observe_build(snapshot):
        worker_threads.append(threading.get_ident())
        building.set()
        try:
            return original(snapshot)
        finally:
            building.clear()

    monkeypatch.setattr(knowledge, "_build_index", observe_build)

    async def heartbeat():
        while running:
            if building.is_set():
                ticks.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.005)

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    try:
        if restore:
            await library.async_replace_backup(sources)
        else:
            await library.async_initialize()
        # Count only callbacks during construction, not callbacks after the
        # index finished or during storage writes. No machine-specific rate.
        assert ticks
        assert all(worker != threading.get_ident() for worker in worker_threads)
    finally:
        running = False
        await task
    assert library.total_source_count == 100
    assert library.source_count == 99
    results = await library.async_search("searchable reference")
    assert results
    assert all(result.source_id != "source-0" for result in results)
    assert (await library.async_get("source-1")).content == sources[1].content
    assert len(library._chunks) == len(knowledge._build_index(tuple(sources)).chunks)


async def test_cancelled_restore_worker_cannot_publish_or_mutate_live_state(
    monkeypatch,
):
    library = knowledge.KnowledgeLibrary(FakeStorage())
    await library.async_initialize()
    old = await library.async_create("Old", "", "original searchable content")
    original = knowledge._build_index
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_build(sources):
        started.set()
        release.wait(timeout=5)
        try:
            return original(sources)
        finally:
            finished.set()

    monkeypatch.setattr(knowledge, "_build_index", blocked_build)
    task = asyncio.create_task(library.async_replace_backup(_sources(2)))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert library.source_count == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await library.async_search("original"))[0].source_id == old.source_id
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    assert library.source_count == 1
    assert (await library.async_get(old.source_id)).content == old.content


async def test_cancelled_initialize_can_retry_after_worker_finishes(monkeypatch):
    sources = _sources(2)
    library = knowledge.KnowledgeLibrary(
        FakeStorage({"sources": [asdict(source) for source in sources]})
    )
    original = knowledge._load_index
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocked_load(raw):
        started.set()
        release.wait(timeout=5)
        try:
            return original(raw)
        finally:
            finished.set()

    monkeypatch.setattr(knowledge, "_load_index", blocked_load)
    task = asyncio.create_task(library.async_initialize())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not library.initialized
        assert not library._chunks
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    assert not library.initialized
    await library.async_initialize()
    assert library.source_count == 1
    assert library.total_source_count == 2


async def test_unchanged_knowledge_preserves_revision_and_does_not_write(monkeypatch):
    storage = FakeStorage()
    library = knowledge.KnowledgeLibrary(storage)
    await library.async_initialize()
    source = await library.async_create("Catalogue", "", "Reference text.")
    save = AsyncMock(wraps=storage.async_save)
    monkeypatch.setattr(storage, "async_save", save)
    assert (
        await library.async_update(
            source.source_id, title="Catalogue", expected_revision=source.updated_at
        )
        == source
    )
    await library.async_replace_backup([source])
    save.assert_not_awaited()
    with pytest.raises(ValueError, match="changed in another tab"):
        await library.async_update(source.source_id, expected_revision="stale")
    await library.async_replace_backup([replace(source, enabled=False)])
    save.assert_awaited_once()
    assert not (await library.async_get(source.source_id)).enabled
