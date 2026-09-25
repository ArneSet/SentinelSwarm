"""FastAPI control-plane API.

Exposes the fleet to operators and the dashboard: fleet summary, drones,
missions, incidents, Prometheus metrics, plus operator actions (create/cancel
mission, add/remove drone, inject a fault for the demo). The UI is deliberately
secondary — every action here maps directly onto the fleet-management core.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..domain.clock import RealClock
from ..domain.geometry import Position, Zone
from ..domain.states import MissionType
from ..observability.logging import configure_logging
from ..sim.orchestrator import FleetOrchestrator
from ..sim.scenario import build_demo_scenario
from .schemas import (
    ActionResponse,
    AddDroneRequest,
    CreateMissionRequest,
    DroneView,
    FleetSummary,
    HealthResponse,
    IncidentResponse,
    InjectFaultRequest,
    MissionView,
)


def create_app(*, orchestrator: FleetOrchestrator | None = None, seed_demo: bool = True) -> FastAPI:
    # Live orchestrator uses a real clock unless one is injected (tests inject a manual one).
    orch_instance: FleetOrchestrator = orchestrator or FleetOrchestrator(clock=RealClock())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(orch_instance.settings.log_level, json_output=True)
        await orch_instance.start()
        if seed_demo and not orch_instance.agent_ids:
            build_demo_scenario(orch_instance, num_drones=3)
        yield
        await orch_instance.stop()

    app = FastAPI(
        title="SentinelSwarm Fleet API",
        version="0.1.0",
        description="Non-weaponized autonomous drone fleet-management control plane.",
        lifespan=lifespan,
    )
    app.state.orchestrator = orch_instance
    _register_routes(app, orch_instance)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/dashboard", StaticFiles(directory=str(static_dir), html=True), name="dashboard")
    return app


def _register_routes(app: FastAPI, orch_instance: FleetOrchestrator) -> None:
    def orch() -> FleetOrchestrator:
        return orch_instance

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/metrics", tags=["ops"])
    async def metrics(o: FleetOrchestrator = Depends(orch)) -> Response:
        body, content_type = o.metrics.render()
        return Response(content=body, media_type=content_type)

    @app.get("/api/fleet", response_model=FleetSummary, tags=["fleet"])
    async def fleet_summary(o: FleetOrchestrator = Depends(orch)) -> FleetSummary:
        return FleetSummary.model_validate(o.manager.fleet_summary())

    @app.get("/api/drones", response_model=list[DroneView], tags=["fleet"])
    async def list_drones(o: FleetOrchestrator = Depends(orch)) -> list[DroneView]:
        now = o.clock.now()
        return [
            DroneView.from_domain(d, now)
            for d in sorted(o.manager.state.list_drones(), key=lambda d: d.drone_id)
        ]

    @app.get("/api/drones/{drone_id}", response_model=DroneView, tags=["fleet"])
    async def get_drone(drone_id: str, o: FleetOrchestrator = Depends(orch)) -> DroneView:
        drone = o.manager.state.get_drone(drone_id)
        if drone is None:
            raise HTTPException(status_code=404, detail="drone not found")
        return DroneView.from_domain(drone, o.clock.now())

    @app.post("/api/drones", response_model=ActionResponse, status_code=201, tags=["fleet"])
    async def add_drone(
        req: AddDroneRequest, o: FleetOrchestrator = Depends(orch)
    ) -> ActionResponse:
        drone_id = o.add_simulated_drone(
            req.drone_id, start=Position(req.x, req.y, req.z), battery_pct=req.battery_pct
        )
        return ActionResponse(ok=True, detail=drone_id)

    @app.delete("/api/drones/{drone_id}", response_model=ActionResponse, tags=["fleet"])
    async def remove_drone(drone_id: str, o: FleetOrchestrator = Depends(orch)) -> ActionResponse:
        if not o.remove_drone(drone_id):
            raise HTTPException(status_code=404, detail="drone not found")
        return ActionResponse(ok=True, detail=drone_id)

    @app.post("/api/drones/{drone_id}/fault", response_model=ActionResponse, tags=["fleet"])
    async def inject_fault(
        drone_id: str, req: InjectFaultRequest, o: FleetOrchestrator = Depends(orch)
    ) -> ActionResponse:
        if not o.inject_fault(drone_id, req.code):
            raise HTTPException(status_code=404, detail="drone not found")
        return ActionResponse(ok=True, detail=f"{drone_id}:{req.code}")

    @app.get("/api/missions", response_model=list[MissionView], tags=["missions"])
    async def list_missions(o: FleetOrchestrator = Depends(orch)) -> list[MissionView]:
        return [MissionView.from_domain(m) for m in o.manager.state.list_missions()]

    @app.get("/api/missions/{mission_id}", response_model=MissionView, tags=["missions"])
    async def get_mission(mission_id: str, o: FleetOrchestrator = Depends(orch)) -> MissionView:
        mission = o.manager.state.get_mission(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="mission not found")
        return MissionView.from_domain(mission)

    @app.post("/api/missions", response_model=MissionView, status_code=201, tags=["missions"])
    async def create_mission(
        req: CreateMissionRequest, o: FleetOrchestrator = Depends(orch)
    ) -> MissionView:
        try:
            mission_type = MissionType(req.type)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid mission type: {req.type}"
            ) from exc

        zone: Zone | None = None
        location: Position | None = None
        if mission_type is MissionType.PATROL_ZONE:
            zone = Zone(
                zone_id=req.zone_id or f"zone-{req.x:.0f}-{req.y:.0f}",
                center=Position(req.x, req.y, 0.0),
                radius=req.radius,
            )
        else:
            location = Position(req.x, req.y, req.z)

        mission = o.manager.create_mission(
            mission_type,
            zone=zone,
            location=location,
            priority=req.priority,
            timeout_s=req.timeout_s,
            max_retries=req.max_retries,
        )
        return MissionView.from_domain(mission)

    @app.post("/api/missions/{mission_id}/cancel", response_model=ActionResponse, tags=["missions"])
    async def cancel_mission(
        mission_id: str, o: FleetOrchestrator = Depends(orch)
    ) -> ActionResponse:
        if not o.manager.cancel_mission(mission_id):
            raise HTTPException(status_code=404, detail="mission not found or already terminal")
        return ActionResponse(ok=True, detail=mission_id)

    @app.get("/api/incidents", response_model=list[IncidentResponse], tags=["incidents"])
    async def list_incidents(o: FleetOrchestrator = Depends(orch)) -> list[IncidentResponse]:
        return [IncidentResponse.from_domain(i) for i in o.manager.state.incidents.list()]

    @app.get("/", tags=["ops"])
    async def index() -> JSONResponse:
        return JSONResponse(
            {
                "service": "sentinelswarm",
                "dashboard": "/dashboard",
                "docs": "/docs",
                "fleet": "/api/fleet",
                "metrics": "/metrics",
            }
        )


# Module-level app for ``uvicorn sentinelswarm.api.app:app``.
app = create_app()
