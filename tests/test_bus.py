"""In-memory message bus: subject routing, delivery, ordering and isolation."""

from __future__ import annotations

from sentinelswarm.domain.states import DroneState
from sentinelswarm.messaging.bus import InMemoryBus, subject_matches
from sentinelswarm.messaging.events import Event, Heartbeat, Telemetry


def test_subject_matches_exact() -> None:
    assert subject_matches("uplink.event.sim-1", "uplink.event.sim-1")


def test_subject_matches_single_token_wildcard() -> None:
    assert subject_matches("command.sim-1", "command.*")
    assert not subject_matches("command.sim-1.extra", "command.*")


def test_subject_matches_tail_wildcard() -> None:
    assert subject_matches("uplink.telemetry.sim-1", "uplink.telemetry.>")
    assert subject_matches("uplink.telemetry.sim-1.hd", "uplink.telemetry.>")
    assert not subject_matches("uplink.telemetry", "uplink.telemetry.>")


async def test_publish_and_deliver() -> None:
    bus = InMemoryBus()
    received: list[Event] = []

    async def handler(subject: str, event: Event) -> None:
        received.append(event)

    bus.subscribe("uplink.telemetry.>", handler)
    await bus.publish(
        "uplink.telemetry.sim-1",
        Telemetry(drone_id="sim-1", x=1, y=2, z=3, battery_pct=90, state=DroneState.IDLE),
    )
    await bus.drain()
    assert len(received) == 1
    assert isinstance(received[0], Telemetry)
    await bus.close()


async def test_wildcard_does_not_cross_prefix() -> None:
    bus = InMemoryBus()
    got: list[Event] = []

    async def handler(subject: str, event: Event) -> None:
        got.append(event)

    bus.subscribe("uplink.telemetry.>", handler)
    await bus.publish(
        "uplink.event.sim-1",
        Heartbeat(drone_id="sim-1", battery_pct=50, state=DroneState.IDLE),
    )
    await bus.drain()
    assert got == []
    await bus.close()


async def test_delivery_is_isolated_deep_copy() -> None:
    bus = InMemoryBus()
    seen: list[Telemetry] = []

    async def handler(subject: str, event: Event) -> None:
        assert isinstance(event, Telemetry)
        seen.append(event)

    bus.subscribe("uplink.telemetry.>", handler)
    original = Telemetry(drone_id="sim-1", x=0, y=0, z=0, battery_pct=100, state=DroneState.IDLE)
    await bus.publish("uplink.telemetry.sim-1", original)
    await bus.drain()
    # Mutating the delivered copy must not affect the caller's object.
    seen[0].x = 999
    assert original.x == 0
    await bus.close()
