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

## Option B — live dashboard

```bash
uvicorn sentinelswarm.api.app:app --reload
# open http://localhost:8000/dashboard
```

Then, interactively:

1. Watch the three seeded drones launch and spread to their zones on the map.
2. Click **fault** next to `sim-2` (or any drone) to inject a comms loss.
3. Within a few seconds the drone turns red (`OFFLINE`), a `COMMS_LOSS` incident appears,
   and its mission's `attempts` increments as another drone picks it up.
4. Use **+ Add sim drone** and **+ Patrol mission** to grow the fleet and workload live.

Equivalent API calls:

```bash
curl -X POST localhost:8000/api/missions \
  -H 'content-type: application/json' \
  -d '{"type":"PATROL_ZONE","x":120,"y":0,"radius":20}'

curl -X POST localhost:8000/api/drones -H 'content-type: application/json' -d '{}'

curl -X POST localhost:8000/api/drones/sim-2/fault \
  -H 'content-type: application/json' -d '{"code":"comms_loss"}'

curl localhost:8000/api/fleet
curl localhost:8000/api/incidents
```

## Option C — full stack (metrics + Grafana)

```bash
docker compose up --build
# dashboard  http://localhost:8000/dashboard
# prometheus http://localhost:9090   (try: sentinel_drones_by_state)
# grafana    http://localhost:3000   (Prometheus datasource pre-provisioned)
```
