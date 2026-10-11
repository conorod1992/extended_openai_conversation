"""Retrieval pagination, snapshot isolation, filtering and lexical regressions."""

import asyncio
import threading
import time

import pytest

from custom_components.extended_openai_conversation_responses import memory as subject
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    _excerpt,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_archive import _archive
from tests.test_memory_search_performance import _record, _Storage


async def test_search_supports_two_full_service_pages_and_metadata_browse():
    records = [
        _record(
            str(i),
            "alice",
            f"Common record number {i}",
            "home",
            subject="UniqueSubject",
            key=f"fact.key{i}",
        )
        for i in range(220)
    ]
    manager = subject.PersistentMemory(_Storage(records))
    await manager.async_initialize()
    first = await manager.async_search("alice", "common", limit=100)
    second = await manager.async_search("alice", "common", limit=100, offset=100)
    assert len(first) == len(second) == 100
    assert not {m.memory_id for m in first} & {m.memory_id for m in second}
    assert (await manager.async_browse("alice", "UniqueSubject"))[1] == 220
    assert (await manager.async_browse("alice", "fact.key219"))[1] == 1


async def test_rank_worker_does_not_hold_live_lock_and_drops_deleted_results(
    monkeypatch,
):
    manager = subject.PersistentMemory(
        _Storage([_record("one", "alice", "Common needle", "home")])
    )
    await manager.async_initialize()
    started = threading.Event()
    release = threading.Event()
    original = subject._rank_memory_snapshot
    loop_thread = threading.get_ident()

    def rank(*args):
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(subject, "_rank_memory_snapshot", rank)
    task = asyncio.create_task(manager.async_search("alice", "needle"))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert await asyncio.wait_for(manager.async_delete("alice", ["one"]), 1) == 1
    finally:
        release.set()
    assert await task == []


async def test_unmatched_multiword_search_keeps_heartbeat_responsive():
    text = " ".join(f"houseitem{i}" for i in range(65))
    records = [
        _record(str(i), "alice", f"{text} unique{i}", "home") for i in range(10000)
    ]
    manager = subject.PersistentMemory(_Storage(records))
    await manager.async_initialize()
    gaps = []

    async def beat():
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    heartbeat = asyncio.create_task(beat())
    await asyncio.sleep(0.01)
    try:
        assert await manager.async_search("alice", "quartz zeppelin orbit") == []
        await asyncio.sleep(0.01)
    finally:
        heartbeat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat
    assert max(gaps) < 0.5


async def test_archive_distinguishes_technical_identifiers_and_apostrophe_variants():
    archive = await _archive()
    session = await archive.async_begin_session(
        "owner",
        user_scope("alice", source="authenticated_user"),
        "chat",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    for i, text in enumerate(["Use C++", "Use C#", "Use C", "Ask O\u2019Connor"]):
        await archive.async_record_turn(
            session.session_id,
            run_id=str(i),
            user_text=text,
            assistant_text="Recorded",
            successful=True,
        )
    for query, expected in [
        ("C++", "Use C++"),
        ("C#", "Use C#"),
        ("C", "Use C"),
        ("O'Connor", "Ask O\u2019Connor"),
    ]:
        results = (await archive.async_search("user:alice", query))["results"]
        assert len(results) == 1
        assert expected in results[0]["excerpt"]
    assert "O\u2019Connor" in _excerpt(
        "prefix " * 200 + "O\u2019Connor owns this", "O'Connor"
    )
