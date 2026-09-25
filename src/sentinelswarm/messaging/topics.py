"""Canonical bus subjects (NATS-style dotted tokens).

Using structured subjects lets the control plane subscribe with wildcards while
each drone publishes to its own subject:

* ``uplink.telemetry.<drone_id>`` — high-rate pose/battery telemetry.
* ``uplink.event.<drone_id>``     — registration, heartbeat, mission lifecycle,
  faults and vision events.
* ``command.<drone_id>``          — downlink commands to a single drone.
* ``fleet.event``                 — control-plane / operator-facing broadcasts.
"""

from __future__ import annotations

UPLINK_TELEMETRY = "uplink.telemetry"
UPLINK_EVENT = "uplink.event"
COMMAND = "command"
FLEET_EVENT = "fleet.event"

# Wildcard subscriptions for the control plane.
ALL_TELEMETRY = "uplink.telemetry.>"
ALL_UPLINK_EVENTS = "uplink.event.>"


def telemetry_subject(drone_id: str) -> str:
    return f"{UPLINK_TELEMETRY}.{drone_id}"


def uplink_event_subject(drone_id: str) -> str:
    return f"{UPLINK_EVENT}.{drone_id}"


def command_subject(drone_id: str) -> str:
    return f"{COMMAND}.{drone_id}"
