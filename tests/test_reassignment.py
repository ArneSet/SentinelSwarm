"""Mission reassignment after a drone failure."""

from __future__ import annotations

from sentinelswarm.agents.base import COMMS_LOSS
from sentinelswarm.domain.states import MissionStatus
from sentinelswarm.sim.orchestrator import FleetOrchestrator
from sentinelswarm.sim.scenario import build_demo_scenario


async def test_failed_drone_mission_is_reassigned_and_completed(
    orchestrator: FleetOrchestrator,
) -> None:
    build_demo_scenario(orchestrator, num_drones=3)
    await orchestrator.advance(6.0)

    orchestrator.inject_fault("sim-2", COMMS_LOSS)
    await orchestrator.advance(90.0)  # detection + reassignment + completion + RTB

    missions = orchestrator.manager.state.list_missions()
    assert len(missions) == 3
    # Every mission ends up completed despite one drone dying mid-patrol.
    assert all(m.status is MissionStatus.COMPLETED for m in missions)

    # At least one mission required a reassignment (attempts > 1) done by a live drone.
    reassigned = [m for m in missions if m.retry.attempts > 1]
    assert reassigned, "expected at least one reassignment"
    for mission in reassigned:
        assert mission.assigned_drone != "sim-2"

    assert orchestrator.metrics.reassignments_total._value.get() >= 1.0


async def test_no_reassignment_when_no_failure(orchestrator: FleetOrchestrator) -> None:
    build_demo_scenario(orchestrator, num_drones=3)
    await orchestrator.advance(90.0)
    missions = orchestrator.manager.state.list_missions()
    assert all(m.status is MissionStatus.COMPLETED for m in missions)
    assert all(m.retry.attempts == 1 for m in missions)
    assert orchestrator.metrics.reassignments_total._value.get() == 0.0
