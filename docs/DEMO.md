# Demo

Two ways to experience the same scenario: the **accelerated CLI** (deterministic, finishes
instantly) and the **live dashboard** (real-time).

## Scenario

3 simulated drones patrol 3 zones. Mid-patrol, `sim-2` suffers a silent comms loss. The
fleet detects the missing heartbeats, marks it offline, raises an incident, and reassigns
its mission to a healthy drone. All missions complete; survivors return to base.

## Option A — accelerated CLI

```bash
sentinelswarm-sim
# variations
sentinelswarm-sim --drones 5 --fail-drone sim-3 --json-logs
```

What to look for in the timeline:

1. **launch + transit** — all drones dispatched, `TRANSIT`, missions `active`.
2. **inject comms loss** — `>>> injecting COMMS LOSS on sim-2 <<<`.
3. **failure detected + reassignment** — `sim-2` → `OFFLINE`/`UNHEALTHY`, one incident,
   one mission back to `pending`.
4. **final** — `mission_success_rate=1.0`, all missions `COMPLETED`, one shown with
   `attempts=2` (it was reassigned), survivors `IDLE`.

This is asserted automatically in
[tests/test_e2e_simulation.py](../tests/test_e2e_simulation.py).

## Option B — live dashboard / command console

```bash
uvicorn sentinelswarm.api.app:app --reload
# open http://localhost:8000/dashboard
```

Then, interactively:

1. Use the large **SIM / REAL** switch in the top-left. SIM starts with the demo fleet;
  REAL starts empty and is reserved for future hardware uplinks.
2. Open **Tactical Map**. Mouse wheel zooms, drag pans. The map is unbounded, so coverage is
  shown as mapped area in `m²`, not as a percentage.
3. Open **3D Terrain**. Drag to orbit, mouse wheel to zoom, right-drag to pan. The terrain
  is reconstructed from the active world's sensor coverage only.
4. Use **+ Patrol** / **Scatter x5** to create more work, or **FAULT** on a unit to inject a
  comms loss and watch reassignment.
5. Switch to **REAL** and confirm the map is empty: SIM coverage, missions and incidents are
  intentionally isolated from the real-hardware world.

Equivalent API calls:

```bash
curl -X POST localhost:8000/api/sim/missions \
  -H 'content-type: application/json' \
  -d '{"type":"PATROL_ZONE","x":120,"y":0,"radius":20}'

curl -X POST localhost:8000/api/sim/drones -H 'content-type: application/json' -d '{}'

curl -X POST localhost:8000/api/sim/drones/sim-2/fault \
  -H 'content-type: application/json' -d '{"code":"comms_loss"}'

curl localhost:8000/api/worlds
curl localhost:8000/api/sim/fleet
curl localhost:8000/api/real/fleet
curl localhost:8000/api/sim/incidents
```

## Option C — full stack (metrics + Grafana)

```bash
docker compose up --build
# dashboard  http://localhost:8000/dashboard
# prometheus http://localhost:9090   (try: sentinel_drones_by_state)
# grafana    http://localhost:3000   (Prometheus datasource pre-provisioned)
```
