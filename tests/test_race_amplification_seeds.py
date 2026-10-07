"""Contract for reproducible nonduplicated race-amplification seeds."""

import pytest

from ci.race_amplification_seeds import seeds


def test_race_seed_generation_is_reproducible_and_distinct():
    a = seeds(7319, 32)
    assert a == seeds(7319, 32)
    assert a[0] == 7319
    assert len(a) == len(set(a)) == 32
    assert all(0 <= seed < 2**32 for seed in a)
    assert seeds(7320, 32) != a


@pytest.mark.parametrize("base,count", [(-1, 5), (2**32, 5), (0, 0), (0, 65)])
def test_race_seed_generator_rejects_invalid_campaigns(base, count):
    with pytest.raises(ValueError):
        seeds(base, count)
