"""FastAPI control-plane API with two fully separated worlds.

The **simulation** and **real** fleets are backed by independent
:class:`FleetOrchestrator` instances — separate buses, state, incidents and
metrics. Anything a simulated drone discovers therefore lives only in the sim
world and can never leak into the real world (and vice-versa). Each world is
exposed under ``/api/{env}/...`` and streamed over ``/ws/{env}``.

The dashboard (mounted at ``/dashboard``) switches the whole context between the
two worlds; the real world starts empty, awaiting a hardware uplink.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings
from ..domain.clock import ManualClock, RealClock
from ..domain.drone import DroneKind
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
    SystemStatus,
)


def _fleet_snapshot(o: FleetOrchestrator) -> dict[str, object]:
    """Compact per-world snapshot pushed over the live WebSocket at telemetry rate."""

    now = o.clock.now()
    st = o.manager.state
    drones: list[dict[str, object]] = []
    for d in sorted(st.list_drones(), key=lambda d: d.drone_id):
        planar = math.hypot(d.position.x - o.base.x, d.position.y - o.base.y)
        drones.append(
            {
                "id": d.drone_id,
                "kind": d.kind.value,
                "state": d.state.value,
                "health": d.health.value,
                "x": round(d.position.x, 3),
                "y": round(d.position.y, 3),
                "z": round(d.position.z, 3),
                "alt": round(d.position.z, 2),
                "dist": round(planar, 2),
                "battery": round(d.battery_pct, 1),
                "mission": d.current_mission_id,
            }
        )
    missions: list[dict[str, object]] = []
    for m in st.list_missions():
        target = m.location or (m.zone.center if m.zone else None)
        missions.append(
            {
                "id": m.mission_id,
                "type": m.type.value,
                "status": m.status.value,
                "drone": m.assigned_drone,
                "attempts": m.retry.attempts,
                "tx": target.x if target else None,
                "ty": target.y if target else None,
                "radius": m.zone.radius if m.zone else 0.0,
            }
        )
    incidents: list[dict[str, object]] = [
        {
            "id": i.incident_id,
            "category": i.category.value,
            "severity": i.severity.value,
            "drone": i.drone_id,
            "mission": i.mission_id,
            "detail": i.detail,
            "at": i.created_at,
        }
        for i in st.incidents.list()[-40:]
    ]
    return {
        "t": now,
        "environment": o.settings.environment,
        "sensor_radius": o.settings.sensor_radius_m,
        "base": {"x": o.base.x, "y": o.base.y, "z": o.base.z},
        "summary": o.manager.fleet_summary(),
        "drones": drones,
        "missions": missions,
        "incidents": incidents,
    }


def create_app(*, orchestrator: FleetOrchestrator | None = None, seed_demo: bool = True) -> FastAPI:
    # Two independent worlds. What a SIM drone discovers never touches the REAL world.
    sim: FleetOrchestrator = orchestrator or FleetOrchestrator(
        clock=RealClock(), settings=Settings(environment="simulation")
    )
    real_clock = ManualClock() if isinstance(sim.clock, ManualClock) else RealClock()
    real: FleetOrchestrator = FleetOrchestrator(
        clock=real_clock, settings=Settings(environment="real")
    )
    worlds: dict[str, FleetOrchestrator] = {"sim": sim, "real": real}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(sim.settings.log_level, json_output=True)
        await sim.start()
        await real.start()
        if seed_demo and not sim.agent_ids:
            build_demo_scenario(sim, num_drones=3)
        yield
        await sim.stop()
        await real.stop()

    app = FastAPI(
        title="SentinelSwarm Fleet API",
        version="0.2.0",
        description="Non-weaponized autonomous drone fleet-management control plane.",
        lifespan=lifespan,
    )
    app.state.sim = sim
    app.state.real = real

    _register_global(app, worlds)
    for env, orch in worlds.items():
        _register_env(app, env, orch)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/dashboard", StaticFiles(directory=str(static_dir), html=True), name="dashboard")
    return app


def _register_global(app: FastAPI, worlds: dict[str, FleetOrchestrator]) -> None:
    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        body, content_type = worlds["sim"].metrics.render()
        return Response(content=body, media_type=content_type)

    @app.get("/api/worlds", tags=["ops"])
    async def api_worlds() -> JSONResponse:
        out: dict[str, object] = {}
        for env, o in worlds.items():
            drones = o.manager.state.list_drones()
            out[env] = {
                "environment": o.settings.environment,
                "fleet_size": len(drones),
                "telemetry_hz": o.settings.telemetry_hz,
                "active_missions": len(o.manager.state.active_missions()),
                "incident_count": o.manager.state.incidents.count,
            }
        return JSONResponse(out)

    @app.websocket("/ws/{env}")
    async def ws_env(ws: WebSocket, env: str) -> None:
        o = worlds.get(env)
        if o is None:
            await ws.close(code=4004)
            return
        await ws.accept()
        period = 1.0 / max(1.0, o.settings.telemetry_hz)
        try:
            while True:
                await ws.send_json(_fleet_snapshot(o))
                await asyncio.sleep(period)
        except WebSocketDisconnect:
            return
        except Exception:  # client vanished / send failed — end the stream quietly
            return

    @app.get("/", tags=["ops"])
    async def index() -> JSONResponse:
        return JSONResponse(
            {
                "service": "sentinelswarm",
                "dashboard": "/dashboard",
                "docs": "/docs",
                "worlds": "/api/worlds",
                "metrics": "/metrics",
            }
        )


def _register_env(app: FastAPI, env: str, orch: FleetOrchestrator) -> None:
    """Register the full data + action route-set for one world under ``/api/{env}``."""

    p = f"/api/{env}"

    def world() -> FleetOrchestrator:
        return orch

    @app.get(f"{p}/status", response_model=SystemStatus, tags=[env])
    async def status(o: FleetOrchestrator = Depends(world)) -> SystemStatus:
        drones = o.manager.state.list_drones()
        return SystemStatus(
            environment=o.settings.environment,
            fleet_size=len(drones),
            simulated=sum(1 for d in drones if d.kind is DroneKind.SIMULATED),
            real=sum(1 for d in drones if d.kind is DroneKind.REAL),
            telemetry_hz=o.settings.telemetry_hz,
            server_time=o.clock.now(),
        )

    @app.get(f"{p}/fleet", response_model=FleetSummary, tags=[env])
    async def fleet_summary(o: FleetOrchestrator = Depends(world)) -> FleetSummary:
        return FleetSummary.model_validate(o.manager.fleet_summary())

    @app.get(f"{p}/drones", response_model=list[DroneView], tags=[env])
    async def list_drones(o: FleetOrchestrator = Depends(world)) -> list[DroneView]:
        now = o.clock.now()
        return [
            DroneView.from_domain(d, now)
            for d in sorted(o.manager.state.list_drones(), key=lambda d: d.drone_id)
        ]

    @app.get(f"{p}/drones/{{drone_id}}", response_model=DroneView, tags=[env])
    async def get_drone(drone_id: str, o: FleetOrchestrator = Depends(world)) -> DroneView:
        drone = o.manager.state.get_drone(drone_id)
        if drone is None:
            raise HTTPException(status_code=404, detail="drone not found")
        return DroneView.from_domain(drone, o.clock.now())

    @app.post(f"{p}/drones", response_model=ActionResponse, status_code=201, tags=[env])
    async def add_drone(
        req: AddDroneRequest, o: FleetOrchestrator = Depends(world)
    ) -> ActionResponse:
        drone_id = o.add_simulated_drone(
            req.drone_id, start=Position(req.x, req.y, req.z), battery_pct=req.battery_pct
        )
        return ActionResponse(ok=True, detail=drone_id)

    @app.delete(f"{p}/drones/{{drone_id}}", response_model=ActionResponse, tags=[env])
    async def remove_drone(drone_id: str, o: FleetOrchestrator = Depends(world)) -> ActionResponse:
        if not o.remove_drone(drone_id):
            raise HTTPException(status_code=404, detail="drone not found")
        return ActionResponse(ok=True, detail=drone_id)

    @app.post(f"{p}/drones/{{drone_id}}/fault", response_model=ActionResponse, tags=[env])
    async def inject_fault(
        drone_id: str, req: InjectFaultRequest, o: FleetOrchestrator = Depends(world)
    ) -> ActionResponse:
        if not o.inject_fault(drone_id, req.code):
            raise HTTPException(status_code=404, detail="drone not found")
        return ActionResponse(ok=True, detail=f"{drone_id}:{req.code}")

    @app.get(f"{p}/missions", response_model=list[MissionView], tags=[env])
    async def list_missions(o: FleetOrchestrator = Depends(world)) -> list[MissionView]:
        return [MissionView.from_domain(m) for m in o.manager.state.list_missions()]

    @app.get(f"{p}/missions/{{mission_id}}", response_model=MissionView, tags=[env])
    async def get_mission(mission_id: str, o: FleetOrchestrator = Depends(world)) -> MissionView:
        mission = o.manager.state.get_mission(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="mission not found")
        return MissionView.from_domain(mission)

    @app.post(f"{p}/missions", response_model=MissionView, status_code=201, tags=[env])
    async def create_mission(
        req: CreateMissionRequest, o: FleetOrchestrator = Depends(world)
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

    @app.post(f"{p}/missions/{{mission_id}}/cancel", response_model=ActionResponse, tags=[env])
    async def cancel_mission(
        mission_id: str, o: FleetOrchestrator = Depends(world)
    ) -> ActionResponse:
        if not o.manager.cancel_mission(mission_id):
            raise HTTPException(status_code=404, detail="mission not found or already terminal")
        return ActionResponse(ok=True, detail=mission_id)

    @app.get(f"{p}/incidents", response_model=list[IncidentResponse], tags=[env])
    async def list_incidents(o: FleetOrchestrator = Depends(world)) -> list[IncidentResponse]:
        return [IncidentResponse.from_domain(i) for i in o.manager.state.incidents.list()]


# Module-level app for ``uvicorn sentinelswarm.api.app:app``.
app = create_app()
