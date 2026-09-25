"""Geometry primitives."""

from __future__ import annotations

import math

from sentinelswarm.domain.geometry import Position, Zone


def test_distance_is_euclidean() -> None:
    assert Position(0, 0, 0).distance_to(Position(3, 4, 0)) == 5.0


def test_moved_towards_reaches_when_step_exceeds_distance() -> None:
    start = Position(0, 0, 0)
    target = Position(10, 0, 0)
    assert start.moved_towards(target, 20) == target


def test_moved_towards_partial_step() -> None:
    start = Position(0, 0, 0)
    target = Position(10, 0, 0)
    moved = start.moved_towards(target, 2.5)
    assert math.isclose(moved.x, 2.5)
    assert moved.y == 0.0


def test_moved_towards_zero_distance_is_stable() -> None:
    p = Position(5, 5, 5)
    assert p.moved_towards(p, 3) == p


def test_zone_contains_ignores_altitude() -> None:
    zone = Zone("z1", Position(0, 0, 0), radius=10)
    assert zone.contains(Position(6, 0, 100))
    assert not zone.contains(Position(11, 0, 0))
