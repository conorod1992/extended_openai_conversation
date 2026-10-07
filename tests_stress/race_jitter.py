"""Reproducible test-owned scheduling perturbations for race amplification.

Never monkeypatch the event loop or production code; only yield at explicit
test synchronization seams. Off by default for ordinary stress runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random


async def race_yield(seed: int, point: str, trace: list[dict], *, maximum_ms: int = 15) -> None:
    if os.environ.get("EOAI_RACE_JITTER") != "1":
        return
    identity = f"{seed}:{point}".encode()
    derived = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big")
    rng = random.Random(derived)
    yields = rng.randrange(1, 5)
    delays = []
    for _ in range(yields):
        delay_ms = rng.randrange(maximum_ms + 1)
        delays.append(delay_ms)
        await asyncio.sleep(delay_ms / 1000)
    trace.append({
        "operation": "race_jitter",
        "point": point,
        "seed": seed,
        "delays_ms": delays,
    })
