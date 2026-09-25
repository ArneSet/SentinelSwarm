# Networking & distributed systems

A drone fleet is a distributed system: unreliable links, partial failures, duplicate and
out-of-order messages. SentinelSwarm models these explicitly so the behaviour is visible and
testable, even though the demo runs in-process.

## Where each concern lives

| Concern | Real-fleet symptom | Handling in SentinelSwarm |
| --- | --- | --- |
| **Heartbeats** | Is the drone still there? | Agents emit `Heartbeat` + `Telemetry` every `heartbeat_interval_s`; the manager tracks `last_heartbeat_at`. |
| **Timeouts** | Link dropped, no reply | `heartbeat_timeout_s` → drone marked `OFFLINE`; missions have `deadline_at`. |
| **Retries** | Transient failure | Missions are retried by **reassignment** with a bounded `RetryPolicy`. |
| **Idempotency** | The same message arrives twice | Dedup by `event_id` (`FleetManager._is_duplicate`, bounded LRU set). |
| **Message acknowledgement** | Did the drone accept the task? | `AssignMission` → `MissionAccepted` ack; unacked missions still time out. |
| **Connection loss** | Drone goes silent | Detected via heartbeat timeout; incident + reassignment. |
| **Event ordering** | Messages arrive out of order | Per-source `seq` for gap detection; stale telemetry dropped by `ts`. |
| **Duplicate messages** | At-least-once delivery | `event_id` dedup + **ownership guards** (ignore mission events from a drone that no longer owns the mission). |
| **Partial failure** | One node down, rest fine | One drone offline never blocks the fleet; its work is recovered. |

## The bus

`InMemoryBus` ([messaging/bus.py](../src/sentinelswarm/messaging/bus.py)) intentionally
mirrors a subject-based broker (NATS/MQTT):

- **Subject routing** with NATS-style wildcards: `*` (one token), `>` (tail).
- **Fan-out** with **per-subscription FIFO** ordering.
- **Isolation**: each subscriber receives a deep copy of the event, mimicking serialization
  across a network boundary — a buggy handler cannot corrupt another subscriber's view.
- **At-least-once handler execution** with per-message error isolation (one bad handler call
  never kills the subscriber).
- **`drain()`**: a deterministic "process everything pending" primitive used by tests and the
  accelerated demo.

Swapping to a real broker means implementing the `MessageBus` protocol over NATS/MQTT and
injecting it into `FleetOrchestrator`. Nothing else changes.

## Idempotency & ordering in practice

```python
# FleetManager._handle(...)
if self._is_duplicate(event.event_id):   # exact-duplicate delivery -> ignore
    return
...
if event.ts < last_telemetry_ts[id]:     # reordered/stale telemetry -> ignore pose
    return
...
if mission.assigned_drone != event.drone_id:  # stale event from previous owner -> ignore
    return
```

These three guards make the manager tolerant of at-least-once delivery, reordering, and
late events from a drone whose mission was already reassigned.

## Correlation ids

Each mission carries a `correlation_id` that is stamped onto every related event
(assignment, telemetry, progress, failure, reassignment, incident) and bound into the
logging context, so a single mission can be traced end-to-end across the "network".

Tested in [test_bus.py](../tests/test_bus.py) and
[test_idempotency.py](../tests/test_idempotency.py).
