"""Pydantic request/response schemas for the control-plane API.

These are the typed boundary between HTTP clients (dashboard, operators) and the
domain. Domain entities are converted into ``*View`` models so internal types are
never leaked directly over the wire.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.drone import Drone
from ..domain.mission import Mission
from ..fleet.incidents import Incident


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "sentinelswarm"


class DroneView(BaseModel):
    drone_id: str
    kind: str
    state: str
    x: float
    y: float
    z: float
    battery_pct: float
    health: str
    current_mission_id: str | None
    last_heartbeat_at: float
    seconds_since_heartbeat: float

    @classmethod
    def from_domain(cls, drone: Drone, now: float) -> DroneView:
        return cls(
            drone_id=drone.drone_id,
            kind=drone.kind.value,
            state=drone.state.value,
            x=drone.position.x,
            y=drone.position.y,
            z=drone.position.z,
            battery_pct=round(drone.battery_pct, 2),
            health=drone.health.value,
            current_mission_id=drone.current_mission_id,
            last_heartbeat_at=drone.last_heartbeat_at,
            seconds_since_heartbeat=round(max(0.0, now - drone.last_heartbeat_at), 2),
        )


class MissionView(BaseModel):
    mission_id: str
    type: str
    status: str
    priority: int
    assigned_drone: str | None
    zone_id: str | None
    target_x: float | None
    target_y: float | None
    target_z: float | None
    created_at: float
    started_at: float | None
    completed_at: float | None
    timeout_s: float
    deadline_at: float | None
    attempts: int
    max_retries: int
    last_error: str | None
    correlation_id: str

    @classmethod
    def from_domain(cls, mission: Mission) -> MissionView:
        target = mission.location or (mission.zone.center if mission.zone else None)
        return cls(
            mission_id=mission.mission_id,
            type=mission.type.value,
            status=mission.status.value,
            priority=mission.priority,
            assigned_drone=mission.assigned_drone,
            zone_id=mission.zone.zone_id if mission.zone else None,
            target_x=target.x if target else None,
            target_y=target.y if target else None,
            target_z=target.z if target else None,
            created_at=mission.created_at,
            started_at=mission.started_at,
            completed_at=mission.completed_at,
            timeout_s=mission.timeout_s,
            deadline_at=mission.deadline_at,
            attempts=mission.retry.attempts,
            max_retries=mission.retry.max_retries,
            last_error=mission.last_error,
            correlation_id=mission.correlation_id,
        )


class IncidentResponse(BaseModel):
    incident_id: str
    category: str
    severity: str
    drone_id: str | None
    mission_id: str | None
    detail: str
    created_at: float
    correlation_id: str | None

    @classmethod
    def from_domain(cls, incident: Incident) -> IncidentResponse:
        return cls(
            incident_id=incident.incident_id,
            category=incident.category.value,
            severity=incident.severity.value,
            drone_id=incident.drone_id,
            mission_id=incident.mission_id,
            detail=incident.detail,
            created_at=incident.created_at,
            correlation_id=incident.correlation_id,
        )


class FleetSummary(BaseModel):
    fleet_size: int
    environment: str
    simulated: int
    real: int
    drones_by_state: dict[str, int]
    healthy: int
    offline: int
    active_missions: int
    pending_missions: int
    mission_success_rate: float | None
    avg_mission_latency_s: float | None
    battery: dict[str, float | None]
    incident_count: int


class SystemStatus(BaseModel):
    environment: str
    fleet_size: int
    simulated: int
    real: int
    telemetry_hz: float
    server_time: float


class CreateMissionRequest(BaseModel):
    type: str = Field(description="PATROL_ZONE | INSPECT_LOCATION | RETURN_TO_BASE | ...")
    x: float = Field(description="Target/zone-center X in metres (ENU).")
    y: float = Field(description="Target/zone-center Y in metres (ENU).")
    z: float = Field(default=15.0, description="Target altitude in metres.")
    radius: float = Field(default=20.0, ge=0, description="Zone radius (PATROL_ZONE only).")
    zone_id: str | None = None
    priority: int = Field(default=20, ge=0, le=100)
    timeout_s: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)


class AddDroneRequest(BaseModel):
    drone_id: str | None = None
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    battery_pct: float = Field(default=100.0, ge=0, le=100)


class InjectFaultRequest(BaseModel):
    code: str = Field(default="comms_loss", description="Fault code, e.g. comms_loss, nav_fault.")


class ActionResponse(BaseModel):
    ok: bool
    detail: str = ""
