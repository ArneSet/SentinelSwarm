# Failure handling

Real fleet management is mostly about what happens when things go wrong. SentinelSwarm
detects failures, updates fleet state, reassigns unfinished work when safe, records an
incident, and notifies operators.

## Failure modes & responses

| Failure | Detection | Response |
| --- | --- | --- |
| **Drone loses communication** | `FleetManager._check_health()` — no heartbeat for `health.heartbeat_timeout_s` | Twin → `OFFLINE`, health `UNHEALTHY`, emit `DroneOffline`, raise `COMMS_LOSS` incident, reassign its active mission |
| **Battery critical** | `DroneAgent._maybe_emergency_return()` while flying | Emit `MissionFailed(battery_critical)`, return-to-base; manager raises `BATTERY_CRITICAL` incident + reassigns |
| **Mission timeout** | `FleetManager._check_timeouts()` — `now > mission.deadline_at` | Send `AbortMission`, raise `MISSION_TIMEOUT` incident, reassign |
| **Camera / navigation / sensor fault** | Vehicle reports `FaultDetected` | Twin health `UNHEALTHY`, raise `FAULT` incident; `MissionFailed(fault:*)` triggers reassignment |
| **Drone becomes unhealthy** | Health derived from telemetry/faults | Excluded from scheduling until healthy again |
| **Duplicate / reordered / stale events** | `event_id` dedup + `ts`/ownership guards | Ignored idempotently (see [NETWORKING.md](NETWORKING.md)) |

## Reassignment logic

`FleetManager._fail_or_reassign(mission, reason, now, failed_drone=...)`:

1. Detach the mission from the failed drone (`current_mission_id = None`).
2. If `mission.can_reassign()` (not terminal and retries not exhausted):
   - `mission.release_for_reassignment(reason)` → status back to `PENDING`
   - increment `sentinel_mission_reassignments_total`
   - emit `MissionReassigned`
   - run one immediate scheduler pass (the periodic loop is the backstop)
3. Otherwise mark the mission `FAILED` and count it in `sentinel_missions_total{outcome="failed"}`.

Because the failed drone is no longer `IDLE`/healthy, the deterministic scheduler naturally
routes the requeued mission to a **different** available drone. If none is free yet, the
mission waits `PENDING` until one returns to base and goes `IDLE` — exactly the behaviour
you want in a real fleet.

## Retry policy

`RetryPolicy(max_retries=N)` counts **reassignments** (not the initial assignment):

- initial dispatch → `attempts = 1`, `reassignments = 0`
- each reassignment increments `attempts`
- `exhausted` when `reassignments >= max_retries`

Default `max_retries = 2` (configurable via `SENTINEL_MISSION_DEFAULT_MAX_RETRIES`).

## Incidents

Every failure produces exactly one incident (`fleet/incidents.py`) with a category,
severity, the drone/mission involved, a human-readable detail, the mission `correlation_id`,
and a timestamp. Incidents are exposed at `GET /api/incidents`, emitted as `IncidentCreated`
on `fleet.event`, and counted in `sentinel_incidents_total{category=...}`.

## Safety properties

- Autonomy is **bounded**: missions have targets, timeouts and retry limits; a drone always
  has a safe fallback (return-to-base / hold).
- Failures **degrade gracefully**: one drone dying never fails the fleet; work is recovered.
- State is **validated**: illegal transitions cannot corrupt fleet state.

Tested in [test_heartbeat.py](../tests/test_heartbeat.py),
[test_reassignment.py](../tests/test_reassignment.py),
[test_battery.py](../tests/test_battery.py) and
[test_e2e_simulation.py](../tests/test_e2e_simulation.py).
