"""End-to-end simulation: 3 drones, one failure, automatic reassignment, RTB.

This is the executable version of the documented demo scenario. It runs entirely
in deterministic virtual time.
"""

from __future__ import annotations

from sentinelswarm.agents.base import COMMS_LOSS
from sentinelswarm.domain.geometry import Position, Zone
from sentinelswarm.domain.states import DroneState, MissionStatus, MissionType
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


async def test_circuit_patrol_continuous_looping(orchestrator: FleetOrchestrator) -> None:
    drone_id = orchestrator.add_simulated_drone("scout-circuit", start=Position(0, 0, 0))
    # Closed loop route: A -> B -> C -> A
    waypoints = [
        Position(20.0, 0.0, 15.0),
        Position(20.0, 20.0, 15.0),
        Position(0.0, 20.0, 15.0),
        Position(20.0, 0.0, 15.0),
    ]
    mission = orchestrator.manager.create_mission(
        MissionType.WAYPOINT_ROUTE,
        waypoints=waypoints,
        preferred_drone_id=drone_id,
        patrol_duration_s=20.0,
    )
    # Advance time through takeoff and circuit traversal
    await orchestrator.advance(10.0)
    agent = orchestrator._agents[drone_id].agent
    assert agent.state in (DroneState.TRANSIT, DroneState.PATROLLING)

    # Advance until mission completes and drone returns
    await orchestrator.advance(40.0)
    st_mission = orchestrator.manager.state.get_mission(mission.mission_id)
    assert st_mission is not None
    assert st_mission.status is MissionStatus.COMPLETED


async def test_zone_patrol_active_exploration(orchestrator: FleetOrchestrator) -> None:
    drone_id = orchestrator.add_simulated_drone("scout-explorer", start=Position(0, 0, 0))
    zone = Zone(zone_id="explore-zone", center=Position(80.0, 80.0, 0.0), radius=35.0)
    mission = orchestrator.manager.create_mission(
        MissionType.PATROL_ZONE,
        zone=zone,
        preferred_drone_id=drone_id,
        patrol_duration_s=25.0,
    )
    assert mission.mission_id is not None
    # Transit to zone
    await orchestrator.advance(15.0)
    agent = orchestrator._agents[drone_id].agent
    assert agent.state is DroneState.PATROLLING
    assert agent._mission is not None
    assert len(agent._mission.exploration_waypoints) > 0

    # Advance time: verify the drone visits different positions inside the zone
    pos_before = Position(agent.driver.position.x, agent.driver.position.y, agent.driver.position.z)
    await orchestrator.advance(6.0)
    pos_after = Position(agent.driver.position.x, agent.driver.position.y, agent.driver.position.z)
    # The drone must have actively moved and explored, not remained static!
    assert pos_before.distance_to(pos_after) > 10.0
