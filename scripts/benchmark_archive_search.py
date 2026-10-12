"""Compare cold/warm archive search CPU using deterministic generated conversations.

Run with the integration's Python dependencies installed. Optional Git revisions
compare the pre-change implementation without switching the working checkout.
This excludes disk I/O and provider requests; timings are not test assertions.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import statistics
import subprocess
import sys
from threading import Event
import time
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom_components.extended_openai_conversation_responses import (  # noqa: E402
    conversation_archive as current,
)

PACKAGE = current.__package__
SOURCE = (
    "custom_components/extended_openai_conversation_responses/conversation_archive.py"
)
SCENARIOS = (
    ("short", 1_000),
    ("mixed", 10_000),
    ("technical", 10_000),
    ("long", 1_000),
    ("repeated", 10_000),
    ("short", 50_000),
)


def revision_module(ref: str, label: str, source_path: str = SOURCE) -> ModuleType:
    source = subprocess.check_output(
        ["git", "show", f"{ref}:{source_path}"], cwd=ROOT, encoding="utf-8"
    )
    name = f"{PACKAGE}._archive_benchmark_{label}"
    module = ModuleType(name)
    module.__package__ = PACKAGE
    sys.modules[name] = module
    exec(compile(source, f"<git:{ref}:{source_path}>", "exec"), module.__dict__)
    return module


def corpus(kind: str, count: int):
    """Use short requests, varied replies, technical text and occasional long turns."""
    requests = (
        ("Turn on the kitchen lights.", "The kitchen lights are on at 60 percent."),
        ("What is the living room temperature?", "It is 21 degrees; heating is off."),
        (
            "Remind me to replace the air filter.",
            "Your household maintenance reminder is saved.",
        ),
        (
            "Why did the bedroom sensor go offline?",
            "Check its battery, signal strength and recent availability.",
        ),
    )
    stamp = datetime(2026, 10, 12, tzinfo=UTC)
    sessions, turns = {}, {}
    for index in range(count):
        session_id = f"session-{index // 50}"
        timestamp = (stamp + timedelta(seconds=index)).isoformat()
        if session_id not in sessions:
            sessions[session_id] = current.ArchiveSession(
                session_id,
                None,
                "agent",
                "user:benchmark",
                "user",
                "user",
                None,
                timestamp,
                timestamp,
                "Household discussion",
                0,
                "retained",
            )
            turns[session_id] = []
        user, assistant = requests[index % len(requests)]
        if kind in {"mixed", "long"}:
            assistant += " " + (
                "The kitchen schedule uses local time. The household routine includes "
                "checking doors, lighting and heating before bedtime. "
            ) * (1 + index % 4)
        if kind == "technical":
            assistant += " " + " ".join(f"sensor_{index}_{word}" for word in range(80))
        if kind == "long" and index % 20 == 0:
            assistant += (
                " "
                + (
                    "Maintenance log: checked the kitchen air filter, thermostat, "
                    "water pressure, window contacts and battery status. "
                )
                * 300
            )
        if kind == "repeated":
            user = assistant = (
                "Routine household maintenance inspection checklist preference equipment notes. "
            ) * 10
        if index % 100 == 0:
            assistant += " The dehumidifier needs an inspection."
        turns[session_id].append(
            current.ArchiveTurn(
                f"turn-{index}",
                session_id,
                None,
                timestamp,
                user,
                assistant[: current.MAX_TEXT_LENGTH],
                True,
            )
        )
    snapshot = tuple(
        (
            replace(
                session,
                turn_count=len(turns[key]),
                last_message_at=turns[key][-1].timestamp,
            ),
            tuple(turns[key]),
        )
        for key, session in sessions.items()
    )
    return snapshot


def clear_cache(module):
    if hasattr(module, "_SEARCH_FEATURES"):
        with module._SEARCH_FEATURE_LOCK:
            module._SEARCH_FEATURES.clear()
            module._SEARCH_FEATURE_BYTES = 0


def measure(modules, snapshot, query, repeats):
    samples = {label: {"cold": [], "warm": []} for label in modules}
    reference = None
    labels = list(modules)
    # Exclude first imports and initial interpreter specialization from samples.
    for module in modules.values():
        module._search_archive_snapshot(snapshot[:1], query, None, None, 1, 0)
    for repetition in range(repeats):
        # Rotate implementations so background CPU/frequency changes do not
        # systematically favor the version measured last.
        start = repetition % len(labels)
        for label in labels[start:] + labels[:start]:
            module = modules[label]
            clear_cache(module)
            for phase in ("cold", "warm"):
                started = time.perf_counter()
                actual = module._search_archive_snapshot(
                    snapshot, query, None, None, 1, 0
                )
                samples[label][phase].append((time.perf_counter() - started) * 1_000)
                if reference is not None:
                    assert reference == actual, (
                        f"Ranking changed for {query}/{label}/{phase}"
                    )
                reference = actual
    return {
        label: {
            "cold_ms": round(statistics.median(samples[label]["cold"]), 2),
            "warm_ms": round(statistics.median(samples[label]["warm"]), 2),
            "cache_bytes": getattr(module, "_SEARCH_FEATURE_BYTES", 0),
            "cache_turns": len(getattr(module, "_SEARCH_FEATURES", ())),
        }
        for label, module in modules.items()
    }


class MemoryStorage:
    """Exclude disk latency while retaining the actual archive mutation path."""

    async def async_load_metadata(self):
        return None

    async def async_save_metadata(self, data):
        pass

    async def async_load_partition(self, partition):
        return None

    async def async_save_partition(self, partition, data):
        pass


async def concurrency_measurements(baseline_ref, repeats):
    from custom_components.extended_openai_conversation_responses import (
        management_history_queries as history,
    )
    from homeassistant.exceptions import HomeAssistantError

    modules = {}
    if baseline_ref:
        baseline_archive = revision_module(baseline_ref, "concurrent_archive")
        baseline = revision_module(
            baseline_ref,
            "concurrent_history",
            SOURCE.replace("conversation_archive.py", "management_history_queries.py"),
        )
        baseline._tokens = baseline_archive._tokens
        baseline._normalize = baseline_archive._normalize
        modules["baseline"] = baseline
    modules["current"] = history
    snapshot = corpus("mixed", 10_000)
    rows = []
    for operation in ("record", "private", "delete"):
        for label, module in modules.items():
            elapsed, locked, retries = [], [], 0
            original = module._archive_search_sync
            for _ in range(repeats):
                clear_cache(current)
                archive = current.ConversationArchive(MemoryStorage(), "agent")
                await archive.async_initialize()
                archive._sessions = {
                    session.session_id: session for session, _ in snapshot
                }
                archive._turns = defaultdict(
                    list, {s.session_id: list(t) for s, t in snapshot}
                )
                archive._partitions = {
                    turn.timestamp[:7] for _, turns in snapshot for turn in turns
                }
                entered = Event()

                def search(*args, started=entered, query=original):
                    started.set()
                    return query(*args)

                module._archive_search_sync = search
                task = asyncio.create_task(
                    module.archive_search_page(
                        archive, "user:benchmark", "kitchen", limit=1
                    )
                )
                try:
                    if not await asyncio.to_thread(entered.wait, 10):
                        raise RuntimeError("Search worker did not start")
                    if task.done():
                        raise RuntimeError(
                            "Search finished before the concurrency probe"
                        )
                    locked.append(archive._lock.locked())
                    session_id = snapshot[-1][0].session_id
                    started = time.perf_counter()
                    if operation == "record":
                        recorded = await archive.async_record_turn(
                            session_id,
                            run_id="benchmark",
                            user_text="Another request",
                            assistant_text="Recorded",
                            successful=True,
                        )
                        assert recorded is not None
                    elif operation == "private":
                        await archive.async_make_private(session_id)
                    else:
                        await archive.async_delete_session("user:benchmark", session_id)
                    elapsed.append((time.perf_counter() - started) * 1_000)
                    try:
                        await task
                    except HomeAssistantError as err:
                        if operation == "record" or "changed during search" not in str(
                            err
                        ):
                            raise
                        retries += 1
                finally:
                    module._archive_search_sync = original
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            row = {
                "operation": operation,
                "implementation": label,
                "mutation_median_ms": round(statistics.median(elapsed), 2),
                "search_held_lock": any(locked),
                "revoked_result_retries": retries,
                "repeats": repeats,
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", help="Git revision before archive changes")
    parser.add_argument(
        "--comparison-ref", help="Git revision before performance tuning"
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenario", choices=sorted({name for name, _ in SCENARIOS}))
    parser.add_argument("--output", type=Path, help="Write measured rows as JSON")
    parser.add_argument(
        "--concurrency-only",
        action="store_true",
        help="Measure actual recording, privacy and deletion during Management search",
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.concurrency_only:
        rows = asyncio.run(concurrency_measurements(args.baseline_ref, args.repeats))
        if args.output:
            args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        return
    modules = {}
    for label, ref in (
        ("baseline", args.baseline_ref),
        ("before_tuning", args.comparison_ref),
    ):
        if ref:
            modules[label] = revision_module(ref, label)
    modules["current"] = current
    rows = []
    for kind, count in SCENARIOS:
        if args.scenario and kind != args.scenario:
            continue
        snapshot = corpus(kind, count)
        chars = sum(
            len(t.user_text) + len(t.assistant_text)
            for _, turns in snapshot
            for t in turns
        )
        common = "household" if kind == "repeated" else "kitchen"
        for query in (common, "dehumidifier", "quasifusion"):
            row = {
                "scenario": kind,
                "turns": count,
                "characters": chars,
                "query": query,
            }
            row.update(measure(modules, snapshot, query, args.repeats))
            rows.append(row)
            print(json.dumps(row), flush=True)
    if args.output:
        args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
