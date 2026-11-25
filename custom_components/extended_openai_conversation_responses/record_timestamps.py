"""Chronological comparisons for validated timestamps in stored records."""

from datetime import UTC, datetime
from functools import lru_cache

from homeassistant.util import dt as dt_util

from .strict_json import has_surrogate


def parse_datetime(value: str) -> datetime | None:
    """Use HA's timestamp formats while guarding its native UTF-8 parser."""
    if isinstance(value, str) and has_surrogate(value):
        return None
    return dt_util.parse_datetime(value)


@lru_cache(maxsize=20_000)
def stored_timestamp_instant(value: str) -> datetime:
    """Compare stored timestamps without changing their representation.

    Legacy naive values use UTC. Aware datetime comparison handles offsets
    directly, including instants that would overflow when converted to UTC.
    """
    parsed = parse_datetime(value)
    assert parsed is not None  # Record validation precedes publication.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
