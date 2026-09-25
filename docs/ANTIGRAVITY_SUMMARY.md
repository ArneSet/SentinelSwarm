# Antigravity summary

Short context for continuing SentinelSwarm in Antigravity.

## What this project is

SentinelSwarm is a non-weaponized drone fleet-management control plane. It grew out of an
ESP32 mini-drone swarm idea: the drones themselves stay minimal and flight-focused; fleet
intelligence lives in SentinelSwarm.

Current implementation:

- deterministic drone state machine and mission scheduler;
- simulated drone agents with battery/kinematic model;
- failure detection, incidents and mission reassignment;
- 30 Hz telemetry stream;
- separate SIM and REAL worlds;
- tactical patrol planning with interactive Zone Loiter (`DRAW_ZONE`) and Multi-Point Waypoints (`WAYPOINT_ROUTE`);
- mathematical Safe-RTB duration budgeting model;
- unit registration and decommissioning;
- coverage/terrain reset capabilities;
- buildless premium dashboard with 2D/3D maps and Tactical Metal theme.

Start with [README.md](../README.md) for the project overview.

## Key docs to read first

1. [WORK_REPORT_PATROL_UPGRADE.md](WORK_REPORT_PATROL_UPGRADE.md)
   Complete technical work report of the advanced patrol planning, multi-point waypoint engine,
   Safe-RTB calculation, terrain reset, and unit decommissioning sprint.

2. [HANDOVER.md](HANDOVER.md)
   Current implementation state, validated commands, file map, API quick reference and next
   recommended work. This is the most important file for continuing in Antigravity.

3. [ARCHITECTURE.md](ARCHITECTURE.md)
   Control-plane architecture, SIM/REAL separation, component responsibilities, event model
   and API model.

4. [FLEET_STATE_MACHINE.md](FLEET_STATE_MACHINE.md)
   Valid drone states and legal transitions. Keep this deterministic; learned policies should
   plug in later around the scheduler, not replace the safety state machine.

5. [FAILURE_HANDLING.md](FAILURE_HANDLING.md)
   How comms loss, faults, low battery, mission timeout and reassignment work.

6. [NETWORKING.md](NETWORKING.md)
   Heartbeats, timeouts, retries, idempotency, duplicate events and message ordering.

7. [OBSERVABILITY.md](OBSERVABILITY.md)
   Metrics, logging, correlation ids and dashboard telemetry model.

8. [DEMO.md](DEMO.md)
   How to run the CLI demo, dashboard, API calls and Docker stack.

## Important files

- `src/sentinelswarm/api/app.py`
  Dual-world FastAPI app. Registers `/api/sim/*`, `/api/real/*`, `/ws/sim`, `/ws/real`.

- `src/sentinelswarm/api/static/app.js`
  Dashboard SPA: routing, SIM/REAL switch, WebSocket clients, 2D map, sparse coverage grid,
  Three.js terrain.

- `src/sentinelswarm/api/static/app.css`
  Dashboard design system, premium theme, world accents and native Border Beam / Liquid
  Metal-inspired effects.

- `src/sentinelswarm/agents/base.py`
  Drone agent loop. Telemetry is high-rate (`telemetry_hz`), heartbeats stay lower-rate.

- `src/sentinelswarm/fleet/manager.py`
  Fleet state updates, health monitoring, scheduling and failure recovery.

- `src/sentinelswarm/fleet/scheduler.py`
  Deterministic scheduler seam. Future learned/adaptive scheduling should plug in here.

- `tests/test_api.py`
  Includes SIM/REAL world-separation tests.

## Commands

```powershell
# local API + dashboard
.\.venv\Scripts\python.exe -m uvicorn sentinelswarm.api.app:app --host 127.0.0.1 --port 8000

# dashboard
# http://127.0.0.1:8000/dashboard

# quality gate
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
```

Last known good before this summary:

- `ruff check .` passed
- `ruff format --check .` passed
- `mypy` passed
- `pytest` passed
- wheel build included dashboard JS/CSS, Three.js, OrbitControls and fonts

## Current dashboard behavior

- Big top-left SIM / REAL switch changes the entire world context.
- SIM starts with simulated drones (supports Scout and Heavy Lifter models).
- REAL starts empty and must remain isolated from SIM data.
- Tactical Map is pan/zoom capable and unbounded.
- Interactive Tactical Patrol Planning:
  - Exclusively Scout-class drones (`sim-scout`, `esp32-s3-mini`, `px4-mini-racer`, etc.) are permitted for patrol missions.
  - **Zone Loiter:** Drag-to-draw radius on tactical map; autonomous active exploration covers the full circular zone with 8-point perimeter survey ($r = 0.72 R$) and 4-point inner cross pattern ($r = 0.35 R$).
  - **Multi-Point Route & Rundflug (Circuit):** Interactive waypoints ($A \to B \to C$) with automatic closed-loop toggle (click near point A or HUD button `[☍ CLOSE LOOP]`). The flight agent loops the closed circuit continuously for the mission duration.
  - **Safe-RTB Duration Dialog:** Real-time calculation of transit drain, safety reserve (15% crit + 10% safety margin), and maximum safe on-station patrol duration.
- Database / Terrain Reset button on Tactical Map and 3D Terrain (`/api/{world}/terrain/reset`).
- Unit Decommissioning (`REMOVE`) with confirmation modal (`DELETE /api/{world}/drones/{id}`).
- Coverage is measured as mapped area in `m²`, not percent.
- 3D Terrain uses Three.js OrbitControls:
  - drag = orbit;
  - wheel = zoom;
  - right-drag = pan.

## Next best tasks

1. Add the real ESP32 hardware uplink for the REAL world.
2. Add a persisted map/coverage store so coverage survives restarts.
3. Add world-labeled metrics or separate registries for SIM and REAL.
4. Add environmental vision events (`VisionEvent`) from simulated/test imagery.
5. Add a smarter scheduler behind `FleetScheduler.plan()` once deterministic behavior remains
   stable and testable.

## Safety / scope reminder

Keep it non-weaponized. No targeting, no autonomous engagement, no person identification or
facial recognition. Computer vision should remain environmental/infrastructure focused.
