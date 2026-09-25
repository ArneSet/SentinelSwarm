"""End-to-end simulation: 3 drones, one failure, automatic reassignment, RTB.

This is the executable version of the documented demo scenario. It runs entirely
in deterministic virtual time.
"""

from __future__ import annotations

from sentinelswarm.agents.base import COMMS_LOSS
from sentinelswarm.domain.states import DroneState, MissionStatus
from sentinelswarm.fleet.incidents import IncidentCategory
from sentinelswarm.sim.orchestrator import FleetOrchestrator
from sentinelswarm.sim.scenario import build_demo_scenario


async def test_end_to_end_patrol_with_failure_and_recovery(
    orchestrator: FleetOrchestrator,
) -> None:
    scenario = build_demo_scenario(orchestrator, num_drones=3)

    # 1) launch + dispatch: every drone should pick up a patrol mission.
    await orchestrator.advance(6.0)
    assert len(orchestrator.manager.state.active_missions()) == 3

    # 2) inject a silent comms loss on one drone mid-patrol.
    assert orchestrator.inject_fault("sim-2", COMMS_LOSS)

    # 3) run through detection, reassignment, completion and return-to-base.
    await orchestrator.advance(90.0)

    state = orchestrator.manager.state
    missions = state.list_missions()

    # every mission completes despite the failure
    assert len(missions) == 3
    assert all(m.status is MissionStatus.COMPLETED for m in missions)

    # the failed drone is offline and did not finish anyone's mission
    sim2 = state.get_drone("sim-2")
    assert sim2 is not None
    assert sim2.state is DroneState.OFFLINE
    for mission in missions:
        assert mission.assigned_drone != "sim-2"

    # a comms-loss incident was recorded and a reassignment happened
    categories = [i.category for i in state.incidents.list()]
    assert IncidentCategory.COMMS_LOSS in categories
    assert orchestrator.metrics.reassignments_total._value.get() >= 1.0

    # surviving drones returned home (idle or charging)
    for drone_id in ("sim-1", "sim-3"):
        drone = state.get_drone(drone_id)
        assert drone is not None
        assert drone.state in (DroneState.IDLE, DroneState.CHARGING)

    # fleet summary reflects a 100% success rate
    summary = orchestrator.manager.fleet_summary()
    assert summary["mission_success_rate"] == 1.0
    assert summary["fleet_size"] == 3
    assert scenario.drone_ids == ["sim-1", "sim-2", "sim-3"]
