"""Drone domain entity: the control-plane "digital twin" of a fleet member.

The twin holds the *last known* state, pose, battery and health of a drone. It is
updated exclusively from telemetry/heartbeats coming off the message bus, and it
enforces the state machine defined in :mod:`sentinelswarm.domain.states`.

The same twin represents a **simulated** or a **real** drone; the only difference
is which driver produces the telemetry. That is what lets the fleet scale from
"3 simulated drones" to "N real + M simulated" without touching fleet logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .geometry import Position
from .states import DroneState, InvalidStateTransition, can_transition


class DroneKind(str, Enum):
    """Whether a fleet member is simulated or a real vehicle."""

    SIMULATED = "SIMULATED"
    REAL = "REAL"


class HealthStatus(str, Enum):
    """Coarse health classification derived from telemetry + heartbeats."""

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


#: States in which a healthy, sufficiently-charged drone can accept new work.
_DISPATCHABLE_STATES: frozenset[DroneState] = frozenset({DroneState.IDLE})


@dataclass(slots=True)
class Drone:
    """Control-plane view of a single drone."""

    drone_id: str
    kind: DroneKind = DroneKind.SIMULATED
    state: DroneState = DroneState.OFFLINE
    position: Position = field(default_factory=lambda: Position(0.0, 0.0, 0.0))
    home: Position = field(default_factory=lambda: Position(0.0, 0.0, 0.0))
    battery_pct: float = 100.0
    health: HealthStatus = HealthStatus.UNKNOWN
    current_mission_id: str | None = None
    registered_at: float = 0.0
    last_heartbeat_at: float = 0.0
    last_state_change_at: float = 0.0

    # -- state machine ---------------------------------------------------
    def transition(self, dst: DroneState, now: float) -> DroneState:
        """Validate and apply a state transition.

        Raises :class:`InvalidStateTransition` on an illegal edge so bugs surface
        loudly instead of corrupting fleet state.
        """

        if not can_transition(self.state, dst):
            raise InvalidStateTransition(self.state, dst)
        if dst != self.state:
            self.state = dst
            self.last_state_change_at = now
        return self.state

    # -- telemetry ingest ------------------------------------------------
    def record_heartbeat(self, now: float) -> None:
        self.last_heartbeat_at = now

    def update_pose(self, position: Position, battery_pct: float, now: float) -> None:
        self.position = position
        self.battery_pct = max(0.0, min(100.0, battery_pct))
        self.last_heartbeat_at = now

    def seconds_since_heartbeat(self, now: float) -> float:
        return now - self.last_heartbeat_at

    # -- queries ---------------------------------------------------------
    def is_available(self, min_battery_pct: float) -> bool:
        """True if the drone can be assigned a new mission right now."""

        return (
            self.state in _DISPATCHABLE_STATES
            and self.health == HealthStatus.HEALTHY
            and self.current_mission_id is None
            and self.battery_pct >= min_battery_pct
        )
