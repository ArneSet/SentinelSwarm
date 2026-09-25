"""Clock abstraction so control logic is deterministic and testable.

Production code uses :class:`RealClock`; simulation and tests use
:class:`ManualClock`, which advances only when told to. Every component that
needs "now" or "sleep" depends on this interface rather than the ``time`` /
``asyncio`` modules directly.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Minimal time source used across the control plane."""

    def now(self) -> float:
        """Monotonic-ish seconds since an arbitrary epoch."""

    async def sleep(self, seconds: float) -> None:
        """Suspend the current task for ``seconds`` of clock time."""


class RealClock:
    """Wall-clock backed by :mod:`asyncio`. Used in production/live runs."""

    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ManualClock:
    """Deterministic virtual clock.

    Time only moves forward via :meth:`advance`. Sleeping tasks are released in
    timestamp order, which gives reproducible scheduling in tests and lets the
    demo run far faster than real time.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = start
        self._counter = itertools.count()
        # Min-heap of (wake_time, seq, future).
        self._waiters: list[tuple[float, int, asyncio.Future[None]]] = []

    def now(self) -> float:
        return self._t

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            # Yield control without blocking so cooperative tasks still interleave.
            await asyncio.sleep(0)
            return
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[None] = loop.create_future()
        heapq.heappush(self._waiters, (self._t + seconds, next(self._counter), fut))
        await fut

    async def advance(self, seconds: float) -> None:
        """Advance virtual time by ``seconds``, waking due sleepers in order."""

        target = self._t + seconds
        while self._waiters and self._waiters[0][0] <= target:
            wake_time, _, fut = heapq.heappop(self._waiters)
            self._t = wake_time
            if not fut.done():
                fut.set_result(None)
            # Let woken tasks make progress before continuing to advance.
            await asyncio.sleep(0)
        self._t = target
        await asyncio.sleep(0)

    @property
    def pending_sleepers(self) -> int:
        return len(self._waiters)
