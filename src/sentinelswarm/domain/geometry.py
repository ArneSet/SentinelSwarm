"""Geometric primitives for the local ENU (East-North-Up) simulation frame.

Kept deliberately small and dependency-free so it can run in CI without numpy.
Distances are Euclidean in metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Position:
    """A point in the local ENU frame (metres)."""

    x: float
    y: float
    z: float = 0.0

    def distance_to(self, other: Position) -> float:
        """Euclidean distance to ``other`` in metres."""

        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))

    def moved_towards(self, target: Position, max_step: float) -> Position:
        """Return a new position advanced towards ``target`` by at most ``max_step`` metres."""

        remaining = self.distance_to(target)
        if remaining <= max_step or remaining == 0.0:
            return target
        ratio = max_step / remaining
        return Position(
            x=self.x + (target.x - self.x) * ratio,
            y=self.y + (target.y - self.y) * ratio,
            z=self.z + (target.z - self.z) * ratio,
        )

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True, slots=True)
class Zone:
    """A named, axis-aligned circular patrol/inspection zone.

    A circle keeps containment checks trivial and dependency-free; the ``radius``
    doubles as the patrol loiter radius.
    """

    zone_id: str
    center: Position
    radius: float

    def contains(self, point: Position) -> bool:
        """True if ``point`` lies within the zone radius (ignoring altitude)."""

        planar = math.dist((self.center.x, self.center.y), (point.x, point.y))
        return planar <= self.radius
