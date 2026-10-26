"""Wait for owned work before releasing its resource or reporting cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable


async def async_settle_owned_operation[T](operation: Awaitable[T]) -> T:
    """Drain owned work, giving its failure priority over caller cancellation.

    The caller retains its existing lock/lease for this entire await. This does
    not acquire ownership, retry an action or establish an execution outcome.
    Transaction reconciliation and best-effort cleanup can need other policies.
    """
    task = asyncio.ensure_future(operation)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as err:
            if task.cancelled():
                raise
            cancellation = cancellation or err
        except Exception:
            break
    result = task.result()
    if cancellation is not None:
        raise cancellation
    return result
