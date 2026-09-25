# SentinelSwarm handover

This handover is for continuing the project in another agent/tool (for example Antigravity).
It captures the current implementation state, validated commands, important files and the
next engineering steps.

## Current state

SentinelSwarm is a non-weaponized drone fleet-management control plane. The physical idea
behind it is a small ESP32 mini-drone swarm: the vehicles stay light and kinetically focused,
while the intelligence lives in the control plane.

The project currently has:

- A deterministic fleet manager with scheduling, health monitoring, incidents, reassignment
  and return-to-base behaviour.
- A high-rate telemetry split: pose/altitude telemetry at `Settings.telemetry_hz` (default
  30 Hz), heartbeats at the lower liveness cadence.
- Two isolated worlds in the API process:
  - `sim` — seeded with simulated drones for demos and tests.
  - `real` — empty hardware world for future ESP32/PX4/ROS 2 uplinks.
- World-scoped REST APIs under `/api/sim/*` and `/api/real/*`.
- World-scoped 30 Hz WebSockets at `/ws/sim` and `/ws/real`.
- A buildless static command dashboard at `/dashboard`:
  - top-left SIM / REAL world switch;
  - Overview, Tactical Map, 3D Terrain, Units, Missions, Incidents pages;
  - zoomable/pannable 2D canvas map;
  - sparse unbounded map coverage measured as mapped `m²`;
  - interactive Three.js 3D terrain reconstruction with OrbitControls;
  - vendored Three.js, OrbitControls and fonts (no runtime CDN dependency).

## Validated commands

From the repository root:

```powershell
# run live API + dashboard
.\.venv\Scripts\python.exe -m uvicorn sentinelswarm.api.app:app --host 127.0.0.1 --port 8000

# run accelerated CLI demo
.\.venv\Scripts\python.exe -m sentinelswarm.sim.runner --drones 3

# quality gate
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pytest -q
```

Last validated in this session:

- `ruff check .` -> passed
- `mypy` -> passed (`34 source files`)
- `pytest -q` -> exit code `0` (`54 tests`)

PowerShell sometimes suppresses the final pytest summary line in pipelines; trust `$LASTEXITCODE`.

## API quick reference

```bash
GET  /health
GET  /api/worlds
GET  /api/sim/fleet
GET  /api/real/fleet
GET  /api/sim/drones
POST /api/sim/drones
POST /api/sim/drones/{drone_id}/fault
GET  /api/sim/missions
POST /api/sim/missions
GET  /api/sim/incidents
WS   /ws/sim
WS   /ws/real
```

Replace `sim` with `real` for hardware-world operations. The worlds are backed by separate
`FleetOrchestrator` instances, so state and map coverage do not cross between them.

## Files to know

- `src/sentinelswarm/api/app.py`
  - creates SIM and REAL `FleetOrchestrator` instances;
  - registers `/api/{env}` routes;
  - streams snapshots from `/ws/{env}`.
- `src/sentinelswarm/agents/base.py`
  - `AgentConfig.telemetry_hz`;
  - decoupled 30 Hz telemetry and lower-rate heartbeat.
- `src/sentinelswarm/config.py`
  - `telemetry_hz`, `sensor_radius_m` settings.
- `src/sentinelswarm/api/static/index.html`
  - dashboard shell and import map.
- `src/sentinelswarm/api/static/app.css`
  - premium visual system, world accents, native Border Beam / Liquid Metal-inspired effects.
- `src/sentinelswarm/api/static/app.js`
  - SPA router, WebSocket clients, world state, 2D map, sparse coverage grid and Three.js terrain.
- `tests/test_api.py`
  - API validation plus SIM/REAL world-separation tests.

## Dashboard behavior

- SIM world starts with seeded simulated drones when `create_app(seed_demo=True)` is used.
- REAL world starts empty and should stay empty until a real driver/uplink is added.
- 2D map:
  - mouse wheel zooms around cursor;
  - drag pans;
  - coverage is sparse and unbounded;
  - display is mapped area (`m²`), not percentage.
- 3D terrain:
  - uses Three.js and OrbitControls;
  - drag = orbit;
  - wheel = zoom;
  - right-drag = pan;
  - terrain is rebuilt from the active world's coverage grid.

## Important design decisions

- Do not merge SIM and REAL coverage. This is a core concept: simulation data is useful for
  demos/training, but must not appear in the real operational map.
- Keep edge vehicles thin. They report telemetry and follow setpoints; scheduling, health,
  incidents and reassignment live in SentinelSwarm.
- Keep deterministic state-machine logic first. Learned/adaptive policies should later plug
  into scheduler seams instead of replacing the state machine.
- Keep the dashboard buildless for now. If the UI grows much further, the next step is
  likely Vite + React or React + react-three-fiber, but the current version deliberately has
  no Node build pipeline.

## Next recommended work

1. **REAL hardware uplink**
   - Add an ESP32 bridge driver (likely Wi-Fi/MQTT first, serial as fallback).
   - Map incoming ESP32 pose/battery/health into the existing `DroneDriver` or a new adapter.
   - Register it only in the REAL world.

2. **Persisted map + missions**
   - Add a PostgreSQL-backed repository for missions/incidents.
   - Add a coverage-store abstraction so sparse map cells can survive restarts.

3. **World-labeled observability**
   - Add a `world` label to metrics or expose per-world registries.
   - Add Grafana dashboards for SIM and REAL separately.

4. **Computer vision events**
   - Emit `VisionEvent` from simulated/test imagery first.
   - Keep detections environmental/infrastructure-only: gate state, marker changes,
     smoke-like anomaly, vehicle/equipment presence.

5. **Adaptive scheduling**
   - Keep `FleetScheduler.plan()` as the seam.
   - Collect real flight/mission data first, then test learned policy alternatives against
     the deterministic scheduler.

## Git / deployment notes

- Remote: `git@github.com:ArneSet/SentinelSwarm.git`
- Main branch: `main`
- SSH key used successfully in this environment: `%USERPROFILE%\.ssh\id_ed25519_github`
- Docker is not installed on this machine right now; local server was tested with `uvicorn`.

## Current caveats

- The REAL world currently has no real hardware driver registered; the dashboard correctly
  shows it as empty.
- Metrics are still easiest to reason about as SIM/default metrics. Production should label
  or split them by world.
- The 3D terrain is client-side and in-memory. It is correct for operator visualization but
  not yet persisted as a backend map artifact.
