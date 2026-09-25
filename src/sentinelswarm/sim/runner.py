"""Accelerated CLI demo runner for the 3-drone patrol scenario.

Runs the full demo in deterministic virtual time (so it finishes in well under a
second of wall-clock) and prints a readable timeline:

    launch -> patrol -> inject comms loss -> detect + reassign -> complete + RTB

Entry point: ``sentinelswarm-sim`` (see pyproject ``[project.scripts]``).
"""

from __future__ import annotations

import argparse
import asyncio

from ..domain.clock import ManualClock
from ..observability.logging import configure_logging, get_logger
from .orchestrator import FleetOrchestrator
from .scenario import build_demo_scenario

logger = get_logger("sentinelswarm.demo")


def _print_phase(orch: FleetOrchestrator, label: str) -> None:
    summary = orch.manager.fleet_summary()
    t = orch.clock.now()
    print(f"\n=== t={t:6.1f}s  [{label}] ===")
    print(
        "  fleet: size={fleet_size} healthy={healthy} offline={offline} "
        "active_missions={active_missions} pending={pending_missions} "
        "incidents={incident_count}".format(**summary)
    )
    rate = summary["mission_success_rate"]
    rate_display = round(rate, 2) if isinstance(rate, (int, float)) else rate
    print(f"  mission_success_rate={rate_display}")
    for drone in sorted(orch.manager.state.list_drones(), key=lambda d: d.drone_id):
        print(
            f"    {drone.drone_id:<7} state={drone.state.value:<11} "
            f"battery={drone.battery_pct:5.1f}%  health={drone.health.value} "
            f"mission={drone.current_mission_id}"
        )


def _print_missions(orch: FleetOrchestrator) -> None:
    print("\n--- missions ---")
    for mission in orch.manager.state.list_missions():
        print(
            f"  {mission.mission_id}  type={mission.type.value:<14} "
            f"status={mission.status.value:<11} drone={mission.assigned_drone} "
            f"attempts={mission.retry.attempts}"
        )
    print("\n--- incidents ---")
    for incident in orch.manager.state.incidents.list():
        print(
            f"  {incident.incident_id}  {incident.category.value:<15} "
            f"drone={incident.drone_id} mission={incident.mission_id} :: {incident.detail}"
        )


async def run_demo(
    *,
    num_drones: int = 3,
    fail_drone: str = "sim-2",
    json_logs: bool = False,
) -> FleetOrchestrator:
    configure_logging("INFO", json_output=json_logs)
    orch = FleetOrchestrator(clock=ManualClock())
    await orch.start()
    scenario = build_demo_scenario(orch, num_drones=num_drones)

    print("SentinelSwarm demo - non-weaponized autonomous fleet monitoring")
    print(f"drones={scenario.drone_ids} zones={[z.zone_id for z in scenario.zones]}")

    await orch.advance(3.0)
    _print_phase(orch, "launch + transit")

    await orch.advance(4.0)
    _print_phase(orch, "patrolling")

    print(f"\n>>> injecting COMMS LOSS on {fail_drone} <<<")
    scenario.inject_comms_loss(fail_drone)

    await orch.advance(8.0)
    _print_phase(orch, "failure detected + reassignment")

    await orch.advance(60.0)
    _print_phase(orch, "final (missions complete, drones returned)")

    _print_missions(orch)
    await orch.stop()
    return orch


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SentinelSwarm simulation demo.")
    parser.add_argument("--drones", type=int, default=3, help="number of simulated drones")
    parser.add_argument("--fail-drone", default="sim-2", help="drone id to fail via comms loss")
    parser.add_argument("--json-logs", action="store_true", help="emit JSON logs")
    args = parser.parse_args()
    asyncio.run(
        run_demo(num_drones=args.drones, fail_drone=args.fail_drone, json_logs=args.json_logs)
    )


if __name__ == "__main__":
    main()
