"""Exact semantic oracle shared by genuine nightly journeys and their canaries."""

from collections.abc import Mapping
from typing import Any


def assert_effect_ledger(
    actual: Mapping[str, Any], expected: Mapping[str, Any]
) -> None:
    """Reject missing, additional, misowned, reordered, or changed observed effects."""
    assert actual == expected, (
        f"Observed journey effects differ: {actual!r} != {expected!r}"
    )
