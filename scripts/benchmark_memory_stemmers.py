"""Non-gating four-way Memory morphology evaluation; no production switch.

Requires a separate BENCHMARK-only installation of snowballstemmer==3.1.1.
Runs in one process without model calls, user data, or persisted store changes.

The four variants share Memory's real ranking, document scores, metadata bonuses,
thresholds, scope checks and thread workers. Each variant gets fresh feature caches.
Hybrid is a prototype (exact terms plus prefixed Snowball evidence), NOT a
calibrated change to production relevance thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from importlib.metadata import version
import json
from pathlib import Path
import re
import sys
import threading
from time import perf_counter, process_time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import snowballstemmer  # noqa: E402
from custom_components.extended_openai_conversation_responses import memory as m  # noqa: E402
from custom_components.extended_openai_conversation_responses.retrieval_text import (  # noqa: E402
    _stem as custom_stem,
    cjk_terms,
    lexical_terms,
)
from scripts.benchmark_memory_refactor import (  # noqa: E402
    SYNTHETIC_QUERIES,
    OWNER,
    _percentile,
    _round,
    load_fixture,
    manager_from_records,
    peak_rss_kib,
    record,
    synthetic_records,
)

STEMMER_VERSION = "3.1.1"
MODES = ("exact", "custom", "snowball", "hybrid")
_thread_local = threading.local()


def snowball(word: str) -> str:
    """Per-thread Snowball instance: Snowball upstream does not promise sharing."""
    if not (word.isascii() and word.isalpha() and len(word) > 3):
        return word
    stemmer = getattr(_thread_local, "stemmer", None)
    if stemmer is None:
        stemmer = snowballstemmer.stemmer("english")
        _thread_local.stemmer = stemmer
    return stemmer.stemWord(word)


def make_tokenizer(mode: str):
    """Preserve current normalization, compound and CJK index behavior."""
    if mode not in MODES:
        raise ValueError(mode)

    def tokens(value: str) -> list[str]:
        normalized = unicodedata.normalize("NFC", value).casefold().replace("\u2019", "'")
        normalized = re.sub(r"(?<=\w)'s\b|(?<=s)'(?=\W|$)", "", normalized)
        raw = [item for item in lexical_terms(normalized) if item not in m._STOP_WORDS]
        if mode == "exact":
            terms = raw
        elif mode == "custom":
            terms = [custom_stem(item) for item in raw]
        elif mode == "snowball":
            terms = [snowball(item) for item in raw]
        else:
            # Treat morphology as ADDITIONAL evidence, never a substitute for
            # the original token. Tag stem terms to keep exact identifiers
            # logically separate, and avoid useless duplicates.
            terms = raw + [
                "snow:" + candidate for item in raw
                if (candidate := snowball(item)) != item
            ]
        return terms + sorted(cjk_terms(normalized) - set(terms))

    return tokens


def clear_caches() -> None:
    for name in (
        "_cached_memory_record_terms",
        "_cached_memory_record_token_set",
        "_cached_memory_term_frequencies",
        "_cached_memory_tokens",
    ):
        getattr(m, name).cache_clear()


async def mode_results(mode: str, count: int, repeats: int) -> dict:
    m._token_list = make_tokenizer(mode)
    clear_caches()
    corpus, digest = load_fixture()
    records = [
        record(
            item["id"], item["scope"], item["content"],
            category=item["category"], key=item.get("key"),
            subject=item.get("subject"),
        )
        for item in corpus["records"]
    ]
    manager = await manager_from_records(records)
    totals = {f"recall_at_{k}": 0 for k in (1, 3, 5, 10)}
    totals.update({f"hard_negative_hits_at_{k}": 0 for k in (1, 3, 5, 10)})
    labels = 0
    rows = []
    for query in corpus["queries"]:
        ranked = await manager.async_search(query["scope"], query["query"], limit=10)
        ids = [result.memory_id for result in ranked]
        relevant = set(query["relevant_ids"])
        excluded = set(query["excluded_ids"])
        labels += len(relevant)
        for k in (1, 3, 5, 10):
            totals[f"recall_at_{k}"] += len(relevant & set(ids[:k]))
            totals[f"hard_negative_hits_at_{k}"] += len(excluded & set(ids[:k]))
        rows.append({
            "id": query["id"],
            "family": query["family"],
            "ranked_ids": ids,
            "false_candidates_at_3": sorted(excluded & set(ids[:3])),
            "missed_relevant_at_5": sorted(relevant - set(ids[:5])),
        })
    for k in (1, 3, 5, 10):
        totals[f"recall_at_{k}"] = _round(totals[f"recall_at_{k}"] / labels) if labels else None
    # Cold/warm synthetic searches use the actual async-to-thread Memory path.
    m._token_list = make_tokenizer(mode)
    clear_caches()
    performance_manager = await manager_from_records(synthetic_records(count))
    cpu_start = process_time()
    gaps: list[float] = []

    async def heartbeat() -> None:
        last = perf_counter()
        while True:
            await asyncio.sleep(0.005)
            current = perf_counter()
            gaps.append(max(0.0, (current - last - 0.005) * 1000))
            last = current

    task = asyncio.create_task(heartbeat())
    cold: list[float] = []
    warm: list[float] = []
    try:
        await asyncio.sleep(0.015)
        for query in SYNTHETIC_QUERIES:
            started = perf_counter()
            await performance_manager.async_search(OWNER, query, limit=10)
            cold.append((perf_counter() - started) * 1000)
        for _ in range(repeats):
            for query in SYNTHETIC_QUERIES:
                started = perf_counter()
                await performance_manager.async_search(OWNER, query, limit=10)
                warm.append((perf_counter() - started) * 1000)
        await asyncio.sleep(0.015)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return {
        "mode": mode,
        "fixture_sha256": digest,
        "queries": len(rows),
        "labels": labels,
        **totals,
        "rows": rows,
        "cold_p95_ms": _percentile(cold, 0.95),
        "warm_p50_ms": _percentile(warm, 0.5),
        "warm_p95_ms": _percentile(warm, 0.95),
        "process_cpu_ms": _round((process_time() - cpu_start) * 1000),
        "max_excess_event_loop_gap_ms": _round(max(gaps, default=0.0)),
        "process_peak_rss_kib": peak_rss_kib(),
    }


async def evaluate(count: int, repeats: int) -> dict:
    if version("snowballstemmer") != STEMMER_VERSION:
        raise RuntimeError("Benchmark requires exactly snowballstemmer==3.1.1")
    original = m._token_list
    try:
        modes = [await mode_results(name, count, repeats) for name in MODES]
    finally:
        m._token_list = original
        clear_caches()
    custom = modes[1]
    for item in modes:
        item["hard_negative_promotion_vs_custom_at_3"] = (
            item["hard_negative_hits_at_3"] - custom["hard_negative_hits_at_3"]
        )
        item["lost_relevant_cases_vs_custom_at_5"] = sorted(
            row["id"] for row, baseline in zip(item["rows"], custom["rows"])
            if baseline["missed_relevant_at_5"] == [] and row["missed_relevant_at_5"]
        )
        item["changed_rankings_vs_custom"] = sum(
            row["ranked_ids"] != baseline["ranked_ids"]
            for row, baseline in zip(item["rows"], custom["rows"])
        )
    return {
        "schema_version": 1,
        "benchmark_only_dependency": "snowballstemmer==3.1.1",
        "third_party_stemmer_class": str(type(getattr(_thread_local, "stemmer", None))),
        "production_changed": False,
        "relevance_thresholds_recalibrated": False,
        "embeddings_or_hybrid_vector_scores_compared": False,
        "case_sensitive_paths_preserved_as_exact_strings": False,
        "note": (
            "Memory currently casefolds paths; rank comparisons do not establish "
            "case-sensitive path identity. Hybrid additive scores have uncalibrated "
            "thresholds and cannot be promoted without additional validation."
        ),
        "modes": modes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.size <= m.MAX_MEMORIES_PER_AGENT or not 1 <= args.repeats <= 30:
        parser.error("invalid benchmark size or repeat count")
    result = asyncio.run(evaluate(args.size, args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for item in result["modes"]:
        print(
            f"{item['mode']}: recall@5={item['recall_at_5']}, "
            f"hard-negatives@3={item['hard_negative_hits_at_3']}, "
            f"warm-p95={item['warm_p95_ms']}ms"
        )


if __name__ == "__main__":
    main()
