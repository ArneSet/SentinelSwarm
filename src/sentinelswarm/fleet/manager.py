"""Fleet manager: the control-plane brain.

Responsibilities:

* **Ingest** uplink telemetry/heartbeats/events off the bus and maintain the
  authoritative fleet state (the drone "twins").
* **Health monitoring**: detect comms loss via heartbeat timeout.
* **Scheduling**: periodically match pending missions to available drones.
* **Failure handling**: on comms loss, fault, timeout or battery emergency —
  update fleet state, raise an incident, and (if safe) reassign the mission.
* **Observability**: keep Prometheus gauges/counters current and stamp events
  with correlation ids.

Distributed-systems concerns handled here: **idempotency** (dedup by
``event_id``), **stale/reordered telemetry** (drop by timestamp), **ownership**
guards (ignore mission events from a drone that no longer owns the mission), and
**at-least-once** delivery tolerance.
"""

from __future__ import annotations

import asyncio
from collections import deque

from ..config import Settings
from ..domain.clock import Clock
from ..domain.drone import Drone, DroneKind, HealthStatus
from ..domain.geometry import Position, Zone
from ..domain.mission import Mission, RetryPolicy, new_id
from ..domain.states import (
    DroneState,
    MissionPriority,
    MissionStatus,
    MissionType,
    can_transition,
)
from ..messaging.bus import MessageBus, Subscription
from ..messaging.events import (
    AbortMission,
    AssignMission,
    DroneOffline,
    DroneRegistered,
    Event,
    FaultDetected,
    Heartbeat,
    IncidentCreated,
    MissionAccepted,
    MissionAssigned,
    MissionCompleted,
    MissionCreated,
    MissionFailed,
    MissionProgress,
    MissionReassigned,
    Telemetry,
)
from ..messaging.topics import (
    ALL_TELEMETRY,
    ALL_UPLINK_EVENTS,
    FLEET_EVENT,
    command_subject,
)
from ..observability.logging import correlation_context, get_logger
from ..observability.metrics import Metrics
from .incidents import Incident, IncidentCategory, IncidentSeverity
from .scheduler import Assignment, FleetScheduler, SchedulerConfig
from .state import FleetState

logger = get_logger("sentinelswarm.fleet")

_MAX_SEEN_IDS = 20_000


