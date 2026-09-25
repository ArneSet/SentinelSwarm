"""Enumerations and the explicit, validated drone state machine.

The transition table is the single source of truth for legal drone-state changes.
Keeping it declarative makes transitions deterministic and easy to test.
"""

from __future__ import annotations

from enum import Enum


class DroneState(str, Enum):
    """Lifecycle states for a drone in the fleet."""

    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    TAKEOFF = "TAKEOFF"
    TRANSIT = "TRANSIT"
    PATROLLING = "PATROLLING"
    RETURNING = "RETURNING"
    CHARGING = "CHARGING"
    FAULT = "FAULT"
    RECOVERING = "RECOVERING"


class MissionType(str, Enum):
    """Supported mission types (all monitoring/inspection oriented)."""

    PATROL_ZONE = "PATROL_ZONE"
    INSPECT_LOCATION = "INSPECT_LOCATION"
    WAYPOINT_ROUTE = "WAYPOINT_ROUTE"
    RETURN_TO_BASE = "RETURN_TO_BASE"
    RECHARGE = "RECHARGE"
    EMERGENCY_RETURN = "EMERGENCY_RETURN"


class MissionStatus(str, Enum):
    """Mission lifecycle status."""

    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REASSIGNED = "REASSIGNED"


class MissionPriority(int, Enum):
    """Higher value == scheduled first."""

    LOW = 10
    NORMAL = 20
    HIGH = 30
    CRITICAL = 40


# ---------------------------------------------------------------------------
# Drone state machine
# ---------------------------------------------------------------------------

#: Legal transitions. Any transition NOT listed here is rejected by ``Drone``.
#: ``FAULT`` and ``OFFLINE`` are reachable from almost every active state because
#: faults and comms-loss can happen at any time; those edges are added below.
_ALLOWED: dict[DroneState, set[DroneState]] = {
    DroneState.OFFLINE: {DroneState.IDLE, DroneState.RECOVERING},
    DroneState.IDLE: {DroneState.ASSIGNED, DroneState.CHARGING, DroneState.RETURNING},
    DroneState.ASSIGNED: {DroneState.TAKEOFF, DroneState.IDLE, DroneState.RETURNING},
    DroneState.TAKEOFF: {DroneState.TRANSIT, DroneState.RETURNING},
    DroneState.TRANSIT: {DroneState.PATROLLING, DroneState.RETURNING},
    DroneState.PATROLLING: {DroneState.RETURNING, DroneState.TRANSIT},
    DroneState.RETURNING: {DroneState.CHARGING, DroneState.IDLE},
    DroneState.CHARGING: {DroneState.IDLE},
    DroneState.FAULT: {DroneState.RECOVERING, DroneState.OFFLINE},
    DroneState.RECOVERING: {DroneState.IDLE, DroneState.OFFLINE, DroneState.FAULT},
}

#: States from which a fault can be raised (i.e. an active/operational drone).
_FAULTABLE: set[DroneState] = {
    DroneState.IDLE,
    DroneState.ASSIGNED,
    DroneState.TAKEOFF,
    DroneState.TRANSIT,
    DroneState.PATROLLING,
    DroneState.RETURNING,
    DroneState.CHARGING,
    DroneState.RECOVERING,
}

#: States from which comms-loss can move the drone to OFFLINE.
_OFFLINEABLE: set[DroneState] = {
    DroneState.IDLE,
    DroneState.ASSIGNED,
    DroneState.TAKEOFF,
    DroneState.TRANSIT,
    DroneState.PATROLLING,
    DroneState.RETURNING,
    DroneState.CHARGING,
    DroneState.FAULT,
    DroneState.RECOVERING,
}


def _build_transition_table() -> dict[DroneState, frozenset[DroneState]]:
    table: dict[DroneState, set[DroneState]] = {
        state: set(targets) for state, targets in _ALLOWED.items()
    }
    for state in _FAULTABLE:
        table[state].add(DroneState.FAULT)
    for state in _OFFLINEABLE:
        table[state].add(DroneState.OFFLINE)
    # Ensure every state has an entry.
    for state in DroneState:
        table.setdefault(state, set())
    return {state: frozenset(targets) for state, targets in table.items()}


#: Fully-expanded, immutable transition table used for validation.
TRANSITIONS: dict[DroneState, frozenset[DroneState]] = _build_transition_table()


def can_transition(src: DroneState, dst: DroneState) -> bool:
    """Return True if ``src -> dst`` is a legal drone-state transition.

    A no-op (``src == dst``) is always allowed so idempotent updates are cheap.
    """

    if src == dst:
        return True
    return dst in TRANSITIONS.get(src, frozenset())


class InvalidStateTransition(RuntimeError):
    """Raised when an illegal drone-state transition is attempted."""

    def __init__(self, src: DroneState, dst: DroneState) -> None:
        super().__init__(f"Illegal drone-state transition: {src.value} -> {dst.value}")
        self.src = src
        self.dst = dst
