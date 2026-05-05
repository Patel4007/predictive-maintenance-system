from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .hub import SnapshotHub
from .models import ActionResponse, AssetSnapshot, DashboardSnapshot, MaintenanceRecommendation, ScenarioDefinition, ScenarioTriggerRequest
from .simulation import PredictiveMaintenanceEngine


async def simulation_loop(engine: PredictiveMaintenanceEngine, hub: SnapshotHub, settings: Settings) -> None:
    while True:
        engine.step()
        await hub.publish(engine.dashboard_snapshot().model_dump(mode="json"))
        await asyncio.sleep(settings.update_interval_seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    static_dir = Path(__file__).resolve().parent / "static"
    asset_version = str(
        int(
            max(
                (static_dir / "index.html").stat().st_mtime,
                (static_dir / "styles.css").stat().st_mtime,
                (static_dir / "app.js").stat().st_mtime,
            )
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = PredictiveMaintenanceEngine(app_settings)
        hub = SnapshotHub()
        app.state.settings = app_settings
        app.state.engine = engine
        app.state.hub = hub
        app.state.simulation_task = asyncio.create_task(simulation_loop(engine, hub, app_settings))
        try:
            yield
        finally:
            app.state.simulation_task.cancel()
            with suppress(asyncio.CancelledError):
                await app.state.simulation_task

    app = FastAPI(
        title="Predictive Maintenance System",
        version="0.1.0",
        description="Forecast equipment failures from simulated machine telemetry and optimize maintenance timing.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def disable_browser_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        html = html.replace("/static/styles.css", f"/static/styles.css?v={asset_version}")
        html = html.replace("/static/app.js", f"/static/app.js?v={asset_version}")
        return HTMLResponse(html)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, object]:
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        snapshot = engine.dashboard_snapshot()
        return {
            "status": "ok",
            "generated_at": snapshot.generated_at,
            "asset_count": snapshot.metrics.asset_count,
            "update_interval_seconds": request.app.state.settings.update_interval_seconds,
        }

    @app.get("/api/dashboard", response_model=DashboardSnapshot)
    async def dashboard(request: Request) -> DashboardSnapshot:
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        return engine.dashboard_snapshot()

    @app.get("/api/assets", response_model=list[AssetSnapshot])
    async def assets(request: Request) -> list[AssetSnapshot]:
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        return engine.dashboard_snapshot().assets

    @app.get("/api/maintenance/schedule", response_model=list[MaintenanceRecommendation])
    async def maintenance_schedule(request: Request) -> list[MaintenanceRecommendation]:
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        return engine.dashboard_snapshot().maintenance_schedule

    @app.get("/api/scenarios", response_model=list[ScenarioDefinition])
    async def scenarios(request: Request) -> list[ScenarioDefinition]:
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        return engine.dashboard_snapshot().scenarios

    @app.post("/api/scenarios/{scenario_id}", response_model=ActionResponse)
    async def trigger_scenario(request: Request, scenario_id: str, payload: ScenarioTriggerRequest) -> ActionResponse:
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        hub: SnapshotHub = request.app.state.hub
        try:
            result = engine.inject_scenario(scenario_id, duration_hours=payload.duration_hours)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await hub.publish(engine.dashboard_snapshot().model_dump(mode="json"))
        return ActionResponse(**result)

    @app.post("/api/maintenance/execute/{asset_id}", response_model=ActionResponse)
    async def execute_maintenance(request: Request, asset_id: str) -> ActionResponse:
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        hub: SnapshotHub = request.app.state.hub
        try:
            result = engine.service_asset(asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await hub.publish(engine.dashboard_snapshot().model_dump(mode="json"))
        return ActionResponse(**result)

    @app.get("/api/events")
    async def events(request: Request, once: bool = False) -> StreamingResponse:
        hub: SnapshotHub = request.app.state.hub
        settings_obj: Settings = request.app.state.settings
        engine: PredictiveMaintenanceEngine = request.app.state.engine
        queue = hub.subscribe()

        async def stream() -> AsyncIterator[str]:
            try:
                initial = engine.dashboard_snapshot().model_dump_json()
                yield f"data: {initial}\n\n"
                if once:
                    return
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=settings_obj.update_interval_seconds * 3)
                        yield f"data: {payload}\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
