"""Resolve local wall times across timezone transitions."""

from datetime import UTC, date, datetime, time, timedelta, tzinfo


def resolve_wall_time(
    day: date, clock: time, zone: tzinfo, *, ending: bool = False
) -> datetime:
    """Resolve daily wall time: first valid after a gap; outer folds on repeats.

    The two fold candidates bracket a forward gap in UTC. Binary search for
    the first real instant whose local wall time reaches the requested clock,
    rather than shifting the requested minute by the full DST jump.
    """
    wall = datetime.combine(day, clock)
    first = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
    second = wall.replace(tzinfo=zone, fold=1).astimezone(UTC)
    candidates = (first, second)
    valid = [
        instant
        for instant in candidates
        if instant.astimezone(zone).replace(tzinfo=None) == wall
    ]
    if valid:
        return (max(valid) if ending else min(valid)).astimezone(zone)

    lower, upper = min(candidates), max(candidates)
    # IANA forward jumps can be 30 minutes, one hour, or even an entire day.
    # The UTC interval is bounded by the offset jump, not the calendar year.
    while upper - lower > timedelta(microseconds=1):
        midpoint = lower + (upper - lower) // 2
        if midpoint.astimezone(zone).replace(tzinfo=None) < wall:
            lower = midpoint
        else:
            upper = midpoint
    return upper.astimezone(zone)
