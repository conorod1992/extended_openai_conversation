"""Manual Knowledge scale probe: python scripts/benchmark_knowledge.py.

Uses the actual public async edit/search APIs and an in-memory persistence adapter.
Reports loop gaps separately from worker initialization and excludes disk latency.
Timings are observations, not portable performance assertions.
"""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.extended_openai_conversation_responses.knowledge import (
    MAX_CONTENT_LENGTH,
    MAX_SOURCES_PER_AGENT,
    KnowledgeLibrary,
    KnowledgeSource,
)


class Storage:
    def __init__(self, data):
        self.data = data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data
        await asyncio.sleep(0)


async def measure(label, operation):
    gaps = []

    async def heartbeat():
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    start = time.perf_counter()
    try:
        await operation()
        elapsed = time.perf_counter() - start
        await asyncio.sleep(0.01)
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
    print(
        f"{label}: elapsed={elapsed * 1000:.1f}ms max_loop_gap={max(gaps) * 1000:.1f}ms"
    )


async def main():
    body = (
        "common reference household " + " ".join(f"token{i}" for i in range(80)) + "\n"
    )
    body = (body * (MAX_CONTENT_LENGTH // len(body) + 1))[:MAX_CONTENT_LENGTH]
    stamp = "2026-10-01T00:00:00+00:00"
    sources = [
        asdict(
            KnowledgeSource(
                str(i), f"Source {i}", "common reference", body, stamp, stamp
            )
        )
        for i in range(MAX_SOURCES_PER_AGENT)
    ]
    library = KnowledgeLibrary(Storage({"sources": sources}))
    await library.async_initialize()
    print(
        f"{library.total_source_count} sources, {len(body)} characters each, {len(library._chunks)} chunks"
    )
    await measure(
        "frequent-term search", lambda: library.async_search("common reference")
    )
    await measure(
        "full source edit and persistence snapshot",
        lambda: library.async_update(
            "0",
            content=body + " " if len(body) < MAX_CONTENT_LENGTH else body[:-1] + "x",
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
