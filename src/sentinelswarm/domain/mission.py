"""Mission domain entity and its lifecycle helpers.

A :class:`Mission` is a unit of work assigned to exactly one drone at a time.
Reassignment after a failure creates a fresh assignment on the *same* mission
object (incrementing ``attempts``) so the mission's identity and audit trail are
preserved end-to-end.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .geometry import Position, Zone
from .states import MissionPriority, MissionStatus, MissionType

#: Statuses from which no further transition is possible.
TERMINAL_STATUSES: frozenset[MissionStatus] = frozenset(
    {MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED}
)


def new_id(prefix: str) -> str:
    """Short, human-readable unique id (e.g. ``mission-1a2b3c4d``)."""

    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class RetryPolicy:
    """How many times a mission may be reassigned before it is failed."""

    max_retries: int = 2
    attempts: int = 0

    @property
    def reassignments(self) -> int:
        """Number of times the mission has been *re*-assigned (excludes the first)."""

        return max(0, self.attempts - 1)

    @property
    def exhausted(self) -> bool:
        return self.reassignments >= self.max_retries

    def record_attempt(self) -> None:
        self.attempts += 1


@dataclass(slots=True)
class Mission:
    """A monitoring/inspection task tracked by the fleet manager."""

    mission_id: str
    type: MissionType
    priority: int = MissionPriority.NORMAL.value
    zone: Zone | None = None
    location: Position | None = None
    waypoints: list[Position] = field(default_factory=list)
    patrol_duration_s: float | None = None
    preferred_drone_id: str | None = None
    status: MissionStatus = MissionStatus.PENDING
    assigned_drone: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    timeout_s: float = 120.0
    deadline_at: float | None = None
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    correlation_id: str = field(default_factory=lambda: new_id("corr"))
    last_error: str | None = None

    # -- queries ---------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.status in (MissionStatus.ASSIGNED, MissionStatus.IN_PROGRESS)

    def target_position(self) -> Position:
        """The waypoint the drone should fly to for this mission."""

        if self.location is not None:
            return self.location
        if self.waypoints:
            return self.waypoints[0]
        if self.zone is not None:
            return self.zone.center
        raise ValueError(f"Mission {self.mission_id} has neither zone nor location nor waypoints")

    def can_reassign(self) -> bool:
        """True if the mission may be handed to another drone after a failure."""

        return not self.is_terminal and not self.retry.exhausted

    # -- lifecycle transitions ------------------------------------------
    def assign(self, drone_id: str, now: float) -> None:
        self.assigned_drone = drone_id
        self.status = MissionStatus.ASSIGNED
        self.retry.record_attempt()
        if self.deadline_at is None:
            self.deadline_at = now + self.timeout_s

    def mark_in_progress(self, now: float) -> None:
        self.status = MissionStatus.IN_PROGRESS
        if self.started_at is None:
            self.started_at = now

    def mark_completed(self, now: float) -> None:
        self.status = MissionStatus.COMPLETED
        self.completed_at = now

    def mark_failed(self, now: float, error: str) -> None:
        self.status = MissionStatus.FAILED
        self.completed_at = now
        self.last_error = error

    def mark_cancelled(self, now: float) -> None:
        self.status = MissionStatus.CANCELLED
        self.completed_at = now

    def release_for_reassignment(self, error: str) -> None:
        """Detach from the current drone and requeue as PENDING."""

        self.assigned_drone = None
        self.status = MissionStatus.PENDING
        self.started_at = None
        self.last_error = error
