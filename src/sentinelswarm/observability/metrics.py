"""Prometheus metrics for the fleet control plane.

All metrics are created against an injectable :class:`CollectorRegistry` so tests
can spin up isolated instances without the global-registry "duplicated timeseries"
error. The API mounts one shared instance at ``/metrics``.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Buckets tuned for short simulated missions and sub-second message latencies.
_MISSION_BUCKETS = (1, 2, 5, 10, 20, 30, 60, 120, 300)
_MESSAGE_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


class Metrics:
    """Container for every metric the control plane exports."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.fleet_size = Gauge(
            "sentinel_fleet_size", "Total number of registered drones.", registry=self.registry
        )
        self.drones_by_state = Gauge(
            "sentinel_drones_by_state",
            "Number of drones in each state.",
            labelnames=("state",),
            registry=self.registry,
        )
        self.healthy_drones = Gauge(
            "sentinel_healthy_drones", "Number of healthy drones.", registry=self.registry
        )
        self.offline_drones = Gauge(
            "sentinel_offline_drones", "Number of offline drones.", registry=self.registry
        )
        self.active_missions = Gauge(
            "sentinel_active_missions",
            "Missions currently assigned or in progress.",
            registry=self.registry,
        )
        self.battery_pct = Gauge(
            "sentinel_drone_battery_pct",
            "Per-drone battery percentage.",
            labelnames=("drone_id",),
            registry=self.registry,
        )

        self.missions_total = Counter(
            "sentinel_missions_total",
            "Missions by terminal outcome.",
            labelnames=("outcome",),
            registry=self.registry,
        )
        self.reassignments_total = Counter(
            "sentinel_mission_reassignments_total",
            "Total mission reassignments after failures.",
            registry=self.registry,
        )
        self.incidents_total = Counter(
            "sentinel_incidents_total",
            "Incidents raised, by category.",
            labelnames=("category",),
            registry=self.registry,
        )
        self.heartbeats_total = Counter(
            "sentinel_heartbeats_total", "Heartbeats received.", registry=self.registry
        )

        self.mission_latency = Histogram(
            "sentinel_mission_latency_seconds",
            "Time from mission creation to completion.",
            buckets=_MISSION_BUCKETS,
            registry=self.registry,
        )
        self.message_latency = Histogram(
            "sentinel_message_latency_seconds",
            "Time from event emission to handling.",
            buckets=_MESSAGE_BUCKETS,
            registry=self.registry,
        )

    def render(self) -> tuple[bytes, str]:
        """Return (body, content_type) for the ``/metrics`` endpoint."""

        return generate_latest(self.registry), CONTENT_TYPE_LATEST
