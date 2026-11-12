"""Manual public-API Knowledge scale probe (in-memory storage, no disk latency).

Run before and after on the same host, e.g. --repeats 3 --output results.json.
Report operation latency separately from the maximum 5 ms heartbeat gap; these
are observations, not portable performance assertions. Initialization is excluded.
"""

import argparse
import asyncio
from contextlib import suppress
from dataclasses import asdict
import json
from pathlib import Path
import platform
import statistics
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


async def measure(operation):
    gaps = []

    async def heartbeat():
        previous = time.perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.02)
    start = time.perf_counter()
    try:
        result = await operation()
        elapsed = time.perf_counter() - start
        await asyncio.sleep(0.02)
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
    return result, {"latency_ms": elapsed * 1000, "max_loop_gap_ms": max(gaps) * 1000}


async def scenario(count, size, repeats):
    paragraph = (
        "common reference household " + " ".join(f"token{i}" for i in range(80)) + "\n"
    )
    body = (paragraph * (size // len(paragraph) + 1))[:size]
    stamp = "2026-10-01T00:00:00+00:00"
    sources = [
        asdict(
            KnowledgeSource(
                str(i), f"Source {i}", "common reference", body, stamp, stamp
            )
        )
        for i in range(count)
    ]
    library = KnowledgeLibrary(Storage({"sources": sources}))
    await library.async_initialize()
    # Distinct alphabetic words exercise morphology rather than the cheaper
    # repeated alphanumeric tokens used to populate the high-frequency library.
    words = [
        "reference" + "".join(chr(97 + (i // (26**j)) % 26) for j in range(4))
        for i in range(size // 10 + 1)
    ]
    varied = ("common reference " + " ".join(words))[:size]
    records = {}
    for repeat in range(repeats):
        created, timing = await measure(
            lambda: library.async_create("New source", "common reference", body)
        )
        records.setdefault("create", []).append(timing)
        _, timing = await measure(
            lambda n=repeat: library.async_update("0", content=body[:-1] + str(n % 10))
        )
        records.setdefault("edit", []).append(timing)
        for label, query, source_ids in [
            ("frequent_search", "common reference", None),
            ("filtered_search", "common reference", ["0"]),
            ("absent_search", "absentidentifier", None),
        ]:
            _, timing = await measure(
                lambda q=query, ids=source_ids: library.async_search(q, source_ids=ids)
            )
            records.setdefault(label, []).append(timing)
        _, timing = await measure(
            lambda sid=created.source_id: library.async_delete(sid)
        )
        records.setdefault("delete", []).append(timing)
        created, timing = await measure(
            lambda: library.async_create("Varied source", "common reference", varied)
        )
        records.setdefault("varied_create", []).append(timing)
        _, timing = await measure(
            lambda sid=created.source_id, n=repeat: library.async_update(
                sid, content=varied[:-1] + str(n % 10)
            )
        )
        records.setdefault("varied_edit", []).append(timing)
        await library.async_delete(created.source_id)
        _, timing = await measure(lambda: asyncio.sleep(0))
        records.setdefault("idle", []).append(timing)
    summaries = {
        name: {
            "median_latency_ms": statistics.median(v["latency_ms"] for v in samples),
            "max_loop_gap_ms": max(v["max_loop_gap_ms"] for v in samples),
            "samples": samples,
        }
        for name, samples in records.items()
    }
    return {
        "sources": count,
        "characters_per_source": size,
        "chunks": len(library._chunks),
        "operations": summaries,
    }


async def main(args):
    cases = {
        "small": (10, 2_000),
        "representative": (100, 20_000),
        "limit": (MAX_SOURCES_PER_AGENT - 1, MAX_CONTENT_LENGTH),
    }
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "heartbeat_interval_ms": 5,
        "repeats": args.repeats,
        "storage": "in-memory; serialization included, disk I/O excluded",
        "scenarios": {},
    }
    for name in args.scenarios:
        result = await scenario(*cases[name], args.repeats)
        report["scenarios"][name] = result
        print(
            f"{name}: {result['sources']} sources x {result['characters_per_source']} characters, {result['chunks']} chunks",
            flush=True,
        )
        for operation, timing in result["operations"].items():
            print(
                f"  {operation}: median_latency={timing['median_latency_ms']:.2f}ms max_loop_gap={timing['max_loop_gap_ms']:.2f}ms",
                flush=True,
            )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=["small", "representative", "limit"],
        default=["small", "representative", "limit"],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    asyncio.run(main(args))
