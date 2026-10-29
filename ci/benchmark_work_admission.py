"""Measure synthetic parse/index/export worker pressure; impose no admission policy."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from threading import Event, Lock, Thread
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", default="1,2,4")
    parser.add_argument("--jobs", type=int, default=64)
    parser.add_argument("--sources", type=int, default=16)
    parser.add_argument("--characters", type=int, default=32768)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        levels = [int(value) for value in args.concurrency.split(",")]
    except ValueError:
        parser.error("concurrency must be comma-separated integers")
    if (
        not levels
        or len(set(levels)) != len(levels)
        or any(not 1 <= level <= 16 for level in levels)
        or not 1 <= args.jobs <= 64
        or not 1 <= args.sources <= 500
        or not 1024 <= args.characters <= 100000
        or any(
            level > args.jobs
            for level in ([args.child] if args.child is not None else levels)
        )
        or (args.child is not None and not 1 <= args.child <= 16)
    ):
        parser.error(
            "invalid benchmark dimensions (jobs <=64, concurrency <=16, sources <=500, characters 1024..100000)"
        )
    return args, levels


async def measure(args):
    # Parent runs stay small; each measured process imports the production code
    # and builds its own workload before taking a baseline.
    sys.path.insert(0, str(ROOT))
    import psutil

    from custom_components.extended_openai_conversation_responses.backup import (
        MAX_BACKUP_BYTES,
        _decode_backup_document,
    )
    from custom_components.extended_openai_conversation_responses.bounded_json import (
        bounded_json_dumps,
    )
    from custom_components.extended_openai_conversation_responses.knowledge import (
        _load_index,
    )

    body = " ".join(
        f"reference_{index:06d}" for index in range(args.characters // 8 + 1)
    )[: args.characters]
    raw = json.dumps(
        {
            "knowledge": {
                "sources": [
                    {
                        "source_id": f"source-{index}",
                        "title": f"Reference {index}",
                        "description": "Synthetic benchmark",
                        "content": body,
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                        "enabled": True,
                    }
                    for index in range(args.sources)
                ]
            }
        }
    )

    worker_lock = Lock()
    active_workers = 0
    peak_workers = 0

    def work():
        nonlocal active_workers, peak_workers
        with worker_lock:
            active_workers += 1
            peak_workers = max(peak_workers, active_workers)
        try:
            return run_worker()
        finally:
            with worker_lock:
                active_workers -= 1

    def run_worker():
        document = _decode_backup_document(raw, MAX_BACKUP_BYTES)
        sources, index = _load_index(document["knowledge"]["sources"])
        exported = bounded_json_dumps(document, MAX_BACKUP_BYTES)
        if len(sources) != args.sources or not index.chunks or not exported:
            raise RuntimeError(
                "production worker did not complete the expected workload"
            )
        # Release heavy outputs after each job. This probes transient worker
        # pressure, not the cost of permanently retaining many agents' indexes.
        return len(index.chunks)

    process = psutil.Process()
    gc.collect()
    baseline = process.memory_info().rss
    rss = [baseline]
    stop = Event()
    sampler_errors = []

    def sample():
        try:
            while not stop.wait(0.01):
                rss.append(process.memory_info().rss)
        except Exception as error:
            sampler_errors.append(error)
            stop.set()

    sampler = Thread(target=sample, name="work-admission-rss", daemon=True)
    sampler.start()
    lag = []
    finished = asyncio.Event()

    async def heartbeat():
        while not finished.is_set():
            before = monotonic()
            await asyncio.sleep(0.01)
            lag.append(max(0.0, monotonic() - before - 0.01))

    admission = asyncio.Semaphore(args.child)

    async def job():
        async with admission:
            return await asyncio.to_thread(work)

    pulse = asyncio.create_task(heartbeat())
    started = monotonic()
    try:
        chunks = await asyncio.gather(*(job() for _ in range(args.jobs)))
        elapsed = monotonic() - started
        rss.append(process.memory_info().rss)
    finally:
        finished.set()
        try:
            await pulse
        finally:
            stop.set()
            sampler.join()
    if sampler_errors:
        raise RuntimeError(
            "RSS sampling failed; benchmark measurements are incomplete"
        ) from sampler_errors[0]
    gc.collect()
    return {
        "concurrency": args.child,
        "observed_peak_workers": peak_workers,
        "completed_jobs": len(chunks),
        "input_bytes_per_job": len(raw.encode("utf-8")),
        "chunks_per_job": chunks[0],
        "elapsed_seconds": round(elapsed, 4),
        "jobs_per_second": round(args.jobs / elapsed, 3),
        "baseline_rss_bytes": baseline,
        "sampled_peak_rss_bytes": max(rss),
        "rss_after_gc_bytes": process.memory_info().rss,
        "heartbeat_samples": len(lag),
        "heartbeat_p95_ms": round(
            statistics.quantiles(lag, n=100, method="inclusive")[94] * 1000, 3
        )
        if len(lag) >= 2
        else None,
        "heartbeat_max_ms": round(max(lag, default=0.0) * 1000, 3),
    }


def main():
    args, levels = arguments()
    if args.child is not None:
        print(json.dumps(asyncio.run(measure(args))))
        return
    try:
        import resource

        address_space_limit = resource.getrlimit(resource.RLIMIT_AS)[0]
        if address_space_limit == resource.RLIM_INFINITY:
            address_space_limit = None
    except ImportError:
        address_space_limit = None
    report = {
        "schema_version": 1,
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "worktree_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip()
        ),
        "address_space_limit_bytes": address_space_limit,
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else None,
        "jobs": args.jobs,
        "sources": args.sources,
        "characters_per_source": args.characters,
        "workload": "production JSON decode, canonical Knowledge load/index, bounded JSON export; synthetic data",
        "cases": [],
    }
    for level in levels:
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            str(level),
            "--jobs",
            str(args.jobs),
            "--sources",
            str(args.sources),
            "--characters",
            str(args.characters),
        ]
        try:
            child = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
            )
        except subprocess.CalledProcessError as error:
            sys.stderr.write(error.stderr)
            raise
        report["cases"].append(json.loads(child.stdout))
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
