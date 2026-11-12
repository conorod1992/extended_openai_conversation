"""Runtime Knowledge workers preserve retrieval, transactions, and loop ownership."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import threading

import pytest

from custom_components.extended_openai_conversation_responses import knowledge
from tests.test_knowledge import FakeStorage
from tests.test_persistence_cancellation_hardening import BlockingStorage


async def _library(storage=None):
    library = knowledge.KnowledgeLibrary(storage or FakeStorage())
    await library.async_initialize()
    return library


def _index_state(library):
    return (
        dict(library._sources),
        dict(library._chunks),
        dict(library._source_features),
        {token: set(keys) for token, keys in library._token_index.items()},
    )


def _assert_consistent(library):
    expected = knowledge._build_index(tuple(library._sources.values()))
    assert library._chunks == expected.chunks
    assert library._source_features == expected.source_features
    assert library._token_index == expected.token_index


async def test_public_search_matches_prechange_oracle_after_edit_and_restart():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/knowledge_search_parity.json").read_text(
            encoding="utf-8"
        )
    )
    storage = FakeStorage({"sources": fixture["sources"]})
    library = await _library(storage)
    for case in fixture["cases"]:
        assert [
            asdict(result)
            for result in await library.async_search(case["query"], **case["options"])
        ] == case["expected"]
    # The edit timestamps advance in the same source order as the oracle.
    # Compare the unchanged retrieval fields, using each actual stored revision.
    for raw in fixture["sources"]:
        await library.async_update(raw["source_id"], content=raw["content"])
    restarted = await _library(storage)
    for current in (library, restarted):
        for case in fixture["cases"]:
            expected = [
                {
                    **result,
                    "updated_at": current._sources[result["source_id"]].updated_at,
                }
                for result in case["expected"]
            ]
            assert [
                asdict(result)
                for result in await current.async_search(
                    case["query"], **case["options"]
                )
            ] == expected
        _assert_consistent(current)


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_partial_private_index_failure_does_not_publish(operation, monkeypatch):
    storage = FakeStorage()
    library = await _library(storage)
    source = await library.async_create("Original", "", "original content")
    before = _index_state(library)
    saved = deepcopy(storage.data)
    original_tokens = knowledge._search_tokens
    original_index = knowledge._index_source
    partial = []
    body_chunks = 0

    def observe(source, index):
        partial.append(index)
        return original_index(source, index)

    def fail_during_second_chunk(text, *args):
        nonlocal body_chunks
        if "unpublished" in text:
            body_chunks += 1
            if body_chunks == 2:
                raise RuntimeError("partial private index")
        return original_tokens(text, *args)

    monkeypatch.setattr(knowledge, "_index_source", observe)
    monkeypatch.setattr(knowledge, "_search_tokens", fail_during_second_chunk)
    with pytest.raises(RuntimeError, match="partial private index"):
        if operation == "create":
            await library.async_create("New", "", "unpublished content " * 300)
        else:
            await library.async_update(
                source.source_id, content="unpublished content " * 300
            )
    assert body_chunks == 2
    assert len(partial[0].chunks) == 1  # The real builder failed mid-source.
    assert _index_state(library) == before
    assert storage.data == saved
    assert (await library.async_search("original"))[0].source_id == source.source_id
    assert await library.async_search("unpublished") == []


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_cancelled_private_builder_cannot_publish_after_new_edit(
    operation, monkeypatch
):
    library = await _library()
    source = await library.async_create("Original", "", "original content")
    original = knowledge._build_index
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    private = []

    def build(sources):
        result = original(sources)
        if sources[0].content == "cancelled content":
            private.append(result)
            started.set()
            try:
                assert release.wait(5)
            finally:
                finished.set()
        return result

    monkeypatch.setattr(knowledge, "_build_index", build)
    operation_coro = (
        library.async_create("Cancelled", "", "cancelled content")
        if operation == "create"
        else library.async_update(source.source_id, content="cancelled content")
    )
    task = asyncio.create_task(operation_coro)
    try:
        assert await asyncio.to_thread(started.wait, 5)
        assert private[0].chunks
        assert library._sources[source.source_id] == source
        assert all(
            chunk.text != "cancelled content" for chunk in library._chunks.values()
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The abandoned worker owns only its private index. It can safely finish
        # after a different edit/search has acquired and released the live lock.
        newer = await library.async_update(source.source_id, content="newer content")
        assert (await library.async_search("newer"))[0].source_id == source.source_id
        assert await library.async_search("cancelled") == []
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    assert library._sources == {source.source_id: newer}
    _assert_consistent(library)


@pytest.mark.parametrize("worker_fails", [False, True])
async def test_search_cancellation_retains_lock_until_worker_stops(
    worker_fails, monkeypatch
):
    library = await _library()
    source = await library.async_create("Original", "", "original content")
    original = library._search_locked
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    first = True
    observed = []

    def search(*args, **kwargs):
        nonlocal first
        if not first:
            return original(*args, **kwargs)
        first = False
        started.set()
        try:
            assert release.wait(5)
            observed.append(library._sources[source.source_id])
            if worker_fails:
                raise RuntimeError("search worker failed")
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(library, "_search_locked", search)
    task = asyncio.create_task(library.async_search("original"))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        edit = asyncio.create_task(
            library.async_update(source.source_id, content="newer content")
        )
        following_search = asyncio.create_task(library.async_search("newer"))
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert library._lock.locked()
            assert not edit.done()
            assert not following_search.done()
            assert library._sources[source.source_id] is source
    finally:
        release.set()
    with pytest.raises(RuntimeError if worker_fails else asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(finished.wait, 5)
    await edit
    assert (await following_search)[0].source_id == source.source_id
    assert observed == [source]
    _assert_consistent(library)


async def test_concurrent_edits_recheck_revision_before_starting_worker(monkeypatch):
    library = await _library()
    source = await library.async_create("Original", "", "original content")
    original = knowledge._build_index
    started, release = threading.Event(), threading.Event()
    builds = []

    def build(sources):
        builds.append(sources)
        started.set()
        assert release.wait(5)
        return original(sources)

    monkeypatch.setattr(knowledge, "_build_index", build)
    first = asyncio.create_task(
        library.async_update(
            source.source_id,
            content="newer content",
            expected_revision=source.updated_at,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        second = asyncio.create_task(
            library.async_update(
                source.source_id,
                content="stale content",
                expected_revision=source.updated_at,
            )
        )
        await asyncio.sleep(0)
        assert not second.done()
    finally:
        release.set()
    await first
    with pytest.raises(ValueError, match="another tab"):
        await second
    assert len(builds) == 1
    assert library._sources[source.source_id].content == "newer content"


@pytest.mark.parametrize("after_write", [False, True])
async def test_worker_edit_reconciles_failed_persistence_and_restart(
    after_write, monkeypatch
):
    storage = BlockingStorage()
    library = await _library(storage)
    source = await library.async_create("Original", "", "original content")
    untouched = await library.async_create("Untouched", "", "independent content")
    original = knowledge._build_index
    builds = []

    def build(sources):
        builds.append(tuple(s.source_id for s in sources))
        return original(sources)

    monkeypatch.setattr(knowledge, "_build_index", build)
    storage.fail_saves = not after_write
    storage.fail_after_write = after_write
    with pytest.raises(RuntimeError):
        await library.async_update(source.source_id, content="newer content")
    assert builds[0] == (source.source_id,)  # Only the changed source is built.
    assert set(builds[1]) == {source.source_id, untouched.source_id}  # Recovery.
    expected_query = "newer" if after_write else "original"
    assert (await library.async_search(expected_query))[0].source_id == source.source_id
    assert await library.async_get(untouched.source_id) == untouched
    _assert_consistent(library)
    restarted = await _library(storage)
    assert _index_state(restarted) == _index_state(library)


@pytest.mark.parametrize("fail", [False, True])
async def test_search_waits_for_complete_persistence_generation(fail):
    storage = BlockingStorage()
    library = await _library(storage)
    source = await library.async_create("Original", "", "original content")
    storage.arm(fail=fail)
    edit = asyncio.create_task(library.async_update(source.source_id, enabled=False))
    await storage.save_started.wait()
    following_search = asyncio.create_task(library.async_search("original"))
    await asyncio.sleep(0)
    assert not following_search.done()
    assert library.source_count == 1  # Synchronous readers still see committed state.
    _assert_consistent(library)  # Internal candidate index is already complete.
    storage.release_save.set()
    if fail:
        with pytest.raises(RuntimeError):
            await edit
    else:
        await edit
    result = await following_search
    assert [r.source_id for r in result] == ([source.source_id] if fail else [])
    assert library.source_count == int(fail)


@pytest.mark.parametrize("operation", ["create", "update", "search"])
async def test_runtime_workers_allow_heartbeat_before_return(operation, monkeypatch):
    content = (
        "common reference "
        + " ".join(
            "word" + "".join(chr(97 + i // 26**j % 26) for j in range(4))
            for i in range(15000)
        )
    )[: knowledge.MAX_CONTENT_LENGTH]
    stamp = "2026-01-01T00:00:00+00:00"
    sources = [
        knowledge.KnowledgeSource(str(i), "common reference", "", content, stamp, stamp)
        for i in range(80 if operation == "search" else 1)
    ]
    library = await _library(FakeStorage({"sources": [asdict(s) for s in sources]}))
    active, loop_acknowledged = threading.Event(), threading.Event()
    worker_threads, ticks = [], []
    original = (
        library._search_locked if operation == "search" else knowledge._build_index
    )

    def observe(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        active.set()
        try:
            result = original(*args, **kwargs)
            # Scheduling under the GIL/debug instrumentation is not a latency
            # guarantee, even for real CPU work. Hold the completed worker result
            # until a heartbeat acknowledges it: this deterministically detects
            # accidentally running that work on the loop. The public-API
            # benchmarks separately measure unpaused CPU work and actual gaps.
            assert loop_acknowledged.wait(5)
            return result
        finally:
            active.clear()

    if operation == "search":
        monkeypatch.setattr(library, "_search_locked", observe)
    else:
        monkeypatch.setattr(knowledge, "_build_index", observe)
    running = True

    async def heartbeat():
        while running:
            if active.is_set():
                ticks.append(asyncio.get_running_loop().time())
                loop_acknowledged.set()
            # An immediately runnable callback avoids depending on the host's
            # timer granularity for a single bounded source build. Benchmarks
            # separately measure timed heartbeat gaps with the normal 5ms probe.
            await asyncio.sleep(0)

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    try:
        if operation == "search":
            assert await library.async_search("common reference")
        elif operation == "create":
            await library.async_create("New", "", content)
        else:
            await library.async_update("0", content=content[:-1] + "x")
        assert ticks
        assert all(t != threading.get_ident() for t in worker_threads)
    finally:
        running = False
        await task


async def test_incremental_postings_and_ties_match_original_insertion(monkeypatch):
    from datetime import datetime

    stamp = "2026-01-01T00:00:00+00:00"
    monkeypatch.setattr(
        knowledge.dt_util, "utcnow", lambda: datetime.fromisoformat(stamp)
    )
    sources = [
        knowledge.KnowledgeSource(
            sid,
            "common reference",
            "",
            f"HEADER-{sid}\n" + "common reference material\n" * 250,
            stamp,
            stamp,
        )
        for sid in ("a", "b", "c")
    ]
    raw = {"sources": [asdict(source) for source in sources]}
    library, reference = (
        await _library(FakeStorage(raw)),
        await _library(FakeStorage(raw)),
    )

    def original_insert(source):
        reference._sources[source.source_id] = source
        knowledge._index_source(
            source,
            knowledge._KnowledgeIndex(
                reference._chunks, reference._source_features, reference._token_index
            ),
        )
        reference._remember_committed_state()

    for sid in ("a", "c", "b"):
        updated = await library.async_update(
            sid, content=library._sources[sid].content + "added"
        )
        reference._unindex(sid)
        original_insert(updated)
        assert [*library._token_index["common"]] == [*reference._token_index["common"]]
        assert await library.async_search(
            "common reference", limit=10
        ) == await reference.async_search("common reference", limit=10)
        assert all(
            result.excerpt.startswith("HEADER-")
            for result in await library.async_search("common reference")
        )
    created = await library.async_create(
        "common reference", "", "HEADER-new\ncommon reference material"
    )
    original_insert(created)
    assert await library.async_search(
        "common reference", offset=1, lookahead=True
    ) == await reference.async_search("common reference", offset=1, lookahead=True)


async def test_global_task_cancellation_cannot_abandon_live_search_worker(monkeypatch):
    from contextvars import ContextVar

    library = await _library()
    source = await library.async_create("Original", "", "original content")
    original = library._search_locked
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    actor = ContextVar("knowledge_test_actor", default=None)
    owner = object()
    token = actor.set(owner)
    observed = []

    def search(*args, **kwargs):
        assert actor.get() is owner  # Executor dispatch retains request context.
        started.set()
        try:
            assert release.wait(5)
            observed.append(library._sources[source.source_id])
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(library, "_search_locked", search)
    existing_tasks = asyncio.all_tasks()
    task = asyncio.create_task(library.async_search("original"))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        # Model shutdown cancelling both caller and any owned child tasks. The
        # executor future itself is not a Task and must remain owned/drained.
        for owned in asyncio.all_tasks() - existing_tasks:
            owned.cancel()
        for _ in range(3):
            await asyncio.sleep(0)
        assert not task.done()
        assert library._lock.locked()
        edit = asyncio.create_task(
            library.async_update(source.source_id, content="newer content")
        )
        await asyncio.sleep(0)
        assert not edit.done()
    finally:
        release.set()
        actor.reset(token)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(finished.wait, 5)
    await edit
    assert observed == [source]
    assert library._sources[source.source_id].content == "newer content"
