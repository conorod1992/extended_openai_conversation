"""Reproducible, non-gating Memory lexical retrieval baseline.

Run in a Home Assistant-compatible development environment:
    python scripts/benchmark_memory_refactor.py --size 1000 --repeats 3 --output baseline.json

The frozen fixture labels are independent of the implementation. Report misses
and hard-negative hits rather than declaring the old behaviour correct.
No real user records, provider requests, embedding calls, or file writes to
Home Assistant storage are involved. Timing excludes disk I/O and HA startup.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from copy import deepcopy
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.extended_openai_conversation_responses.memory import (  # noqa: E402
    MAX_MEMORIES_PER_AGENT,
    PersistentMemory,
)

TIMESTAMP = "2026-10-01T12:00:00+00:00"
OWNER = "baseline"
FIXTURE = ROOT / "tests" / "fixtures" / "memory_refactor_retrieval.json"
BASELINE_SHA = "71608da9e16ec40dd77ee83f7ae41458d0c20f78"
SYNTHETIC_QUERIES = (
    "sensor calibration",
    "thermostat heating",
    "rail journey",
    "garden lights",
    "paleontology fossil",
    "unfindable-seven-digit-identifier",
)


class Storage:
    """Detached in-memory copy of one durable generation; no filesystem writes."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = deepcopy(data)
        self.save_count = 0

    async def async_load(self) -> dict[str, Any]:
        return deepcopy(self._data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.save_count += 1
        self._data = deepcopy(data)


def record(
    identity: str,
    owner: str,
    content: str,
    *,
    category: str = "general",
    key: str | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    return {
        "memory_id": identity,
        "user_id": owner,
        "content": content,
        "category": category,
        "source": "explicit",
        "created_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        "subject": subject,
        "key": key,
        "valid_from": None,
    }


def load_fixture() -> tuple[dict[str, Any], str]:
    raw = FIXTURE.read_bytes()
    corpus = json.loads(raw)
    if corpus.get("schema_version") != 1:
        raise ValueError("Unrecognized retrieval corpus version")
    return corpus, hashlib.sha256(raw).hexdigest()


async def manager_from_records(records: list[dict[str, Any]]) -> PersistentMemory:
    storage = Storage({"memories": records})
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    if storage.save_count:
        raise RuntimeError("Baseline storage unexpectedly rewrote a generated corpus")
    return memory


def _round(value: float) -> float:
    return round(value, 4)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    samples = sorted(values)
    position = (len(samples) - 1) * percentile
    lower = int(position)
    fraction = position - lower
    return _round(samples[lower] + fraction * (samples[min(lower + 1, len(samples) - 1)] - samples[lower]))


async def fixture_ranking() -> dict[str, Any]:
    fixture, digest = load_fixture()
    records = [
        record(
            item["id"],
            item["scope"],
            item["content"],
            category=item["category"],
            key=item.get("key"),
            subject=item.get("subject"),
        )
        for item in fixture["records"]
    ]
    memory = await manager_from_records(records)
    positive_total = found5 = found10 = hard_negative_hits = 0
    rows = []
    for query in fixture["queries"]:
        actual = await memory.async_search(query["scope"], query["query"], limit=10)
        returned = [entry.memory_id for entry in actual]
        gold = set(query["relevant_ids"])
        negatives = set(query["excluded_ids"])
        positive_total += len(gold)
        found5 += len(set(returned[:5]) & gold)
        found10 += len(set(returned) & gold)
        hard_negative_hits += len(set(returned) & negatives)
        rows.append(
            {
                "id": query["id"],
                "family": query["family"],
                "scope": query["scope"],
                "relevant_ids": sorted(gold),
                "excluded_ids": sorted(negatives),
                "top10_ids": returned,
                "recall5_numerator": len(set(returned[:5]) & gold),
                "recall10_numerator": len(set(returned) & gold),
                "hard_negative_hits10": sorted(set(returned) & negatives),
            }
        )
    return {
        "fixture_sha256": digest,
        "record_count": len(records),
        "query_count": len(rows),
        "positive_labels": positive_total,
        "micro_recall_at_5": _round(found5 / positive_total) if positive_total else None,
        "micro_recall_at_10": _round(found10 / positive_total) if positive_total else None,
        "hard_negative_hits_at_10": hard_negative_hits,
        "rows": rows,
    }


def synthetic_records(count: int) -> list[dict[str, Any]]:
    # Fixed, heterogeneous workload. Terms deliberately repeat; other tokens
    # distinguish records to exercise corpus frequency and feature caching.
    templates = (
        "Sensor calibration completed for thermostat heating zone {i}.",
        "The rail journey timetable changed for platform {i}.",
        "Garden lights were inspected near path {i}.",
        "Paleontology fossil catalogue entry {i} documents marine reptiles.",
        "The household utility meter reading at site {i} was checked.",
        "Firmware deployment gpt-5.6-monitoring test at node {i} succeeded.",
    )
    return [
        record(
            f"scale-{index:05d}",
            OWNER,
            templates[index % len(templates)].format(i=index),
            category=("test", "home", "transport")[index % 3],
        )
        for index in range(count)
    ]


async def _timed_search(
    manager: PersistentMemory, query: str
) -> tuple[float, int]:
    start = perf_counter()
    results = await manager.async_search(OWNER, query, limit=10)
    return (perf_counter() - start) * 1000, len(results)


async def synthetic_performance(count: int, repeats: int) -> dict[str, Any]:
    manager = await manager_from_records(synthetic_records(count))
    loop_gaps_ms: list[float] = []

    async def heartbeat() -> None:
        last = perf_counter()
        while True:
            await asyncio.sleep(0.005)
            now = perf_counter()
            loop_gaps_ms.append(max(0.0, (now - last - 0.005) * 1000))
            last = now

    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.015)
    cold_ms: list[float] = []
    warm_ms: list[float] = []
    return_counts: dict[str, int] = {}
    try:
        for query in SYNTHETIC_QUERIES:
            elapsed, count_returned = await _timed_search(manager, query)
            cold_ms.append(elapsed)
            return_counts[query] = count_returned
        for _ in range(repeats):
            for query in SYNTHETIC_QUERIES:
                elapsed, count_returned = await _timed_search(manager, query)
                warm_ms.append(elapsed)
                if count_returned != return_counts[query]:
                    raise RuntimeError("Identical warm query changed its result count")
        await asyncio.sleep(0.015)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return {
        "record_count": count,
        "repeats": repeats,
        "query_count_per_pass": len(SYNTHETIC_QUERIES),
        "cold_ms": {
            "p50": _percentile(cold_ms, 0.5),
            "p95": _percentile(cold_ms, 0.95),
            "max": _round(max(cold_ms, default=0.0)),
        },
        "warm_ms": {
            "p50": _percentile(warm_ms, 0.5),
            "p95": _percentile(warm_ms, 0.95),
            "max": _round(max(warm_ms, default=0.0)),
        },
        "max_excess_event_loop_gap_ms": _round(max(loop_gaps_ms, default=0.0)),
        "returns_per_query": return_counts,
    }


async def current_mutation_behaviour() -> dict[str, Any]:
    """Measure legacy write outcomes without judging their semantic correctness.

    These synthetic probes give subsequent contract changes a differential
    reference. No assertion demands that the old behaviour be preserved.
    """
    tests = [
        {
            "name": "existing-key-explicit-correction",
            "initial": [record("original", "Thermostat target is 19 C.", "alice", key="temp.target")],
            "owner": "alice",
            "incoming": {"content": "Thermostat target is 21 C.", "category": "general", "source": "explicit", "key": "temp.target"},
        },
        {
            "name": "unkeyed-related-contradiction",
            "initial": [record("original", "I prefer tea to coffee.", "alice")],
            "owner": "alice",
            "incoming": {"content": "I prefer coffee to tea.", "category": "general", "source": "explicit"},
        },
        {
            "name": "punctuation-only-duplicate",
            "initial": [record("original", "I like milk.", "alice")],
            "owner": "alice",
            "incoming": {"content": "I like milk!!!", "category": "general", "source": "explicit"},
        },
        {
            "name": "implicit-protected-key",
            "initial": [record("original", "I like tea.", "alice", key="drink.preference")],
            "owner": "alice",
            "incoming": {"content": "I dislike tea.", "category": "general", "source": "implicit", "key": "drink.preference"},
        },
        {
            "name": "new-key",
            "initial": [],
            "owner": "alice",
            "incoming": {"content": "The router is downstairs.", "category": "general", "source": "explicit", "key": "router.location"},
        },
        {
            "name": "unkeyed-key-promotion",
            "initial": [dict(record("original", "My dog is a collie.", "alice", subject="Dog"), source="implicit")],
            "owner": "alice",
            "incoming": {"content": "My dog is a collie.", "category": "general", "source": "explicit", "subject": "Dog", "key": "pets.dog.breed"},
        },
        {
            "name": "same-key-different-owner",
            "initial": [record("original", "My dog is a collie.", "alice", key="pets.dog.breed")],
            "owner": "bob",
            "incoming": {"content": "My dog is a terrier.", "category": "general", "source": "explicit", "key": "pets.dog.breed"},
        },
        {
            "name": "unkeyed-same-text-different-subject",
            "initial": [record("original", "The lamp is on.", "alice", subject="Kitchen")],
            "owner": "alice",
            "incoming": {"content": "The lamp is on.", "category": "general", "source": "explicit", "subject": "Bedroom"},
        },
    ]
    rows = []
    for test in tests:
        manager = await manager_from_records(test["initial"])
        before = {
            entry.memory_id: entry
            for user in {"alice", "bob"}
            for entry in await manager.async_list(user, limit=100)
        }
        result = await manager.async_upsert(test["owner"], **test["incoming"])
        after = {
            entry.memory_id: entry
            for user in {"alice", "bob"}
            for entry in await manager.async_list(user, limit=100)
        }
        rows.append(
            {
                "name": test["name"],
                "status": result["status"],
                "before_count": len(before),
                "after_count": len(after),
                "changed_existing_ids": sorted(
                    identity
                    for identity in before.keys() & after.keys()
                    if before[identity] != after[identity]
                ),
                "deleted_existing_ids": sorted(before.keys() - after.keys()),
                "new_record_count": len(after.keys() - before.keys()),
            }
        )
    return {"probe_count": len(rows), "rows": rows}


def package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


async def run(size: int, repeats: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "baseline_source": BASELINE_SHA,
        "checkout_sha": os.environ.get("GITHUB_SHA"),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "homeassistant": package_version("homeassistant"),
            "cpu_count": os.cpu_count(),
        },
        "constraints": {
            "provider_calls": 0,
            "disk_io": False,
            "real_user_data": False,
            "hybrid_embeddings_measured": False,
            "timings_are_release_gates": False,
            "cold_definition": "first search of each query on fresh manager in this process",
        },
        "fixture_retrieval": await fixture_ranking(),
        "current_mutation_behaviour": await current_mutation_behaviour(),
        "synthetic_lexical": await synthetic_performance(size, repeats),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.size <= MAX_MEMORIES_PER_AGENT:
        parser.error(f"--size must be 1..{MAX_MEMORIES_PER_AGENT}")
    if not 1 <= args.repeats <= 30:
        parser.error("--repeats must be 1..30")
    result = asyncio.run(run(args.size, args.repeats))
    output = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Saved baseline: {args.output}")
    else:
        print(output)
    print(
        "lexical_recall@5="
        f"{result['fixture_retrieval']['micro_recall_at_5']}, "
        "hard_negative_hits@10="
        f"{result['fixture_retrieval']['hard_negative_hits_at_10']}, "
        "synthetic_warm_p95_ms="
        f"{result['synthetic_lexical']['warm_ms']['p95']}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
