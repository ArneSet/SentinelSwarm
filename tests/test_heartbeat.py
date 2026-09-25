"""Health monitoring: comms-loss detection via heartbeat timeout."""

from __future__ import annotations

from sentinelswarm.agents.base import COMMS_LOSS
from sentinelswarm.domain.drone import HealthStatus
from sentinelswarm.domain.states import DroneState
from sentinelswarm.fleet.incidents import IncidentCategory
from sentinelswarm.sim.orchestrator import FleetOrchestrator
from sentinelswarm.sim.scenario import build_demo_scenario


async def test_comms_loss_marks_offline_and_raises_incident(
    orchestrator: FleetOrchestrator,
) -> None:
    build_demo_scenario(orchestrator, num_drones=3)
    await orchestrator.advance(6.0)  # launch + reach zones

    assert orchestrator.inject_fault("sim-2", COMMS_LOSS)
    await orchestrator.advance(8.0)  # exceed heartbeat_timeout (5s)

    drone = orchestrator.manager.state.get_drone("sim-2")
    assert drone is not None
    assert drone.state is DroneState.OFFLINE
    assert drone.health is HealthStatus.UNHEALTHY

    categories = [i.category for i in orchestrator.manager.state.incidents.list()]
    assert IncidentCategory.COMMS_LOSS in categories


async def test_healthy_drones_stay_online(orchestrator: FleetOrchestrator) -> None:
    build_demo_scenario(orchestrator, num_drones=3)
    await orchestrator.advance(6.0)
    for drone_id in ("sim-1", "sim-3"):
        drone = orchestrator.manager.state.get_drone(drone_id)
        assert drone is not None
        assert drone.state is not DroneState.OFFLINE
        assert drone.health is HealthStatus.HEALTHY
