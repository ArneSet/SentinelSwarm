"""Incident model — the audit record raised when something goes wrong.

Every failure the fleet manager reacts to (comms loss, fault, mission timeout,
battery emergency) produces exactly one incident so operators get a durable,
correlated trail of what happened and how the fleet responded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..domain.mission import new_id


class IncidentCategory(str, Enum):
    COMMS_LOSS = "COMMS_LOSS"
    FAULT = "FAULT"
    MISSION_TIMEOUT = "MISSION_TIMEOUT"
    BATTERY_CRITICAL = "BATTERY_CRITICAL"
    MISSION_FAILED = "MISSION_FAILED"


class IncidentSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(slots=True)
class Incident:
    incident_id: str
    category: IncidentCategory
    severity: IncidentSeverity
    created_at: float
    drone_id: str | None = None
    mission_id: str | None = None
    detail: str = ""
    correlation_id: str | None = None
    reassigned_to: str | None = None

    @classmethod
    def create(
        cls,
        category: IncidentCategory,
        now: float,
        *,
        severity: IncidentSeverity = IncidentSeverity.WARNING,
        drone_id: str | None = None,
        mission_id: str | None = None,
        detail: str = "",
        correlation_id: str | None = None,
    ) -> Incident:
        return cls(
            incident_id=new_id("inc"),
            category=category,
            severity=severity,
            created_at=now,
            drone_id=drone_id,
            mission_id=mission_id,
            detail=detail,
            correlation_id=correlation_id,
        )


@dataclass(slots=True)
class IncidentLog:
    """Append-only in-memory incident store (swap for Postgres in production)."""

    _items: list[Incident] = field(default_factory=list)

    def add(self, incident: Incident) -> Incident:
        self._items.append(incident)
        return incident

    def list(self) -> list[Incident]:
        return list(self._items)

    @property
    def count(self) -> int:
        return len(self._items)
