import logging
import os
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI app
app = FastAPI(
    title="AI Agent API",
    description="API for AI agent management and task execution",
    version="1.0.0"
)

# Allowed browser origins come from MAESTRO_CORS_ORIGINS (comma-separated); default to the local
# dev servers. Auth is a Bearer header, not a cookie, so credentials are off — a pinned origin
# list is what keeps other sites from scripting the API on a logged-in user's behalf.
_cors_origins = [
    o.strip() for o in
    os.getenv("MAESTRO_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

import tools.chat_tools  # noqa: F401  — the @tool decorators register on import
import tools.comfyui_tools  # noqa: F401
import tools.system_tools  # noqa: F401
from api.build_queue import build_queue
from api.routers import agents, billing, chat, games, system, websocket, workqueue
from api.websocket.event_bus import event_bus
from auth.deps import install_auth
from auth.router import router as auth_router
from config.settings_manager import settings_manager
from scaler.autoscaler import Autoscaler
from scaler.runpod_client import RunPodClient
from scaler.stats import SqliteStatsSource

# Gate every route behind a valid bearer token (public paths + the WebSocket handle themselves).
install_auth(app)

@app.on_event("startup")
async def startup_event():
    """Startup event handler to register tools and start the event bus."""
    try:
        await event_bus.start()
        logging.info("Event bus started")

        # Start the single-GPU build worker (serializes builds; extras queue with a position).
        build_queue.start()
        logging.info("Build queue started")

        # RunPod autoscaler — only when the worker-pull queue is on and RunPod is configured.
        _settings = settings_manager.get_settings()
        _rp = _settings.get("runpod") or {}
        if _rp.get("enabled") and _rp.get("api_key") \
                and (_settings.get("workqueue") or {}).get("enabled"):
            app.state.autoscaler = Autoscaler(
                SqliteStatsSource(), RunPodClient(_rp["api_key"]),
                settings_manager.get_settings)
            app.state.autoscaler.start()
            logging.info("RunPod autoscaler started")

    except Exception as e:
        logging.error(f"Startup error: {str(e)}")
        raise HTTPException(status_code=500, detail="Server startup failed")

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event handler to clean up resources."""
    try:
        if getattr(app.state, "autoscaler", None):
            app.state.autoscaler.stop()
        build_queue.stop()
        await event_bus.shutdown()
        logging.info("Event bus stopped")
    except Exception as e:
        logging.error(f"Shutdown error: {str(e)}")

@app.get("/healthz", response_model=Dict[str, Any])
async def healthz():
    """Liveness probe. (`/` serves the SPA in a deployed build, so health lives here.)"""
    return {"status": "healthy", "server": "running"}

# Mount routers
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(websocket.router, prefix="/api", tags=["websocket"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(games.router, prefix="/api/games", tags=["games"])
app.include_router(billing.router, prefix="/api/billing", tags=["billing"])
# Outside the /api user gate on purpose — workers auth with the shared workqueue token.
app.include_router(workqueue.router, prefix="/worker", tags=["workqueue"])

# Serve the built frontend same-origin (one process, one Funnel port, no CORS). Mounted LAST so
# the API routers above win; skipped when dist/ is absent (dev runs the Vite server instead).
from pathlib import Path

from fastapi.staticfiles import StaticFiles

# Serve the runtime harness so the SPA can open a built game (mounted before the SPA catch-all).
_runtime = Path(__file__).resolve().parents[2] / "runtime"
if _runtime.is_dir():
    app.mount("/play", StaticFiles(directory=_runtime, html=True), name="play")

_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="spa")

__all__ = ["app"]
