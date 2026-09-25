"""FastAPI control-plane API tests (validation, CRUD, metrics, world separation)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sentinelswarm.api import create_app
from sentinelswarm.config import Settings
from sentinelswarm.domain.clock import ManualClock
from sentinelswarm.messaging.bus import InMemoryBus
from sentinelswarm.observability.metrics import Metrics
from sentinelswarm.sim.orchestrator import FleetOrchestrator


@pytest.fixture
def api_client() -> Iterator[TestClient]:
    orch = FleetOrchestrator(
        settings=Settings(), clock=ManualClock(), bus=InMemoryBus(), metrics=Metrics()
    )
    app = create_app(orchestrator=orch, seed_demo=False)
    with TestClient(app) as client:
        yield client


def test_health(api_client: TestClient) -> None:
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_fleet_summary_starts_empty(api_client: TestClient) -> None:
    resp = api_client.get("/api/sim/fleet")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fleet_size"] == 0
    assert body["incident_count"] == 0


def test_worlds_endpoint_lists_both(api_client: TestClient) -> None:
    resp = api_client.get("/api/worlds")
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"sim", "real"}


def test_sim_and_real_are_distinct_worlds(api_client: TestClient) -> None:
    sim = api_client.get("/api/sim/fleet").json()
    real = api_client.get("/api/real/fleet").json()
    assert sim["environment"] == "simulation"
    assert real["environment"] == "real"
    assert real["fleet_size"] == 0


def test_create_mission_valid(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/sim/missions", json={"type": "PATROL_ZONE", "x": 100, "y": 0, "radius": 20}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "PATROL_ZONE"
    assert body["status"] == "PENDING"
    assert body["target_x"] == 100


def test_create_mission_invalid_type_is_422(api_client: TestClient) -> None:
    resp = api_client.post("/api/sim/missions", json={"type": "LASER_STRIKE", "x": 0, "y": 0})
    assert resp.status_code == 422


def test_create_mission_missing_fields_is_422(api_client: TestClient) -> None:
    resp = api_client.post("/api/sim/missions", json={"type": "PATROL_ZONE"})
    assert resp.status_code == 422


def test_list_and_get_mission(api_client: TestClient) -> None:
    created = api_client.post(
        "/api/sim/missions", json={"type": "INSPECT_LOCATION", "x": 10, "y": 20, "z": 15}
    ).json()
    listing = api_client.get("/api/sim/missions")
    assert listing.status_code == 200
    assert any(m["mission_id"] == created["mission_id"] for m in listing.json())
    single = api_client.get(f"/api/sim/missions/{created['mission_id']}")
    assert single.status_code == 200


def test_mission_created_in_sim_not_visible_in_real(api_client: TestClient) -> None:
    api_client.post("/api/sim/missions", json={"type": "PATROL_ZONE", "x": 50, "y": 50})
    assert len(api_client.get("/api/sim/missions").json()) == 1
    assert api_client.get("/api/real/missions").json() == []


def test_get_unknown_mission_is_404(api_client: TestClient) -> None:
    assert api_client.get("/api/sim/missions/does-not-exist").status_code == 404


def test_cancel_unknown_mission_is_404(api_client: TestClient) -> None:
    assert api_client.post("/api/sim/missions/none/cancel").status_code == 404


def test_add_drone_returns_id(api_client: TestClient) -> None:
    resp = api_client.post("/api/sim/drones", json={"drone_id": "api-1"})
    assert resp.status_code == 201
    assert resp.json()["ok"] is True
    assert resp.json()["detail"] == "api-1"


def test_inject_fault_unknown_drone_is_404(api_client: TestClient) -> None:
    resp = api_client.post("/api/sim/drones/ghost/fault", json={"code": "comms_loss"})
    assert resp.status_code == 404


def test_metrics_endpoint_exposes_prometheus(api_client: TestClient) -> None:
    resp = api_client.get("/metrics")
    assert resp.status_code == 200
    assert "sentinel_fleet_size" in resp.text


def test_create_waypoint_route_mission(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/sim/missions",
        json={
            "type": "WAYPOINT_ROUTE",
            "waypoints": [[10.0, 20.0, 15.0], [30.0, 40.0, 15.0], [50.0, 20.0, 15.0]],
            "patrol_duration_s": 25.0,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "WAYPOINT_ROUTE"
    assert len(data["waypoints"]) == 3
    assert data["patrol_duration_s"] == 25.0


def test_coverage_reset_endpoint(api_client: TestClient) -> None:
    resp = api_client.post("/api/sim/coverage/reset")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_delete_drone_endpoint(api_client: TestClient) -> None:
    api_client.post("/api/sim/drones", json={"drone_id": "to-delete"})
    del_resp = api_client.delete("/api/sim/drones/to-delete")
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True
    assert api_client.get("/api/sim/drones/to-delete").status_code == 404


def test_patrol_mission_rejects_non_scout_target(api_client: TestClient) -> None:
    # Register a heavy drone
    api_client.post("/api/sim/drones", json={"drone_id": "heavy-lifter", "model": "sim-heavy"})
    resp = api_client.post(
        "/api/sim/missions",
        json={
            "type": "PATROL_ZONE",
            "x": 30.0,
            "y": 30.0,
            "target_drone_id": "heavy-lifter",
        },
    )
    assert resp.status_code == 422
    assert "not a Scout drone" in resp.json()["detail"]


def test_patrol_mission_accepts_scout_target(api_client: TestClient) -> None:
    # Register a scout drone
    api_client.post("/api/sim/drones", json={"drone_id": "scout-unit", "model": "sim-scout"})
    resp = api_client.post(
        "/api/sim/missions",
        json={
            "type": "PATROL_ZONE",
            "x": 40.0,
            "y": 40.0,
            "target_drone_id": "scout-unit",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["type"] == "PATROL_ZONE"


def test_add_named_scout_drone_and_snapshot_serialization(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/sim/drones",
        json={
            "drone_id": "Nadia",
            "name": "Nadia Scout",
            "model": "sim-scout",
            "unit_type": "simulated",
            "battery_pct": 99.8,
        },
    )
    assert resp.status_code == 201

    get_resp = api_client.get("/api/sim/drones/Nadia")
    assert get_resp.status_code == 200
    d_data = get_resp.json()
    assert d_data["drone_id"] == "Nadia"
    assert d_data["name"] == "Nadia Scout"
    assert d_data["model"] == "sim-scout"

    pz_resp = api_client.post(
        "/api/sim/missions",
        json={
            "type": "PATROL_ZONE",
            "x": 20.0,
            "y": 20.0,
            "target_drone_id": "Nadia",
        },
    )
    assert pz_resp.status_code == 201
    assert pz_resp.json()["type"] == "PATROL_ZONE"
