#!/usr/bin/env python3
"""Generate a reproducible, bounded collection of distinct race-campaign seeds."""

from __future__ import annotations

import argparse
import random


def seeds(base: int, count: int) -> list[int]:
    if not 1 <= count <= 64:
        raise ValueError("race seed count must be between 1 and 64")
    if not 0 <= base < 2**32:
        raise ValueError("race base seed must be an unsigned 32-bit integer")
    rng = random.Random(base ^ 0xE0A1FACE)
    result = [base]
    used = {base}
    while len(result) < count:
        candidate = rng.randrange(2**32)
        if candidate not in used:
            result.append(candidate)
            used.add(candidate)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=int)
    parser.add_argument("--count", required=True, type=int)
    args = parser.parse_args()
    for seed in seeds(args.base, args.count):
        print(seed)


if __name__ == "__main__":
    main()
