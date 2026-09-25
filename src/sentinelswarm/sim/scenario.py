"""Demo scenario: 3 drones, patrol zones, one injected failure, auto-reassignment.

The scenario only *composes* the fleet; it does not drive time. A test drives it
with a :class:`ManualClock`; the CLI runner drives it in accelerated virtual time.
Both share this exact setup, so the documented demo and the automated end-to-end
test exercise identical behaviour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..domain.geometry import Position, Zone
from .orchestrator import FleetOrchestrator

# Patrol ring radius (metres) around the base.
_RING_RADIUS_M = 120.0
_ZONE_RADIUS_M = 20.0


def generate_zones(count: int, radius: float = _RING_RADIUS_M) -> list[Zone]:
    """Evenly distribute ``count`` circular patrol zones on a ring around base."""

    zones: list[Zone] = []
    for i in range(count):
        angle = 2.0 * math.pi * i / max(1, count)
        center = Position(radius * math.cos(angle), radius * math.sin(angle), 0.0)
        zones.append(Zone(zone_id=f"zone-{i + 1}", center=center, radius=_ZONE_RADIUS_M))
    return zones


@dataclass(slots=True)
class DemoScenario:
    orchestrator: FleetOrchestrator
    zones: list[Zone] = field(default_factory=list)
    drone_ids: list[str] = field(default_factory=list)
    mission_ids: list[str] = field(default_factory=list)

    def inject_comms_loss(self, drone_id: str) -> bool:
        from ..agents.base import COMMS_LOSS

        return self.orchestrator.inject_fault(drone_id, COMMS_LOSS)


def build_demo_scenario(orchestrator: FleetOrchestrator, *, num_drones: int = 3) -> DemoScenario:
    """Add ``num_drones`` simulated drones and one patrol mission per zone.

    Must be called *after* :meth:`FleetOrchestrator.start` so the manager is
    subscribed when the agents emit their registration events.
    """

    zones = generate_zones(num_drones)
    drone_ids = [orchestrator.add_simulated_drone(f"sim-{i + 1}") for i in range(num_drones)]
    mission_ids = [orchestrator.create_patrol_mission(zone).mission_id for zone in zones]
    return DemoScenario(
        orchestrator=orchestrator,
        zones=zones,
        drone_ids=drone_ids,
        mission_ids=mission_ids,
    )
