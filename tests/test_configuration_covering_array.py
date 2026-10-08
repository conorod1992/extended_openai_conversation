"""The generated configuration matrix is complete, repeatable and auditable."""
import json

import pytest

from tests.functions.configuration_covering_array import (
    DIMENSIONS,
    cases,
    interactions,
    report,
    universe,
)


def test_three_way_matrix_has_no_missing_interactions():
    summary = report()
    assert summary["required_interactions"] > 0
    assert summary["missing"] == []
    assert summary["covered_interactions"] == summary["required_interactions"]
    assert summary["selected_cases"] < summary["total_full_combinations"]
    assert set().union(*(interactions(row) for row in cases())) == universe()


def test_generator_is_deterministic_and_has_unique_cases():
    first, second = cases(), cases()
    assert first == second
    assert len({tuple(row.items()) for row in first}) == len(first)
    assert json.loads(json.dumps(report())) == report()


@pytest.mark.parametrize("factor", list(DIMENSIONS))
def test_every_factor_can_change_independently(factor):
    """Witness pairs prove no factor is always masked by another restriction."""
    matrix = [
        dict(zip(DIMENSIONS, combination))
        for combination in __import__("itertools").product(*DIMENSIONS.values())
    ]
    for value in DIMENSIONS[factor]:
        if value == DIMENSIONS[factor][0]:
            continue
        left = next(row for row in matrix if row[factor] == DIMENSIONS[factor][0])
        right = dict(left, **{factor: value})
        assert left in matrix and right in matrix
        assert all(left[name] == right[name] for name in DIMENSIONS if name != factor)


@pytest.mark.parametrize("strength", [0, -1, 7])
def test_invalid_coverage_strength_is_rejected(strength):
    with pytest.raises(ValueError):
        universe(strength)
