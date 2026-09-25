"""Event model — the typed messages that flow across the bus.

Two directions:

* **Uplink** (drone -> control plane): registration, heartbeats, telemetry,
  mission progress, faults, vision events.
* **Downlink** (control plane -> drone): mission assignment, abort, return-to-base,
  ping.

Every event carries envelope metadata (``event_id``, ``ts``, ``source``,
``correlation_id``, ``seq``) so the control plane can implement idempotency
(dedup by ``event_id``), event ordering (per-source ``seq``) and end-to-end
tracing (``correlation_id``). Events are pydantic models so they serialise
cleanly to JSON when the in-memory bus is later swapped for NATS/MQTT.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field

from ..domain.geometry import Position
from ..domain.states import DroneState


class EventType(str, Enum):
    # uplink: drone -> control plane
    DRONE_REGISTERED = "drone.registered"
    HEARTBEAT = "drone.heartbeat"
    TELEMETRY = "drone.telemetry"
    STATE_CHANGED = "drone.state_changed"
    MISSION_ACCEPTED = "mission.accepted"
    MISSION_PROGRESS = "mission.progress"
    MISSION_COMPLETED = "mission.completed"
    MISSION_FAILED = "mission.failed"
    FAULT_DETECTED = "drone.fault"
    VISION_EVENT = "vision.event"

    # downlink: control plane -> drone
    ASSIGN_MISSION = "command.assign_mission"
    ABORT_MISSION = "command.abort_mission"
    RETURN_TO_BASE = "command.return_to_base"
    PING = "command.ping"

    # control-plane / operator facing
    MISSION_CREATED = "fleet.mission_created"
    MISSION_ASSIGNED = "fleet.mission_assigned"
    MISSION_REASSIGNED = "fleet.mission_reassigned"
    INCIDENT_CREATED = "fleet.incident_created"
    DRONE_OFFLINE = "fleet.drone_offline"


def _new_event_id() -> str:
    return uuid.uuid4().hex


class Event(BaseModel):
    """Base envelope shared by every event."""

    type: EventType
    event_id: str = Field(default_factory=_new_event_id)
    source: str = "unknown"
    correlation_id: str | None = None
    ts: float = 0.0
    seq: int = 0


# ---------------------------------------------------------------------------
# Uplink events
# ---------------------------------------------------------------------------
class DroneRegistered(Event):
    type: EventType = EventType.DRONE_REGISTERED
    drone_id: str
    kind: str = "SIMULATED"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    battery_pct: float = 100.0


class Heartbeat(Event):
    type: EventType = EventType.HEARTBEAT
    drone_id: str
    battery_pct: float
    state: DroneState


class Telemetry(Event):
    type: EventType = EventType.TELEMETRY
    drone_id: str
    x: float
    y: float
    z: float
    battery_pct: float
    state: DroneState
    speed_mps: float = 0.0

    @property
    def position(self) -> Position:
        return Position(self.x, self.y, self.z)


class MissionAccepted(Event):
    type: EventType = EventType.MISSION_ACCEPTED
    drone_id: str
    mission_id: str


class MissionProgress(Event):
    type: EventType = EventType.MISSION_PROGRESS
    drone_id: str
    mission_id: str
    progress: float = 0.0  # 0..1
    phase: str = ""


class MissionCompleted(Event):
    type: EventType = EventType.MISSION_COMPLETED
    drone_id: str
    mission_id: str


class MissionFailed(Event):
    type: EventType = EventType.MISSION_FAILED
    drone_id: str
    mission_id: str
    reason: str = ""


class FaultDetected(Event):
    type: EventType = EventType.FAULT_DETECTED
    drone_id: str
    fault_code: str
    detail: str = ""


class VisionEvent(Event):
    type: EventType = EventType.VISION_EVENT
    drone_id: str
    category: str
    confidence: float = 0.0
    zone_id: str | None = None


# ---------------------------------------------------------------------------
# Downlink commands
# ---------------------------------------------------------------------------
class AssignMission(Event):
    type: EventType = EventType.ASSIGN_MISSION
    drone_id: str
    mission_id: str
    mission_type: str
    target_x: float
    target_y: float
    target_z: float
    zone_id: str | None = None
    zone_radius: float = 0.0


class AbortMission(Event):
    type: EventType = EventType.ABORT_MISSION
    drone_id: str
    mission_id: str
    reason: str = ""


class ReturnToBase(Event):
    type: EventType = EventType.RETURN_TO_BASE
    drone_id: str
    reason: str = ""


class Ping(Event):
    type: EventType = EventType.PING
    drone_id: str
    nonce: str = ""


# ---------------------------------------------------------------------------
# Control-plane / operator-facing events
# ---------------------------------------------------------------------------
class IncidentCreated(Event):
    type: EventType = EventType.INCIDENT_CREATED
    incident_id: str
    drone_id: str | None = None
    mission_id: str | None = None
    category: str = ""
    detail: str = ""


class DroneOffline(Event):
    type: EventType = EventType.DRONE_OFFLINE
    drone_id: str
    last_seen_s_ago: float = 0.0


class MissionCreated(Event):
    type: EventType = EventType.MISSION_CREATED
    mission_id: str
    mission_type: str
    priority: int = 20
    zone_id: str | None = None


class MissionAssigned(Event):
    type: EventType = EventType.MISSION_ASSIGNED
    mission_id: str
    drone_id: str


class MissionReassigned(Event):
    type: EventType = EventType.MISSION_REASSIGNED
    mission_id: str
    from_drone: str | None = None
    to_drone: str | None = None
    reason: str = ""
