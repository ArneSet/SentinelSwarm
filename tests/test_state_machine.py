"""Drone state-machine transitions."""

from __future__ import annotations

import pytest

from sentinelswarm.domain.drone import Drone
from sentinelswarm.domain.states import (
    DroneState,
    InvalidStateTransition,
    can_transition,
)


def test_boot_transition_offline_to_idle() -> None:
    assert can_transition(DroneState.OFFLINE, DroneState.IDLE)


def test_same_state_is_noop_allowed() -> None:
    assert can_transition(DroneState.PATROLLING, DroneState.PATROLLING)


def test_fault_reachable_from_every_active_state() -> None:
    for state in (
        DroneState.IDLE,
        DroneState.TAKEOFF,
        DroneState.TRANSIT,
        DroneState.PATROLLING,
        DroneState.RETURNING,
        DroneState.CHARGING,
    ):
        assert can_transition(state, DroneState.FAULT)


def test_offline_reachable_after_comms_loss() -> None:
    assert can_transition(DroneState.PATROLLING, DroneState.OFFLINE)


def test_illegal_transition_is_rejected() -> None:
    assert not can_transition(DroneState.IDLE, DroneState.PATROLLING)
    assert not can_transition(DroneState.CHARGING, DroneState.TRANSIT)


def test_drone_transition_applies_and_timestamps() -> None:
    drone = Drone(drone_id="d1", state=DroneState.OFFLINE)
    drone.transition(DroneState.IDLE, now=12.0)
    assert drone.state is DroneState.IDLE
    assert drone.last_state_change_at == 12.0


def test_drone_transition_raises_on_illegal_edge() -> None:
    drone = Drone(drone_id="d1", state=DroneState.IDLE)
    with pytest.raises(InvalidStateTransition):
        drone.transition(DroneState.PATROLLING, now=1.0)


def test_full_happy_path_is_legal() -> None:
    path = [
        DroneState.IDLE,
        DroneState.ASSIGNED,
        DroneState.TAKEOFF,
        DroneState.TRANSIT,
        DroneState.PATROLLING,
        DroneState.RETURNING,
        DroneState.CHARGING,
        DroneState.IDLE,
    ]
    drone = Drone(drone_id="d1", state=DroneState.OFFLINE)
    drone.transition(DroneState.IDLE, now=0.0)
    for i in range(1, len(path)):
        assert can_transition(path[i - 1], path[i]), f"{path[i - 1]} -> {path[i]}"
        drone.transition(path[i], now=float(i))
    assert drone.state is DroneState.IDLE
