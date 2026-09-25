"""Driver-agnostic drone agent.

The :class:`DroneAgent` owns mission execution and the *vehicle-side* state
machine. It talks to the physical world only through a :class:`DroneDriver`,
which is the seam that makes simulated and real drones interchangeable:

* ``SimulatedDriver`` integrates a kinematic + battery model (this repo).
* A future ``Ros2Px4Driver`` would wrap MAVSDK/PX4 offboard setpoints.

Because the fleet manager only ever sees bus events, it cannot tell a simulated
drone from a real one — which is exactly what lets the fleet scale from "3
simulated" to "N real + M simulated" with no control-plane changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..config import Settings
from ..domain.clock import Clock
from ..domain.drone import DroneKind
from ..domain.geometry import Position, Zone, generate_zone_exploration_waypoints
from ..domain.states import DroneState, MissionType, can_transition
from ..messaging.bus import MessageBus
from ..messaging.events import (
    AbortMission,
    AssignMission,
    DroneRegistered,
    Event,
    EventType,
    FaultDetected,
    Heartbeat,
    MissionAccepted,
    MissionCompleted,
    MissionFailed,
    MissionProgress,
    ReturnToBase,
    Telemetry,
)
from ..messaging.topics import (
    command_subject,
    telemetry_subject,
    uplink_event_subject,
)
from ..observability.logging import get_logger

logger = get_logger("sentinelswarm.agent")

# Fault code that models a total loss of communication (the drone goes silent).
COMMS_LOSS = "comms_loss"

_FLYING_STATES = {
    DroneState.TAKEOFF,
    DroneState.TRANSIT,
    DroneState.PATROLLING,
}


@runtime_checkable
class DroneDriver(Protocol):
    """Vehicle abstraction. Implemented by the simulator (or a real ROS2/PX4 stack)."""

    drone_id: str
    kind: DroneKind

    @property
    def position(self) -> Position: ...

    @property
    def battery_pct(self) -> float: ...

    @property
    def fault_code(self) -> str | None: ...

    async def connect(self) -> None: ...

    def set_setpoint(self, target: Position, speed: float) -> None: ...

    def set_charging(self, on: bool) -> None: ...

    async def step(self, dt: float) -> None: ...

    def inject_fault(self, code: str) -> None: ...

    def clear_fault(self) -> None: ...


@dataclass(slots=True)
class AgentConfig:
    """Tunables for the vehicle-side behaviour."""

    heartbeat_interval_s: float = 1.0
    telemetry_hz: float = 30.0
    cruise_speed_mps: float = 12.0
    takeoff_altitude_m: float = 15.0
    patrol_duration_s: float = 5.0
    reach_tolerance_m: float = 1.5
    battery_critical_pct: float = 15.0
    battery_full_pct: float = 100.0

    @classmethod
    def from_settings(cls, settings: Settings) -> AgentConfig:
        return cls(
            heartbeat_interval_s=settings.health.heartbeat_interval_s,
            telemetry_hz=settings.telemetry_hz,
            battery_critical_pct=settings.battery.critical_pct,
            battery_full_pct=settings.battery.full_pct,
        )


@dataclass(slots=True)
class _ActiveMission:
    mission_id: str
    mission_type: MissionType
    target: Position
    zone: Zone | None
    correlation_id: str | None
    patrol_duration_s: float | None = None
    waypoints: list[Position] = field(default_factory=list)
    waypoint_index: int = 0
    exploration_waypoints: list[Position] = field(default_factory=list)
    exploration_index: int = 0


class DroneAgent:
    """Executes missions for one drone and streams telemetry to the bus."""

    def __init__(
        self,
        driver: DroneDriver,
        bus: MessageBus,
        clock: Clock,
        home: Position,
        config: AgentConfig | None = None,
    ) -> None:
        self.driver = driver
        self.drone_id = driver.drone_id
        self.bus = bus
        self.clock = clock
        self.home = home
        self.cfg = config or AgentConfig()

        self.state = DroneState.OFFLINE
        self._mission: _ActiveMission | None = None
        self._setpoint: Position = home
        self._patrol_started_at: float | None = None
        self._seq = 0
        self._alive = True  # False after comms loss => publishes nothing
        self._running = False
        self._last_t = 0.0
        self._last_heartbeat_at = 0.0

    # -- lifecycle -------------------------------------------------------
    async def run(self) -> None:
        """Main vehicle loop; cancel the task to stop the agent."""

        await self.driver.connect()
        self.bus.subscribe(command_subject(self.drone_id), self._on_command)
        self._last_t = self.clock.now()
        self._last_heartbeat_at = self._last_t
        self._transition(DroneState.IDLE)
        self._emit_registration()
        self._emit_heartbeat(self._last_t)
        self._running = True
        tick = 1.0 / max(1.0, self.cfg.telemetry_hz)
        while self._running:
            now = self.clock.now()
            dt = now - self._last_t
            if dt <= 0:
                dt = tick
            self._last_t = now
            await self.driver.step(dt)
            self._handle_fault()
            if self._alive:
                await self._advance(now)
                # High-rate pose/altitude telemetry for a smooth operator picture.
                self._publish_telemetry(now)
                if now - self._last_heartbeat_at >= self.cfg.heartbeat_interval_s:
                    self._last_heartbeat_at = now
                    self._emit_heartbeat(now)
            await self.clock.sleep(tick)

    def stop(self) -> None:
        self._running = False

    # -- command handling ------------------------------------------------
    async def _on_command(self, subject: str, event: Event) -> None:
        if getattr(event, "drone_id", None) != self.drone_id:
            return
        if event.type is EventType.ASSIGN_MISSION and isinstance(event, AssignMission):
            await self._accept_mission(event)
        elif event.type is EventType.ABORT_MISSION and isinstance(event, AbortMission):
            self._abort(event.reason or "aborted")
        elif event.type is EventType.RETURN_TO_BASE and isinstance(event, ReturnToBase):
            self._recall(event.reason or "recall")

    async def _accept_mission(self, cmd: AssignMission) -> None:
        if self.state is not DroneState.IDLE or self._mission is not None:
            logger.warning(
                "assign rejected: drone busy",
                extra={"drone_id": self.drone_id, "state": self.state.value},
            )
            self._emit(
                uplink_event_subject(self.drone_id),
                MissionFailed(
                    drone_id=self.drone_id, mission_id=cmd.mission_id, reason="drone_busy"
                ),
                correlation_id=cmd.correlation_id,
            )
            return
        waypoints = [Position(wp[0], wp[1], wp[2]) for wp in cmd.waypoints] if cmd.waypoints else []
        target = (
            waypoints[0]
            if waypoints
            else Position(cmd.target_x, cmd.target_y, cmd.target_z or self.cfg.takeoff_altitude_m)
        )
        zone = (
            Zone(cmd.zone_id, Position(cmd.target_x, cmd.target_y, 0.0), cmd.zone_radius)
            if cmd.zone_id
            else None
        )
        self._mission = _ActiveMission(
            mission_id=cmd.mission_id,
            mission_type=MissionType(cmd.mission_type),
            target=target,
            zone=zone,
            correlation_id=cmd.correlation_id,
            patrol_duration_s=cmd.patrol_duration_s,
            waypoints=waypoints,
            waypoint_index=0,
        )
        self._transition(DroneState.ASSIGNED)
        self._emit(
            uplink_event_subject(self.drone_id),
            MissionAccepted(drone_id=self.drone_id, mission_id=cmd.mission_id),
            correlation_id=cmd.correlation_id,
        )

    def _abort(self, reason: str) -> None:
        if self._mission is not None:
            self._emit(
                uplink_event_subject(self.drone_id),
                MissionFailed(
                    drone_id=self.drone_id,
                    mission_id=self._mission.mission_id,
                    reason=reason,
                ),
                correlation_id=self._mission.correlation_id,
            )
        self._clear_mission()
        self._begin_return()

    def _recall(self, reason: str) -> None:
        logger.info("recalled", extra={"drone_id": self.drone_id, "reason": reason})
        self._clear_mission()
        if self.state in _FLYING_STATES or self.state is DroneState.ASSIGNED:
            self._begin_return()

    # -- per-tick state machine -----------------------------------------
    async def _advance(self, now: float) -> None:
        if self._maybe_emergency_return():
            return

        if self.state is DroneState.ASSIGNED:
            # Begin takeoff: climb straight up to cruise altitude.
            pos = self.driver.position
            self._set_target(Position(pos.x, pos.y, self.cfg.takeoff_altitude_m))
            self._transition(DroneState.TAKEOFF)

        elif self.state is DroneState.TAKEOFF:
            if self._reached():
                assert self._mission is not None
                self._set_target(self._mission.target)
                self._transition(DroneState.TRANSIT)
                self._emit_progress(0.3, "transit")

        elif self.state is DroneState.TRANSIT:
            if self._reached():
                assert self._mission is not None
                # If following a multi-waypoint route, step through remaining waypoints
                if self._mission.waypoints and len(self._mission.waypoints) > 1:
                    self._mission.waypoint_index += 1
                    if self._mission.waypoint_index < len(self._mission.waypoints):
                        next_wp = self._mission.waypoints[self._mission.waypoint_index]
                        self._set_target(next_wp)
                        pct = 0.3 + 0.5 * (
                            self._mission.waypoint_index / len(self._mission.waypoints)
                        )
                        self._emit_progress(pct, f"waypoint_{self._mission.waypoint_index + 1}")
                        return
                self._transition(DroneState.PATROLLING)
                self._patrol_started_at = now
                if self._mission.zone:
                    if self._mission.waypoints:
                        self._mission.exploration_waypoints = list(self._mission.waypoints)
                    else:
                        self._mission.exploration_waypoints = generate_zone_exploration_waypoints(
                            self._mission.zone.center,
                            self._mission.zone.radius,
                            altitude=self.cfg.takeoff_altitude_m,
                        )
                    self._mission.exploration_index = 0
                    if self._mission.exploration_waypoints:
                        self._set_target(self._mission.exploration_waypoints[0])
                self._emit_progress(0.8 if self._mission.waypoints else 0.6, "patrolling")

        elif self.state is DroneState.PATROLLING:
            if self._patrol_started_at is None:
                self._patrol_started_at = now
            duration = (
                self._mission.patrol_duration_s
                if (self._mission and self._mission.patrol_duration_s is not None)
                else self.cfg.patrol_duration_s
            )
            if now - self._patrol_started_at >= duration:
                self._complete_mission()
                return

            assert self._mission is not None
            # 1. Zone Loiter: actively cycle through exploration waypoints to survey the full circle
            if self._mission.zone and self._mission.exploration_waypoints:
                if self._reached():
                    self._mission.exploration_index = (self._mission.exploration_index + 1) % len(
                        self._mission.exploration_waypoints
                    )
                    next_point = self._mission.exploration_waypoints[
                        self._mission.exploration_index
                    ]
                    self._set_target(next_point)

            # 2. Waypoint Route: if circuit / loop (first point matches last point),
            # continue flying through the circuit until duration completes
            elif self._mission.waypoints and len(self._mission.waypoints) > 1:
                is_circuit = (
                    self._mission.waypoints[0].distance_to(self._mission.waypoints[-1]) <= 2.0
                )
                if is_circuit and self._reached():
                    self._mission.waypoint_index = (self._mission.waypoint_index + 1) % len(
                        self._mission.waypoints
                    )
                    next_wp = self._mission.waypoints[self._mission.waypoint_index]
                    self._set_target(next_wp)

        elif self.state is DroneState.RETURNING:
            if self._reached():
                self._land_and_settle()

        elif self.state is DroneState.CHARGING:
            if self.driver.battery_pct >= self.cfg.battery_full_pct:
                self.driver.set_charging(False)
                self._transition(DroneState.IDLE)

    def _complete_mission(self) -> None:
        assert self._mission is not None
        self._emit_progress(1.0, "complete")
        self._emit(
            uplink_event_subject(self.drone_id),
            MissionCompleted(drone_id=self.drone_id, mission_id=self._mission.mission_id),
            correlation_id=self._mission.correlation_id,
        )
        self._clear_mission()
        self._begin_return()

    def _begin_return(self) -> None:
        self._set_target(Position(self.home.x, self.home.y, self.cfg.takeoff_altitude_m))
        if self.state is not DroneState.RETURNING:
            self._transition(DroneState.RETURNING)

    def _land_and_settle(self) -> None:
        # Descend to ground at home, then charge if not full, else go idle.
        self._set_target(self.home)
        if self.driver.position.distance_to(self.home) <= self.cfg.reach_tolerance_m:
            if self.driver.battery_pct < self.cfg.battery_full_pct:
                self.driver.set_charging(True)
                self._transition(DroneState.CHARGING)
            else:
                self._transition(DroneState.IDLE)

    def _maybe_emergency_return(self) -> bool:
        if (
            self.state in _FLYING_STATES
            and self.driver.battery_pct <= self.cfg.battery_critical_pct
        ):
            if self._mission is not None:
                self._emit(
                    uplink_event_subject(self.drone_id),
                    MissionFailed(
                        drone_id=self.drone_id,
                        mission_id=self._mission.mission_id,
                        reason="battery_critical",
                    ),
                    correlation_id=self._mission.correlation_id,
                )
                self._clear_mission()
            self._begin_return()
            return True
        return False

    # -- fault handling --------------------------------------------------
    def _handle_fault(self) -> None:
        code = self.driver.fault_code
        if code is None:
            return
        if self.state is DroneState.FAULT:
            return  # already handled
        if code == COMMS_LOSS:
            # Silent failure: stop emitting so the fleet must detect via heartbeat timeout.
            self._alive = False
            self._transition(DroneState.FAULT)
            logger.error("comms loss (silent)", extra={"drone_id": self.drone_id})
            return
        # Reported fault: tell the control plane before holding position.
        self._transition(DroneState.FAULT)
        self._emit(
            uplink_event_subject(self.drone_id),
            FaultDetected(drone_id=self.drone_id, fault_code=code, detail="reported by driver"),
        )
        if self._mission is not None:
            self._emit(
                uplink_event_subject(self.drone_id),
                MissionFailed(
                    drone_id=self.drone_id,
                    mission_id=self._mission.mission_id,
                    reason=f"fault:{code}",
                ),
                correlation_id=self._mission.correlation_id,
            )
            self._clear_mission()

    # -- helpers ---------------------------------------------------------
    def _set_target(self, target: Position) -> None:
        self._setpoint = target
        self.driver.set_setpoint(target, self.cfg.cruise_speed_mps)

    def _reached(self) -> bool:
        return self.driver.position.distance_to(self._setpoint) <= self.cfg.reach_tolerance_m

    def _clear_mission(self) -> None:
        self._mission = None
        self._patrol_started_at = None

    def _transition(self, dst: DroneState) -> None:
        if dst == self.state:
            return
        if not can_transition(self.state, dst):
            # Physical reality wins on the vehicle, but an illegal edge is a bug.
            logger.error(
                "illegal vehicle transition",
                extra={"drone_id": self.drone_id, "from": self.state.value, "to": dst.value},
            )
        self.state = dst

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _stamp(self, event: Event, correlation_id: str | None = None) -> Event:
        event.ts = self.clock.now()
        event.seq = self._next_seq()
        event.source = self.drone_id
        if correlation_id is not None:
            event.correlation_id = correlation_id
        return event

    def _emit(self, subject: str, event: Event, correlation_id: str | None = None) -> None:
        if not self._alive:
            return
        stamped = self._stamp(event, correlation_id)
        # Fire-and-forget; the bus enqueues synchronously so drain() stays deterministic.
        self.bus.publish_nowait(subject, stamped)

    def _emit_registration(self) -> None:
        pos = self.driver.position
        model = getattr(self.driver, "model", "sim-scout")
        name = getattr(self.driver, "name", None)
        host = getattr(self.driver, "host", None)
        port = getattr(self.driver, "port", None)
        protocol = getattr(self.driver, "protocol", None)
        self._emit(
            uplink_event_subject(self.drone_id),
            DroneRegistered(
                drone_id=self.drone_id,
                kind=self.driver.kind.value,
                model=model,
                name=name,
                host=host,
                port=port,
                protocol=protocol,
                x=pos.x,
                y=pos.y,
                z=pos.z,
                battery_pct=self.driver.battery_pct,
            ),
        )

    def _emit_progress(self, progress: float, phase: str) -> None:
        if self._mission is None:
            return
        self._emit(
            uplink_event_subject(self.drone_id),
            MissionProgress(
                drone_id=self.drone_id,
                mission_id=self._mission.mission_id,
                progress=progress,
                phase=phase,
            ),
            correlation_id=self._mission.correlation_id,
        )

    def _emit_heartbeat(self, now: float) -> None:
        self._emit(
            uplink_event_subject(self.drone_id),
            Heartbeat(
                drone_id=self.drone_id, battery_pct=self.driver.battery_pct, state=self.state
            ),
        )

    def _publish_telemetry(self, now: float) -> None:
        pos = self.driver.position
        self._emit(
            telemetry_subject(self.drone_id),
            Telemetry(
                drone_id=self.drone_id,
                x=pos.x,
                y=pos.y,
                z=pos.z,
                battery_pct=self.driver.battery_pct,
                state=self.state,
                speed_mps=self.cfg.cruise_speed_mps if self.state in _FLYING_STATES else 0.0,
            ),
        )
