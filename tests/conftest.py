"""Shared pytest fixtures for the SentinelSwarm test-suite."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest
import pytest_asyncio

from sentinelswarm.config import Settings
from sentinelswarm.domain.clock import ManualClock
from sentinelswarm.messaging.bus import InMemoryBus
from sentinelswarm.observability.metrics import Metrics
from sentinelswarm.sim.orchestrator import FleetOrchestrator

Pump = Callable[[InMemoryBus, ManualClock, float], Awaitable[None]]


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def metrics() -> Metrics:
    # Fresh registry per test avoids prometheus "duplicated timeseries" errors.
    return Metrics()


@pytest.fixture
def pump() -> Pump:
    async def _pump(bus: InMemoryBus, clock: ManualClock, seconds: float) -> None:
        await clock.advance(seconds)
        await bus.drain()
        for _ in range(6):
            await asyncio.sleep(0)
            await bus.drain()

    return _pump


@pytest_asyncio.fixture
async def orchestrator(settings: Settings, clock: ManualClock, metrics: Metrics) -> object:
    orch = FleetOrchestrator(settings=settings, clock=clock, bus=InMemoryBus(), metrics=metrics)
    await orch.start()
    try:
        yield orch
    finally:
        await orch.stop()
