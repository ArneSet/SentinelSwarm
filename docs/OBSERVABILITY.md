# Observability

SentinelSwarm exposes the three pillars — metrics, logs and traces — so the fleet is
operable, not just runnable.

## Metrics (Prometheus)

Served at `GET /metrics` from an injectable `CollectorRegistry`
([observability/metrics.py](../src/sentinelswarm/observability/metrics.py)).

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `sentinel_fleet_size` | gauge | – | Registered drones |
| `sentinel_drones_by_state` | gauge | `state` | Drones per state |
| `sentinel_healthy_drones` | gauge | – | Healthy drones |
| `sentinel_offline_drones` | gauge | – | Offline drones |
| `sentinel_active_missions` | gauge | – | Assigned or in-progress missions |
| `sentinel_drone_battery_pct` | gauge | `drone_id` | Battery per drone |
| `sentinel_missions_total` | counter | `outcome` | Missions by terminal outcome |
| `sentinel_mission_reassignments_total` | counter | – | Reassignments after failures |
| `sentinel_incidents_total` | counter | `category` | Incidents by category |
| `sentinel_heartbeats_total` | counter | – | Heartbeats received |
| `sentinel_mission_latency_seconds` | histogram | – | Creation → completion |
| `sentinel_message_latency_seconds` | histogram | – | Emission → handling |

These cover the required signals: fleet size, healthy/offline drones, active missions,
mission success rate (`missions_total`), mission latency, message latency, battery
distribution and incident count.

### Example queries

```promql
# success rate
sum(sentinel_missions_total{outcome="completed"})
  / clamp_min(sum(sentinel_missions_total), 1)

# fleet availability
sentinel_healthy_drones / sentinel_fleet_size

# p95 mission latency
histogram_quantile(0.95, sum(rate(sentinel_mission_latency_seconds_bucket[5m])) by (le))
```

## Logs (structured)

`observability/logging.py` emits one JSON object per line in production
(`{"ts","level","logger","msg","correlation_id", ...extra}`) or a readable console format
locally. A `contextvars`-based `correlation_id` is attached automatically to every log line
emitted while handling an event, so logs for one mission share an id without threading it
through call signatures.

```json
{"ts": 1723200000.12, "level": "WARNING", "logger": "sentinelswarm.fleet",
 "msg": "mission requeued for reassignment", "correlation_id": "corr-3bfc7fd3",
 "mission_id": "mission-57945290", "reason": "comms_loss"}
```

## Traces / correlation

Each mission's `correlation_id` threads across create → assign → telemetry → fault →
reassign → incident. This is a lightweight, dependency-free trace today; **OpenTelemetry**
spans are a documented extension point (wrap `FleetManager._handle` and command dispatch in
spans keyed by `correlation_id`).

## Dashboard & stack

- **Dashboard**: `GET /dashboard` renders a buildless multi-page command console. It uses
  30 Hz WebSocket snapshots from `WS /ws/sim` or `WS /ws/real`, interpolates motion in the
  browser, and keeps SIM/REAL coverage maps separate.
- **2D map telemetry**: the tactical map renders an unbounded sparse coverage grid, mapped
  area in `m²`, range rings, patrol zones, trails and altitude-aware drone markers.
- **3D telemetry**: the terrain page uses vendored Three.js + OrbitControls to reconstruct
  the active world's coverage as interactive extruded terrain.
- **Local stack**: `docker compose up` starts the API, Prometheus (scraping `/metrics`) and
  Grafana (Prometheus datasource pre-provisioned in `deploy/`).

The Prometheus registry currently represents the default SIM stack. A production deployment
should either label metrics by world (`sim` / `real`) or expose separate registries per world.
