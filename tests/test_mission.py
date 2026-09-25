"""Mission lifecycle and retry policy."""

from __future__ import annotations

import pytest

from sentinelswarm.domain.geometry import Position, Zone
from sentinelswarm.domain.mission import Mission, RetryPolicy
from sentinelswarm.domain.states import MissionStatus, MissionType


def _patrol_mission() -> Mission:
    return Mission(
        mission_id="m1",
        type=MissionType.PATROL_ZONE,
        zone=Zone("z1", Position(100, 0, 0), 20),
        created_at=0.0,
        timeout_s=60.0,
        retry=RetryPolicy(max_retries=2),
    )


def test_retry_policy_counts_reassignments_not_initial() -> None:
    policy = RetryPolicy(max_retries=2)
    policy.record_attempt()  # initial assignment
    assert policy.reassignments == 0
    assert not policy.exhausted
    policy.record_attempt()  # 1st reassignment
    assert policy.reassignments == 1
    assert not policy.exhausted
    policy.record_attempt()  # 2nd reassignment
    assert policy.reassignments == 2
    assert policy.exhausted


def test_assign_sets_status_deadline_and_attempts() -> None:
    mission = _patrol_mission()
    mission.assign("d1", now=10.0)
    assert mission.status is MissionStatus.ASSIGNED
    assert mission.assigned_drone == "d1"
    assert mission.deadline_at == 70.0
    assert mission.retry.attempts == 1


def test_can_reassign_until_retries_exhausted() -> None:
    mission = _patrol_mission()
    mission.assign("d1", now=0.0)
    assert mission.can_reassign()
    mission.release_for_reassignment("failed")
    mission.assign("d2", now=1.0)
    assert mission.can_reassign()
    mission.release_for_reassignment("failed")
    mission.assign("d3", now=2.0)
    assert not mission.can_reassign()


def test_target_position_prefers_location_then_zone() -> None:
    m = Mission(mission_id="m", type=MissionType.INSPECT_LOCATION, location=Position(5, 6, 7))
    assert m.target_position() == Position(5, 6, 7)


def test_target_position_raises_without_target() -> None:
    m = Mission(mission_id="m", type=MissionType.RETURN_TO_BASE)
    with pytest.raises(ValueError):
        m.target_position()


def test_terminal_status_blocks_reassignment() -> None:
    mission = _patrol_mission()
    mission.mark_completed(now=5.0)
    assert mission.is_terminal
    assert not mission.can_reassign()
