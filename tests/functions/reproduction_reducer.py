"""Reduce production reproductions without accepting a different failure."""

from __future__ import annotations

from copy import deepcopy


async def minimize_reproduction(operations, replay, expected_signature):
    """Replay each candidate from fresh state; retain exact operation/schedule fields.

    The replay returns an observed failure signature or None for a healthy or
    inapplicable sequence. Unexpected harness errors propagate to the caller.
    """
    current = deepcopy(operations)
    if not current or await replay(current) != expected_signature:
        raise ValueError("Original operations do not reproduce the intended failure")
    granularity = 2
    attempts = []
    while len(current) >= 2:
        chunk = max(1, len(current) // granularity)
        reduced = False
        for start in range(0, len(current), chunk):
            candidate = current[:start] + current[start + chunk :]
            if not candidate:
                continue
            observed = await replay(deepcopy(candidate))
            attempts.append({"operations": deepcopy(candidate), "observed_signature": observed})
            if observed == expected_signature:
                current = candidate
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if not reduced:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)
    return {"failure_signature": expected_signature, "operations": current, "reduction_attempts": attempts}
