"""Deterministic fleet scheduler.

Intentionally simple and pure: given the pending missions and the current drones,
it returns a stable list of ``(mission, drone)`` assignments. No hidden state, no
randomness — the same inputs always yield the same plan, which makes it trivial to
reason about and test. Sophisticated optimisation (VRP solvers, etc.) can replace
:meth:`FleetScheduler.plan` later without touching the rest of the control plane.

Selection policy, in order:

1. Consider missions by **priority** (desc), then age, then id.
2. Consider only **available** drones (idle, healthy, above the dispatch battery).
3. Reject drones without enough battery for the estimated **round trip + margin**.
4. Pick the **closest** feasible drone; break ties by drone id for determinism.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from ..domain.drone import Drone
from ..domain.geometry import Position
from ..domain.mission import Mission


@dataclass(slots=True)
class Assignment:
    mission_id: str
    drone_id: str
    estimated_cost_pct: float


@dataclass(slots=True)
class SchedulerConfig:
    min_battery_pct: float
    drain_per_meter_pct: float
    safety_margin_pct: float
    base: Position

    @classmethod
    def from_settings(cls, settings: Settings) -> SchedulerConfig:
        return cls(
            min_battery_pct=settings.battery.min_dispatch_pct,
            drain_per_meter_pct=settings.battery.drain_per_meter_pct,
            safety_margin_pct=settings.scheduler.battery_safety_margin_pct,
            base=Position(settings.base_x, settings.base_y, settings.base_z),
        )


class FleetScheduler:
    """Pure, deterministic mission-to-drone matcher."""

    def __init__(self, config: SchedulerConfig) -> None:
        self.config = config

    def plan(self, missions: list[Mission], drones: list[Drone], now: float) -> list[Assignment]:
        pending = sorted(missions, key=lambda m: (-m.priority, m.created_at, m.mission_id))
        available: dict[str, Drone] = {
            d.drone_id: d for d in drones if d.is_available(self.config.min_battery_pct)
        }

        assignments: list[Assignment] = []
        for mission in pending:
            target = mission.target_position()
            best_id: str | None = None
            best_cost = 0.0
            best_key: tuple[float, str] | None = None
            for drone in available.values():
                cost = self._estimate_cost_pct(drone, target)
                if drone.battery_pct < cost + self.config.safety_margin_pct:
                    continue  # not enough battery for a safe round trip
                key = (drone.position.distance_to(target), drone.drone_id)
                if best_key is None or key < best_key:
                    best_key = key
                    best_id = drone.drone_id
                    best_cost = cost
            if best_id is not None:
                assignments.append(Assignment(mission.mission_id, best_id, best_cost))
                del available[best_id]
        return assignments

    def _estimate_cost_pct(self, drone: Drone, target: Position) -> float:
        """Estimated battery (percent) for drone -> target -> base."""

        outbound = drone.position.distance_to(target)
        inbound = target.distance_to(self.config.base)
        return (outbound + inbound) * self.config.drain_per_meter_pct
