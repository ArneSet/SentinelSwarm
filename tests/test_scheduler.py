"""Deterministic fleet scheduler."""

from __future__ import annotations

from sentinelswarm.domain.drone import Drone, HealthStatus
from sentinelswarm.domain.geometry import Position, Zone
from sentinelswarm.domain.mission import Mission
from sentinelswarm.domain.states import DroneState, MissionPriority, MissionType
from sentinelswarm.fleet.scheduler import FleetScheduler, SchedulerConfig


def _config() -> SchedulerConfig:
    return SchedulerConfig(
        min_battery_pct=40.0,
        drain_per_meter_pct=0.02,
        safety_margin_pct=10.0,
        base=Position(0, 0, 0),
    )


def _drone(drone_id: str, x: float, y: float, battery: float = 100.0) -> Drone:
    return Drone(
        drone_id=drone_id,
        state=DroneState.IDLE,
        position=Position(x, y, 0),
        home=Position(0, 0, 0),
        battery_pct=battery,
        health=HealthStatus.HEALTHY,
        last_heartbeat_at=1.0,
    )


def _patrol(mission_id: str, x: float, y: float, priority: int) -> Mission:
    return Mission(
        mission_id=mission_id,
        type=MissionType.PATROL_ZONE,
        zone=Zone(f"z-{mission_id}", Position(x, y, 0), 20),
        priority=priority,
        created_at=0.0,
    )


def test_assigns_closest_available_drone() -> None:
    sched = FleetScheduler(_config())
    near = _drone("near", 90, 0)
    far = _drone("far", 0, 0)
    mission = _patrol("m1", 100, 0, MissionPriority.NORMAL.value)
    plan = sched.plan([mission], [near, far], now=0.0)
    assert len(plan) == 1
    assert plan[0].drone_id == "near"


def test_tie_break_is_deterministic_by_drone_id() -> None:
    sched = FleetScheduler(_config())
    a = _drone("d-a", 50, 0)
    b = _drone("d-b", -50, 0)  # same distance to (0,100)
    mission = _patrol("m1", 0, 100, MissionPriority.NORMAL.value)
    plan = sched.plan([mission], [b, a], now=0.0)
    assert plan[0].drone_id == "d-a"


def test_higher_priority_mission_wins_scarce_drone() -> None:
    sched = FleetScheduler(_config())
    only = _drone("only", 0, 0)
    normal = _patrol("normal", 100, 0, MissionPriority.NORMAL.value)
    high = _patrol("high", 100, 0, MissionPriority.HIGH.value)
    plan = sched.plan([normal, high], [only], now=0.0)
    assert len(plan) == 1
    assert plan[0].mission_id == "high"


def test_unavailable_drone_below_min_battery_is_skipped() -> None:
    sched = FleetScheduler(_config())
    low = _drone("low", 0, 0, battery=30.0)  # below min_dispatch 40
    mission = _patrol("m1", 100, 0, MissionPriority.NORMAL.value)
    assert sched.plan([mission], [low], now=0.0) == []


def test_drone_without_roundtrip_battery_is_rejected() -> None:
    sched = FleetScheduler(_config())
    # Far target: round trip cost ~ (1000+1000)*0.02 = 40, +10 margin = 50 required.
    weak = _drone("weak", 0, 0, battery=45.0)
    strong = _drone("strong", 0, 0, battery=60.0)
    mission = _patrol("m1", 1000, 0, MissionPriority.NORMAL.value)
    plan = sched.plan([mission], [weak, strong], now=0.0)
    assert len(plan) == 1
    assert plan[0].drone_id == "strong"


def test_no_double_assignment_of_one_drone() -> None:
    sched = FleetScheduler(_config())
    only = _drone("only", 0, 0)
    m1 = _patrol("m1", 100, 0, MissionPriority.HIGH.value)
    m2 = _patrol("m2", 100, 0, MissionPriority.NORMAL.value)
    plan = sched.plan([m1, m2], [only], now=0.0)
    assigned_drones = [a.drone_id for a in plan]
    assert assigned_drones == ["only"]
