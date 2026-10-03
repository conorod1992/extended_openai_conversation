"""Observe entry at a real lock without substituting its exclusion semantics."""

import asyncio


class LockProbe:
    def __init__(self, lock):
        self.lock = lock
        self.attempts = asyncio.Queue()

    async def __aenter__(self):
        self.attempts.put_nowait(asyncio.current_task())
        await self.lock.acquire()
        return self

    async def __aexit__(self, *args):
        self.lock.release()

    async def next_attempt(self):
        return await asyncio.wait_for(self.attempts.get(), timeout=5)
