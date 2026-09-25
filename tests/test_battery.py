"""Battery-aware behaviour: agent emergency return on critical battery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sentinelswarm.agents.base import AgentConfig, DroneAgent
from sentinelswarm.agents.simulated import SimParams, SimulatedDriver
from sentinelswarm.domain.clock import ManualClock
from sentinelswarm.domain.geometry import Position
from sentinelswarm.domain.states import DroneState
from sentinelswarm.messaging.bus import InMemoryBus
from sentinelswarm.messaging.events import AssignMission, Event, MissionFailed
from sentinelswarm.messaging.topics import ALL_UPLINK_EVENTS, command_subject

Pump = Callable[[InMemoryBus, ManualClock, float], Awaitable[None]]


async def test_agent_emergency_returns_on_critical_battery(clock: ManualClock, pump: Pump) -> None:
    bus = InMemoryBus()
    home = Position(0, 0, 0)
    driver = SimulatedDriver("sim-x", start=home, battery_pct=100.0, params=SimParams())
    # Trip the critical threshold almost as soon as the drone is airborne.
    agent = DroneAgent(driver, bus, clock, home, AgentConfig(battery_critical_pct=99.0))

    events: list[Event] = []

    async def collect(subject: str, event: Event) -> None:
        events.append(event)

    bus.subscribe(ALL_UPLINK_EVENTS, collect)
    task = asyncio.create_task(agent.run())
    await pump(bus, clock, 0.0)  # register + first telemetry

    command = AssignMission(
        drone_id="sim-x",
        mission_id="m1",
        mission_type="PATROL_ZONE",
        target_x=500,
        target_y=0,
        target_z=15,
        zone_id="z1",
        zone_radius=20,
    )
    command.ts = clock.now()
    bus.publish_nowait(command_subject("sim-x"), command)

    await pump(bus, clock, 6.0)

    reasons = [e.reason for e in events if isinstance(e, MissionFailed)]
    assert "battery_critical" in reasons
    assert agent.state in (DroneState.RETURNING, DroneState.CHARGING, DroneState.IDLE)

    agent.stop()
    task.cancel()
    await bus.close()
