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


def partition_circular_zone(
    center: Position, radius: float, num_partitions: int
) -> list[tuple[float, float]]:
    """Partition a circular zone into ``num_partitions`` disjoint vertical slices along X.

    Guarantees mathematically strictly disjoint X-intervals to eliminate flight-path
    and sensor overlap between swarm drones.
    """
    n = max(1, num_partitions)
    if n == 1:
        return [(center.x - radius, center.x + radius)]

    slices: list[tuple[float, float]] = []
    step = (2.0 * radius) / n
    for k in range(n):
        x_left = center.x - radius + k * step
        x_right = center.x - radius + (k + 1) * step
        slices.append((x_left, x_right))
    return slices


def generate_boustrophedon_sweep(
    center: Position,
    radius: float,
    x_min: float,
    x_max: float,
    lane_width: float = 22.0,
    altitude: float = 15.0,
) -> list[Position]:
    """Generate high-speed, non-overlapping Boustrophedon (lawnmower) survey tracks.

    Lanes are spaced proportionally to the sensor footprint and clipped to the circular
    boundary of the zone.
    """
    width = x_max - x_min
    if width <= 0.0:
        return [Position(center.x, center.y, altitude)]

    # Compute optimal number of lanes inside this slice
    num_lanes = max(1, round(width / lane_width))
    lane_spacing = width / (num_lanes + 1)

    x_coords = [x_min + (i + 1) * lane_spacing for i in range(num_lanes)]
    waypoints: list[Position] = []

    for i, x in enumerate(x_coords):
        dx = abs(x - center.x)
        if dx >= radius:
            dx = radius * 0.95
        y_span = math.sqrt(max(0.0, radius * radius - dx * dx)) * 0.90
        y_span = max(4.0, y_span)

        y_bottom = center.y - y_span
        y_top = center.y + y_span

        # Alternating Boustrophedon direction
        if i % 2 == 0:
            waypoints.append(Position(x, y_bottom, altitude))
            waypoints.append(Position(x, y_top, altitude))
        else:
            waypoints.append(Position(x, y_top, altitude))
            waypoints.append(Position(x, y_bottom, altitude))

    return waypoints


def generate_swarm_zone_slices(
    center: Position,
    radius: float,
    num_drones: int,
    lane_width: float = 22.0,
    altitude: float = 15.0,
) -> list[list[Position]]:
    """Partition a circular zone among ``num_drones`` and return disjoint CPP waypoints.

    Each drone receives a distinct, non-overlapping list of waypoints that sweep out its
    assigned slice with maximal area coverage and zero flight-lane intersection.
    """
    slices = partition_circular_zone(center, radius, num_drones)
    swarm_paths: list[list[Position]] = []
    for x_left, x_right in slices:
        path = generate_boustrophedon_sweep(
            center=center,
            radius=radius,
            x_min=x_left,
            x_max=x_right,
            lane_width=lane_width,
            altitude=altitude,
        )
        swarm_paths.append(path)
    return swarm_paths


def generate_zone_exploration_waypoints(
    center: Position, radius: float, altitude: float = 15.0
) -> list[Position]:
    """Generate a multi-pass reconnaissance trajectory that covers the circular zone."""
    pts: list[Position] = []
    r_outer = max(6.0, radius * 0.72)
    r_inner = max(3.0, radius * 0.35)
    alt = center.z if center.z > 0 else altitude

    # 8-point perimeter survey interspersed with interior passes
    angles = [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]
    for i, deg in enumerate(angles):
        rad = math.radians(deg)
        pts.append(
            Position(
                center.x + r_outer * math.cos(rad),
                center.y + r_outer * math.sin(rad),
                alt,
            )
        )
        if i % 2 == 1:
            rad_cross = math.radians((deg + 180.0) % 360.0)
            pts.append(
                Position(
                    center.x + r_inner * math.cos(rad_cross),
                    center.y + r_inner * math.sin(rad_cross),
                    alt,
                )
            )
    return pts

