"""Deterministic configuration covering arrays and explicit decision-independence witnesses.

This module is independent of EOAI production validation; a generator must never
silently count an uncovered interaction as covered. The real-HA replay layer can
consume the case IDs and selections exposed here.
"""
from __future__ import annotations

from itertools import combinations, product
from typing import Mapping

DIMENSIONS = {
    "guest": ("off", "on"),
    "identity": ("authenticated", "shared", "unretained"),
    "memory": ("off", "lexical", "hybrid"),
    "continuity": ("ha", "user", "device"),
    "storage": ("fresh", "existing"),
    "route": ("local", "rule", "ai"),
}


def valid(case: Mapping[str, str]) -> bool:
    """Every declared factor combination is meaningful for the current pilot."""
    return all(case.get(name) in values for name, values in DIMENSIONS.items())


def universe(strength: int = 3) -> set[tuple[tuple[str, str], ...]]:
    """All allowed t-way interactions, with no random sampling."""
    if not 1 <= strength <= len(DIMENSIONS):
        raise ValueError("invalid coverage strength")
    names = tuple(DIMENSIONS)
    return {
        tuple(zip(chosen, values))
        for chosen in combinations(names, strength)
        for values in product(*(DIMENSIONS[n] for n in chosen))
    }


def interactions(case: Mapping[str, str], strength: int = 3):
    return {
        tuple((name, case[name]) for name in names)
        for names in combinations(DIMENSIONS, strength)
    }


def cases(strength: int = 3) -> list[dict[str, str]]:
    """Stable greedy set cover; ties use canonical enumeration order."""
    options = [
        dict(zip(DIMENSIONS, values))
        for values in product(*DIMENSIONS.values())
    ]
    options = [case for case in options if valid(case)]
    remaining = universe(strength)
    selected = []
    while remaining:
        best = max(options, key=lambda c: len(interactions(c, strength) & remaining))
        covered = interactions(best, strength) & remaining
        if not covered:
            raise AssertionError("coverage universe contains impossible interactions")
        selected.append(best)
        remaining -= covered
        options.remove(best)
    return selected


def report(strength: int = 3) -> dict:
    chosen = cases(strength)
    expected = universe(strength)
    actual = set().union(*(interactions(c, strength) for c in chosen))
    return {
        "strength": strength,
        "dimensions": {name: list(values) for name, values in DIMENSIONS.items()},
        "total_full_combinations": len(list(product(*DIMENSIONS.values()))),
        "selected_cases": len(chosen),
        "covered_interactions": len(actual),
        "required_interactions": len(expected),
        "missing": [list(item) for item in sorted(expected - actual)],
        "cases": [
            {"id": f"config-{i:03d}", "factors": case}
            for i, case in enumerate(chosen)
        ],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(report(), indent=2))
