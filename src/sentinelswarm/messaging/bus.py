"""Message bus abstraction plus a dependency-free in-memory implementation.

The bus is the seam that decouples the control plane from drones. For the
simulation it runs entirely in-process, but the interface mirrors a subject-based
broker (NATS/MQTT) so the implementation can be swapped without touching fleet logic.

Distributed-systems properties handled here:

* **Subject routing** with NATS-style ``*`` (one token) and ``>`` (tail) wildcards.
* **Fan-out** delivery with **per-subscription FIFO ordering**.
* **Isolation**: each subscriber receives its own deep copy of the event, so a
  buggy handler cannot corrupt another subscriber's view (mirrors serialisation
  across a real network boundary).
* **At-least-once handler execution** with handler errors isolated per message.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from .events import Event

logger = logging.getLogger("sentinelswarm.bus")

Handler = Callable[[str, Event], Awaitable[None]]

# Safety cap so a misbehaving handler cannot spin drain() forever.
_MAX_DRAIN_ITERATIONS = 100_000


def subject_matches(subject: str, pattern: str) -> bool:
    """NATS-style subject matching.

    ``*`` matches exactly one token; ``>`` matches one or more trailing tokens and
    must be the final token in the pattern.
    """

    if pattern == subject:
        return True
    subj_tokens = subject.split(".")
    pat_tokens = pattern.split(".")
    for i, pat in enumerate(pat_tokens):
        if pat == ">":
            # Matches the remainder; must have at least one token left.
            return i < len(subj_tokens)
        if i >= len(subj_tokens):
            return False
        if pat == "*":
            continue
        if pat != subj_tokens[i]:
            return False
    return len(subj_tokens) == len(pat_tokens)


@runtime_checkable
class MessageBus(Protocol):
    """Interface every bus implementation must satisfy."""

    async def publish(self, subject: str, event: Event) -> None: ...

    def publish_nowait(self, subject: str, event: Event) -> None: ...

    def subscribe(self, pattern: str, handler: Handler) -> Subscription: ...

    async def close(self) -> None: ...


class Subscription:
    """A single subscription with its own FIFO queue and worker task."""

    def __init__(self, bus: InMemoryBus, pattern: str, handler: Handler) -> None:
        self._bus = bus
        self.pattern = pattern
        self._handler = handler
        self._queue: asyncio.Queue[tuple[str, Event]] = asyncio.Queue()
        self.active = True
        self._task: asyncio.Task[None] = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            subject, event = await self._queue.get()
            try:
                if self.active:
                    await self._handler(subject, event)
            except Exception:  # never let one bad message kill the subscriber
                logger.exception("subscriber handler failed", extra={"subject": subject})
            finally:
                self._bus._on_delivered()
                self._queue.task_done()

    def _enqueue(self, subject: str, event: Event) -> None:
        self._queue.put_nowait((subject, event))

    def unsubscribe(self) -> None:
        self.active = False
        self._task.cancel()
        self._bus._remove(self)


class InMemoryBus:
    """In-process bus with subject routing and drain support for tests/sim."""

    def __init__(self) -> None:
        self._subs: list[Subscription] = []
        self._inflight = 0

    def subscribe(self, pattern: str, handler: Handler) -> Subscription:
        sub = Subscription(self, pattern, handler)
        self._subs.append(sub)
        return sub

    def publish_nowait(self, subject: str, event: Event) -> None:
        """Enqueue ``event`` to every matching subscriber and account for it now.

        In-flight accounting happens synchronously so :meth:`drain` observes the
        message immediately, even for fire-and-forget publishers.
        """

        for sub in list(self._subs):
            if sub.active and subject_matches(subject, sub.pattern):
                self._inflight += 1
                sub._enqueue(subject, event.model_copy(deep=True))

    async def publish(self, subject: str, event: Event) -> None:
        self.publish_nowait(subject, event)

    async def drain(self) -> None:
        """Block until every queued message has been handled.

        Deterministic primitive for tests and the simulation loop: it processes
        cascading publishes until the bus is quiescent.
        """

        iterations = 0
        while self._inflight > 0:
            await asyncio.sleep(0)
            iterations += 1
            if iterations > _MAX_DRAIN_ITERATIONS:  # pragma: no cover - safety net
                raise RuntimeError("bus.drain exceeded iteration cap; possible message storm")

    async def close(self) -> None:
        for sub in list(self._subs):
            sub.unsubscribe()
        self._subs.clear()

    # -- internal --------------------------------------------------------
    def _on_delivered(self) -> None:
        self._inflight = max(0, self._inflight - 1)

    def _remove(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    @property
    def subscription_count(self) -> int:
        return len(self._subs)
