"""Idempotency and ownership guards in the fleet manager.

These cover the distributed-systems edge cases: duplicate delivery (at-least-once)
and stale events from a drone that no longer owns a mission.
"""

from __future__ import annotations

from sentinelswarm.config import Settings
from sentinelswarm.domain.clock import ManualClock
from sentinelswarm.domain.drone import Drone, HealthStatus
from sentinelswarm.domain.geometry import Position, Zone
from sentinelswarm.domain.mission import Mission
from sentinelswarm.domain.states import DroneState, MissionStatus, MissionType
from sentinelswarm.fleet.manager import FleetManager
from sentinelswarm.messaging.bus import InMemoryBus
from sentinelswarm.messaging.events import MissionCompleted
from sentinelswarm.observability.metrics import Metrics


def _manager() -> FleetManager:
    return FleetManager(
        bus=InMemoryBus(), clock=ManualClock(), settings=Settings(), metrics=Metrics()
    )


def _assigned(mgr: FleetManager, drone_id: str, mission_id: str) -> Mission:
    drone = Drone(
        drone_id=drone_id,
        state=DroneState.PATROLLING,
        position=Position(0, 0, 0),
        battery_pct=90,
        health=HealthStatus.HEALTHY,
        last_heartbeat_at=1.0,
    )
    mgr.state.add_drone(drone)
    mission = Mission(
        mission_id=mission_id,
        type=MissionType.PATROL_ZONE,
        zone=Zone("z1", Position(100, 0, 0), 20),
        created_at=0.0,
    )
    mission.assign(drone_id, now=0.0)
    drone.current_mission_id = mission_id
    mgr.state.add_mission(mission)
    return mission


def test_is_duplicate_tracks_seen_ids() -> None:
    mgr = _manager()
    assert mgr._is_duplicate("abc") is False
    assert mgr._is_duplicate("abc") is True


async def test_duplicate_event_is_processed_once() -> None:
    mgr = _manager()
    mission = _assigned(mgr, "d1", "m1")
    event = MissionCompleted(drone_id="d1", mission_id="m1", ts=1.0)

    await mgr._handle("uplink.event.d1", event)
    assert mission.status is MissionStatus.COMPLETED

    # Tamper, then redeliver the *same* event id: dedup must ignore it.
    mission.status = MissionStatus.IN_PROGRESS
    await mgr._handle("uplink.event.d1", event)
    assert mission.status is MissionStatus.IN_PROGRESS


async def test_event_from_non_owner_drone_is_ignored() -> None:
    mgr = _manager()
    mission = _assigned(mgr, "d1", "m1")
    stale = MissionCompleted(drone_id="d2", mission_id="m1", ts=1.0)
    await mgr._handle("uplink.event.d2", stale)
    assert mission.status is not MissionStatus.COMPLETED
