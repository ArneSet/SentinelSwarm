"""Fleet orchestrator: wires the bus, clock, manager and drone agents together.

This is the object that makes the fleet *seamlessly scalable*:

* :meth:`add_drone` accepts any :class:`DroneDriver` — a ``SimulatedDriver`` today
  or a real ``Ros2Px4Driver`` tomorrow — and the control plane cannot tell the
  difference.
* :meth:`add_simulated_drone` / :meth:`remove_drone` add or retire vehicles at
  runtime, so the demo can start with 3 simulated drones and grow or shrink.
* :meth:`advance` provides a deterministic virtual-time step for tests and the
  accelerated CLI demo (pairs a :class:`ManualClock` with ``bus.drain()``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..agents.base import AgentConfig, DroneAgent, DroneDriver
from ..agents.simulated import SimParams, SimulatedDriver
from ..config import Settings, load_settings
from ..domain.clock import Clock, ManualClock
from ..domain.geometry import Position, Zone
from ..domain.mission import Mission
from ..domain.states import MissionPriority, MissionType
from ..fleet.manager import FleetManager
from ..messaging.bus import InMemoryBus, MessageBus
from ..observability.logging import get_logger
from ..observability.metrics import Metrics

logger = get_logger("sentinelswarm.orchestrator")


@dataclass(slots=True)
class _AgentHandle:
    agent: DroneAgent
    task: asyncio.Task[None]
    driver: DroneDriver


class FleetOrchestrator:
    """Owns the manager and all drone agents for one simulated (or hybrid) fleet."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
        bus: MessageBus | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.clock = clock or ManualClock()
        self.bus = bus or InMemoryBus()
        self.metrics = metrics or Metrics()
        self.manager = FleetManager(
            bus=self.bus, clock=self.clock, settings=self.settings, metrics=self.metrics
        )
        self.base = Position(self.settings.base_x, self.settings.base_y, self.settings.base_z)
        self._agents: dict[str, _AgentHandle] = {}
        self._sim_counter = 0

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        await self.manager.start()

    async def stop(self) -> None:
        for handle in list(self._agents.values()):
            handle.agent.stop()
            handle.task.cancel()
        self._agents.clear()
        await self.manager.stop()
        if isinstance(self.bus, InMemoryBus):
            await self.bus.close()

    # ------------------------------------------------------------------
    # fleet composition (seamless add/remove of real or simulated drones)
    # ------------------------------------------------------------------
    def add_drone(self, driver: DroneDriver, *, config: AgentConfig | None = None) -> str:
        agent = DroneAgent(
            driver=driver,
            bus=self.bus,
            clock=self.clock,
            home=self.base,
            config=config or AgentConfig.from_settings(self.settings),
        )
        task = asyncio.create_task(agent.run())
        self._agents[driver.drone_id] = _AgentHandle(agent, task, driver)
        logger.info("drone agent added", extra={"drone_id": driver.drone_id})
        return driver.drone_id

    def add_simulated_drone(
        self,
        drone_id: str | None = None,
        *,
        start: Position | None = None,
        battery_pct: float = 100.0,
    ) -> str:
        self._sim_counter += 1
        drone_id = drone_id or f"sim-{self._sim_counter}"
        driver = SimulatedDriver(
            drone_id=drone_id,
            start=start or self.base,
            battery_pct=battery_pct,
            params=SimParams.from_settings(self.settings),
        )
        return self.add_drone(driver)

    def remove_drone(self, drone_id: str) -> bool:
        handle = self._agents.pop(drone_id, None)
        if handle is None:
            return False
        handle.agent.stop()
        handle.task.cancel()
        self.manager.state.remove_drone(drone_id)
        logger.info("drone removed", extra={"drone_id": drone_id})
        return True

    def inject_fault(self, drone_id: str, code: str) -> bool:
        handle = self._agents.get(drone_id)
        if handle is None:
            return False
        handle.driver.inject_fault(code)
        logger.warning("fault injected", extra={"drone_id": drone_id, "code": code})
        return True

    # ------------------------------------------------------------------
    # mission helpers
    # ------------------------------------------------------------------
    def create_patrol_mission(
        self, zone: Zone, priority: int = MissionPriority.NORMAL.value
    ) -> Mission:
        return self.manager.create_mission(MissionType.PATROL_ZONE, zone=zone, priority=priority)

    def create_inspection_mission(
        self, location: Position, priority: int = MissionPriority.HIGH.value
    ) -> Mission:
        return self.manager.create_mission(
            MissionType.INSPECT_LOCATION, location=location, priority=priority
        )

    # ------------------------------------------------------------------
    # deterministic virtual-time stepping (ManualClock only)
    # ------------------------------------------------------------------
    async def advance(self, seconds: float) -> None:
        """Advance virtual time and flush the bus (deterministic for tests/CLI)."""

        if not isinstance(self.clock, ManualClock):
            raise RuntimeError("advance() requires a ManualClock")
        # Let freshly-created agent/loop tasks initialise at the current instant
        # before time jumps forward, so the first scheduler tick sees the drones.
        await self._settle()
        await self.clock.advance(seconds)
        await self._settle()

    async def _settle(self) -> None:
        if isinstance(self.bus, InMemoryBus):
            await self.bus.drain()
            for _ in range(6):
                await asyncio.sleep(0)
                await self.bus.drain()

    @property
    def agent_ids(self) -> list[str]:
        return list(self._agents.keys())