class FleetManager:
    """Owns fleet state and runs the monitor + scheduler control loops."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        clock: Clock,
        settings: Settings,
        metrics: Metrics | None = None,
        state: FleetState | None = None,
        scheduler: FleetScheduler | None = None,
    ) -> None:
        self.bus = bus
        self.clock = clock
        self.settings = settings
        self.metrics = metrics or Metrics()
        self.state = state or FleetState()
        self.scheduler = scheduler or FleetScheduler(SchedulerConfig.from_settings(settings))
        self.base = Position(settings.base_x, settings.base_y, settings.base_z)

        self._subs: list[Subscription] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

        self._seen_order: deque[str] = deque()
        self._seen_ids: set[str] = set()
        self._last_telemetry_ts: dict[str, float] = {}
        self._fleet_seq = 0

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        self._subs.append(self.bus.subscribe(ALL_UPLINK_EVENTS, self._handle))
        self._subs.append(self.bus.subscribe(ALL_TELEMETRY, self._handle))
        self._tasks.append(asyncio.create_task(self._monitor_loop()))
        self._tasks.append(asyncio.create_task(self._scheduler_loop()))
        logger.info("fleet manager started", extra={"base": self.base.as_tuple()})

    async def stop(self) -> None:
        self._running = False
        for sub in self._subs:
            sub.unsubscribe()
        self._subs.clear()
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    # ------------------------------------------------------------------
    # operator API (used by the FastAPI layer and scenarios)
    # ------------------------------------------------------------------
    def create_mission(
        self,
        mission_type: MissionType,
        *,
        zone: Zone | None = None,
        location: Position | None = None,
        priority: int = MissionPriority.NORMAL.value,
        timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> Mission:
        now = self.clock.now()
        mission = Mission(
            mission_id=new_id("mission"),
            type=mission_type,
            priority=priority,
            zone=zone,
            location=location,
            status=MissionStatus.PENDING,
            created_at=now,
            timeout_s=timeout_s
            if timeout_s is not None
            else self.settings.mission.default_timeout_s,
            retry=RetryPolicy(
                max_retries=max_retries
                if max_retries is not None
                else self.settings.mission.default_max_retries
            ),
        )
        self.state.add_mission(mission)
        self._emit_fleet(
            MissionCreated(
                mission_id=mission.mission_id,
                mission_type=mission.type.value,
                priority=mission.priority,
                zone_id=zone.zone_id if zone else None,
            ),
            correlation_id=mission.correlation_id,
        )
        logger.info(
            "mission created",
            extra={"mission_id": mission.mission_id, "type": mission.type.value},
        )
        return mission

    def cancel_mission(self, mission_id: str) -> bool:
        mission = self.state.get_mission(mission_id)
        if mission is None or mission.is_terminal:
            return False
        now = self.clock.now()
        if mission.assigned_drone:
            self._send_command(
                AbortMission(
                    drone_id=mission.assigned_drone,
                    mission_id=mission.mission_id,
                    reason="operator_cancel",
                ),
                correlation_id=mission.correlation_id,
            )
            drone = self.state.get_drone(mission.assigned_drone)
            if drone and drone.current_mission_id == mission.mission_id:
                drone.current_mission_id = None
        mission.mark_cancelled(now)
        self.metrics.missions_total.labels(outcome="cancelled").inc()
        return True

    def fleet_summary(self) -> dict[str, object]:
        drones = self.state.list_drones()
        missions = self.state.list_missions()
        completed = [m for m in missions if m.status is MissionStatus.COMPLETED]
        failed = [m for m in missions if m.status is MissionStatus.FAILED]
        terminal = len(completed) + len(failed)
        counts = self.state.counts_by_state()
        batteries = [d.battery_pct for d in drones]
        latencies = [m.completed_at - m.created_at for m in completed if m.completed_at is not None]
        return {
            "fleet_size": len(drones),
            "environment": self.settings.environment,
            "simulated": sum(1 for d in drones if d.kind is DroneKind.SIMULATED),
            "real": sum(1 for d in drones if d.kind is DroneKind.REAL),
            "drones_by_state": {s.value: c for s, c in counts.items() if c},
            "healthy": sum(1 for d in drones if d.health is HealthStatus.HEALTHY),
            "offline": counts[DroneState.OFFLINE],
            "active_missions": len(self.state.active_missions()),
            "pending_missions": len(self.state.pending_missions()),
            "mission_success_rate": (len(completed) / terminal) if terminal else None,
            "avg_mission_latency_s": (sum(latencies) / len(latencies)) if latencies else None,
            "battery": {
                "min": min(batteries) if batteries else None,
                "avg": (sum(batteries) / len(batteries)) if batteries else None,
                "max": max(batteries) if batteries else None,
            },
            "incident_count": self.state.incidents.count,
        }

    # ------------------------------------------------------------------
    # event ingestion
    # ------------------------------------------------------------------
    async def _handle(self, subject: str, event: Event) -> None:
        if self._is_duplicate(event.event_id):
            return
        now = self.clock.now()
        self.metrics.message_latency.observe(max(0.0, now - event.ts))
        with correlation_context(event.correlation_id):
            if isinstance(event, Telemetry):
                self._on_telemetry(event, now)
            elif isinstance(event, Heartbeat):
                self._on_heartbeat(event, now)
            elif isinstance(event, DroneRegistered):
                self._on_registered(event, now)
            elif isinstance(event, MissionAccepted):
                self._on_mission_accepted(event, now)
            elif isinstance(event, MissionProgress):
                self._on_mission_progress(event, now)
            elif isinstance(event, MissionCompleted):
                self._on_mission_completed(event, now)
            elif isinstance(event, MissionFailed):
                self._on_mission_failed(event, now)
            elif isinstance(event, FaultDetected):
                self._on_fault(event, now)

    def _on_registered(self, event: DroneRegistered, now: float) -> None:
        drone = self.state.get_drone(event.drone_id)
        if drone is None:
            drone = Drone(
                drone_id=event.drone_id,
                kind=DroneKind(event.kind),
                state=DroneState.IDLE,
                position=Position(event.x, event.y, event.z),
                home=self.base,
                battery_pct=event.battery_pct,
                health=HealthStatus.HEALTHY,
                registered_at=now,
                last_heartbeat_at=now,
                last_state_change_at=now,
            )
            self.state.add_drone(drone)
            self.metrics.fleet_size.set(len(self.state.list_drones()))
            logger.info(
                "drone registered", extra={"drone_id": drone.drone_id, "kind": drone.kind.value}
            )

    def _on_telemetry(self, event: Telemetry, now: float) -> None:
        drone = self._ensure_drone(event.drone_id, event.battery_pct, now)
        last = self._last_telemetry_ts.get(event.drone_id, float("-inf"))
        if event.ts < last:
            return  # stale/reordered telemetry
        self._last_telemetry_ts[event.drone_id] = event.ts
        drone.update_pose(event.position, event.battery_pct, now)
        self._apply_reported_state(drone, event.state, now)
        self._refresh_health(drone, event.state)
        self.metrics.battery_pct.labels(drone_id=drone.drone_id).set(drone.battery_pct)

    def _on_heartbeat(self, event: Heartbeat, now: float) -> None:
        drone = self._ensure_drone(event.drone_id, event.battery_pct, now)
        drone.battery_pct = event.battery_pct
        drone.record_heartbeat(now)
        self._apply_reported_state(drone, event.state, now)
        self._refresh_health(drone, event.state)
        self.metrics.heartbeats_total.inc()

    def _on_mission_accepted(self, event: MissionAccepted, now: float) -> None:
        mission = self.state.get_mission(event.mission_id)
        if mission is None or not self._owns(mission, event.drone_id):
            return
        drone = self.state.get_drone(event.drone_id)
        if drone is not None:
            drone.current_mission_id = mission.mission_id

    def _on_mission_progress(self, event: MissionProgress, now: float) -> None:
        mission = self.state.get_mission(event.mission_id)
        if mission is None or not self._owns(mission, event.drone_id):
            return
        if mission.status is MissionStatus.ASSIGNED:
            mission.mark_in_progress(now)

    def _on_mission_completed(self, event: MissionCompleted, now: float) -> None:
        mission = self.state.get_mission(event.mission_id)
        if mission is None or mission.is_terminal or not self._owns(mission, event.drone_id):
            return
        mission.mark_completed(now)
        self.metrics.missions_total.labels(outcome="completed").inc()
        self.metrics.mission_latency.observe(now - mission.created_at)
        drone = self.state.get_drone(event.drone_id)
        if drone and drone.current_mission_id == mission.mission_id:
            drone.current_mission_id = None
        logger.info(
            "mission completed",
            extra={"mission_id": mission.mission_id, "drone_id": event.drone_id},
        )

    def _on_mission_failed(self, event: MissionFailed, now: float) -> None:
        mission = self.state.get_mission(event.mission_id)
        if mission is None or mission.is_terminal or not self._owns(mission, event.drone_id):
            return
        reason = event.reason or "unknown"
        # A fault already raised its own incident (ordered before this event).
        if not reason.startswith("fault:"):
            category = (
                IncidentCategory.BATTERY_CRITICAL
                if reason.startswith("battery")
                else IncidentCategory.MISSION_FAILED
            )
            self._raise_incident(
                category,
                now,
                drone_id=event.drone_id,
                mission_id=mission.mission_id,
                detail=reason,
                correlation_id=mission.correlation_id,
            )
        self._fail_or_reassign(mission, reason, now, failed_drone=event.drone_id)

    def _on_fault(self, event: FaultDetected, now: float) -> None:
        drone = self.state.get_drone(event.drone_id)
        if drone is not None:
            drone.health = HealthStatus.UNHEALTHY
        self._raise_incident(
            IncidentCategory.FAULT,
            now,
            severity=IncidentSeverity.CRITICAL,
            drone_id=event.drone_id,
            detail=f"{event.fault_code}: {event.detail}",
        )
        logger.error(
            "fault reported",
            extra={"drone_id": event.drone_id, "fault_code": event.fault_code},
        )

    # ------------------------------------------------------------------
    # control loops
    # ------------------------------------------------------------------
    async def _monitor_loop(self) -> None:
        interval = self.settings.health.heartbeat_interval_s
        while self._running:
            now = self.clock.now()
            self._check_health(now)
            self._check_timeouts(now)
            self._update_metrics(now)
            await self.clock.sleep(interval)

    async def _scheduler_loop(self) -> None:
        interval = self.settings.scheduler.tick_interval_s
        while self._running:
            self._schedule_once(self.clock.now())
            await self.clock.sleep(interval)

    def _check_health(self, now: float) -> None:
        timeout = self.settings.health.heartbeat_timeout_s
        for drone in self.state.list_drones():
            if drone.state is DroneState.OFFLINE or drone.last_heartbeat_at <= 0:
                continue
            silence = drone.seconds_since_heartbeat(now)
            if silence <= timeout:
                continue
            self._apply_transition(drone, DroneState.OFFLINE, now)
            drone.health = HealthStatus.UNHEALTHY
            self._emit_fleet(DroneOffline(drone_id=drone.drone_id, last_seen_s_ago=silence))
            self._raise_incident(
                IncidentCategory.COMMS_LOSS,
                now,
                severity=IncidentSeverity.CRITICAL,
                drone_id=drone.drone_id,
                detail=f"no heartbeat for {silence:.1f}s",
            )
            logger.error(
                "comms loss detected",
                extra={"drone_id": drone.drone_id, "silence_s": round(silence, 1)},
            )
            if drone.current_mission_id:
                mission = self.state.get_mission(drone.current_mission_id)
                if mission is not None:
                    self._fail_or_reassign(mission, "comms_loss", now, failed_drone=drone.drone_id)

    def _check_timeouts(self, now: float) -> None:
        for mission in self.state.active_missions():
            if mission.deadline_at is None or now <= mission.deadline_at:
                continue
            failed_drone = mission.assigned_drone
            if failed_drone:
                self._send_command(
                    AbortMission(
                        drone_id=failed_drone, mission_id=mission.mission_id, reason="timeout"
                    ),
                    correlation_id=mission.correlation_id,
                )
            self._raise_incident(
                IncidentCategory.MISSION_TIMEOUT,
                now,
                drone_id=failed_drone,
                mission_id=mission.mission_id,
                detail=f"exceeded {mission.timeout_s:.0f}s",
                correlation_id=mission.correlation_id,
            )
            self._fail_or_reassign(mission, "timeout", now, failed_drone=failed_drone)

    def _schedule_once(self, now: float) -> None:
        pending = self.state.pending_missions()
        if not pending:
            return
        assignments = self.scheduler.plan(pending, self.state.list_drones(), now)
        for assignment in assignments:
            self._dispatch(assignment, now)

    # ------------------------------------------------------------------
    # dispatch & recovery
    # ------------------------------------------------------------------
    def _dispatch(self, assignment: Assignment, now: float) -> None:
        mission = self.state.get_mission(assignment.mission_id)
        drone = self.state.get_drone(assignment.drone_id)
        if mission is None or drone is None:
            return
        mission.assign(drone.drone_id, now)
        drone.current_mission_id = mission.mission_id
        target = mission.target_position()
        self._send_command(
            AssignMission(
                drone_id=drone.drone_id,
                mission_id=mission.mission_id,
                mission_type=mission.type.value,
                target_x=target.x,
                target_y=target.y,
                target_z=target.z,
                zone_id=mission.zone.zone_id if mission.zone else None,
                zone_radius=mission.zone.radius if mission.zone else 0.0,
            ),
            correlation_id=mission.correlation_id,
        )
        self._emit_fleet(
            MissionAssigned(mission_id=mission.mission_id, drone_id=drone.drone_id),
            correlation_id=mission.correlation_id,
        )
        logger.info(
            "mission dispatched",
            extra={
                "mission_id": mission.mission_id,
                "drone_id": drone.drone_id,
                "attempt": mission.retry.attempts,
            },
        )

    def _fail_or_reassign(
        self, mission: Mission, reason: str, now: float, *, failed_drone: str | None
    ) -> bool:
        # Detach from the drone that just failed.
        if failed_drone:
            drone = self.state.get_drone(failed_drone)
            if drone and drone.current_mission_id == mission.mission_id:
                drone.current_mission_id = None
        if mission.can_reassign():
            mission.release_for_reassignment(reason)
            self.metrics.reassignments_total.inc()
            self._emit_fleet(
                MissionReassigned(
                    mission_id=mission.mission_id, from_drone=failed_drone, reason=reason
                ),
                correlation_id=mission.correlation_id,
            )
            logger.warning(
                "mission requeued for reassignment",
                extra={"mission_id": mission.mission_id, "reason": reason},
            )
            # Try to place it immediately; otherwise the scheduler loop will retry.
            self._schedule_once(now)
            return True
        mission.mark_failed(now, reason)
        self.metrics.missions_total.labels(outcome="failed").inc()
        logger.error(
            "mission failed permanently",
            extra={"mission_id": mission.mission_id, "reason": reason},
        )
        return False

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _ensure_drone(self, drone_id: str, battery_pct: float, now: float) -> Drone:
        drone = self.state.get_drone(drone_id)
        if drone is None:
            drone = Drone(
                drone_id=drone_id,
                state=DroneState.IDLE,
                position=self.base,
                home=self.base,
                battery_pct=battery_pct,
                health=HealthStatus.HEALTHY,
                registered_at=now,
                last_heartbeat_at=now,
                last_state_change_at=now,
            )
            self.state.add_drone(drone)
            self.metrics.fleet_size.set(len(self.state.list_drones()))
        return drone

    def _refresh_health(self, drone: Drone, reported: DroneState) -> None:
        if reported is DroneState.FAULT:
            drone.health = HealthStatus.UNHEALTHY
        elif drone.state is not DroneState.OFFLINE:
            drone.health = HealthStatus.HEALTHY

    def _apply_reported_state(self, drone: Drone, reported: DroneState, now: float) -> None:
        if drone.state is reported:
            return
        if can_transition(drone.state, reported):
            drone.transition(reported, now)
        else:
            # Telemetry can skip intermediate states after message loss; trust the vehicle.
            drone.state = reported
            drone.last_state_change_at = now

    def _apply_transition(self, drone: Drone, dst: DroneState, now: float) -> None:
        if can_transition(drone.state, dst):
            drone.transition(dst, now)
        else:
            drone.state = dst
            drone.last_state_change_at = now

    def _owns(self, mission: Mission, drone_id: str) -> bool:
        return mission.assigned_drone == drone_id

    def _raise_incident(
        self,
        category: IncidentCategory,
        now: float,
        *,
        severity: IncidentSeverity = IncidentSeverity.WARNING,
        drone_id: str | None = None,
        mission_id: str | None = None,
        detail: str = "",
        correlation_id: str | None = None,
    ) -> Incident:
        incident = Incident.create(
            category,
            now,
            severity=severity,
            drone_id=drone_id,
            mission_id=mission_id,
            detail=detail,
            correlation_id=correlation_id,
        )
        self.state.add_incident(incident)
        self.metrics.incidents_total.labels(category=category.value).inc()
        self._emit_fleet(
            IncidentCreated(
                incident_id=incident.incident_id,
                drone_id=drone_id,
                mission_id=mission_id,
                category=category.value,
                detail=detail,
            ),
            correlation_id=correlation_id,
        )
        return incident

    def _update_metrics(self, now: float) -> None:
        drones = self.state.list_drones()
        self.metrics.fleet_size.set(len(drones))
        for state, count in self.state.counts_by_state().items():
            self.metrics.drones_by_state.labels(state=state.value).set(count)
        self.metrics.healthy_drones.set(sum(1 for d in drones if d.health is HealthStatus.HEALTHY))
        self.metrics.offline_drones.set(sum(1 for d in drones if d.state is DroneState.OFFLINE))
        self.metrics.active_missions.set(len(self.state.active_missions()))

    def _is_duplicate(self, event_id: str) -> bool:
        if event_id in self._seen_ids:
            return True
        self._seen_ids.add(event_id)
        self._seen_order.append(event_id)
        if len(self._seen_order) > _MAX_SEEN_IDS:
            oldest = self._seen_order.popleft()
            self._seen_ids.discard(oldest)
        return False

    def _next_fleet_seq(self) -> int:
        self._fleet_seq += 1
        return self._fleet_seq

    def _emit_fleet(self, event: Event, correlation_id: str | None = None) -> None:
        event.ts = self.clock.now()
        event.seq = self._next_fleet_seq()
        event.source = "fleet-manager"
        if correlation_id is not None:
            event.correlation_id = correlation_id
        self.bus.publish_nowait(FLEET_EVENT, event)

    def _send_command(self, command: Event, correlation_id: str | None = None) -> None:
        command.ts = self.clock.now()
        command.seq = self._next_fleet_seq()
        command.source = "fleet-manager"
        if correlation_id is not None:
            command.correlation_id = correlation_id
        drone_id = getattr(command, "drone_id", None)
        if drone_id is None:
            return
        self.bus.publish_nowait(command_subject(drone_id), command)
