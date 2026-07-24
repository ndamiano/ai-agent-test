import logging
import os
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Interactive API docs are a dev convenience; in prod they hand an attacker the full route +
# schema map, so they only exist when MAESTRO_DEV=1 (same switch run.py keys reload on).
_dev = os.getenv("MAESTRO_DEV") == "1"

app = FastAPI(
    title="AI Agent API",
    description="API for AI agent management and task execution",
    version="1.0.0",
    docs_url="/docs" if _dev else None,
    redoc_url="/redoc" if _dev else None,
    openapi_url="/openapi.json" if _dev else None,
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
from api.routers import admin, agents, billing, chat, games, system, websocket, workqueue
from api.websocket.event_bus import event_bus
from auth.deps import install_auth
from auth.router import router as auth_router
from config.settings_manager import settings_manager
from db.reaper import Reaper
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

        # Queue housekeeping. Unconditional, unlike the autoscaler below: a wedged job, a dropped
        # asset finalize, or a stuck build (its driver process died mid-turn) needs reaping on the
        # home box too. Builds have no dedicated worker any more — each build's llm turns ride the
        # shared `llm` queue and the completion handler drives the next.
        app.state.reaper = Reaper()
        app.state.reaper.start()
        logging.info("Queue reaper started")

        # RunPod autoscaler — only when RunPod is configured.
        _settings = settings_manager.get_settings()
        _rp = _settings.get("runpod") or {}
        if _rp.get("enabled") and _rp.get("api_key"):
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
        if getattr(app.state, "reaper", None):
            app.state.reaper.stop()
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
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
# Outside the /api user gate on purpose — workers auth with the shared workqueue token.
app.include_router(workqueue.router, prefix="/worker", tags=["workqueue"])

# Serve the built frontend same-origin (one process, one Funnel port, no CORS). Mounted LAST so
# the API routers above win; skipped when dist/ is absent (dev runs the Vite server instead).
from pathlib import Path

from fastapi.staticfiles import StaticFiles

# Serve the runtime harness so the SPA can open a built game (mounted before the SPA catch-all).
# /play runs MODEL-AUTHORED JS, so every response carries a CSP that pins all loads and network
# to this origin: generated code cannot exfiltrate anywhere or pull external scripts. It shares
# the app origin (ownership-gated — a game only ever runs in its owner's browser), so this is
# containment, not isolation; SHARING games requires true origin isolation first — see
# tasks/production_hardening.md H1. 'unsafe-inline' is for index.html's own bootstrap script;
# eval stays blocked. blob: + data: in img-src (blob: in connect-src too) are for GLTFLoader's
# embedded GLB textures: it decodes them through same-document object URLs (ImageBitmapLoader
# fetches them, so connect-src governs as well), and EXT_texture_webp's support DETECTION loads a
# 1-px data: probe image — blocking either rejects the whole GLB and the game silently plays as
# bare boxes (shipped, twice: first blob:, then the data: probe). Neither grants anything
# cross-origin — the bytes already live in the page.
_PLAY_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
             "connect-src 'self' blob:; img-src 'self' blob: data:; "
             "style-src 'self' 'unsafe-inline'; "
             "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")


@app.middleware("http")
async def _play_csp(request, call_next):
    response = await call_next(request)
    if request.url.path == "/play" or request.url.path.startswith("/play/"):
        response.headers["Content-Security-Policy"] = _PLAY_CSP
    return response


_runtime = Path(__file__).resolve().parents[2] / "runtime"
if _runtime.is_dir():
    app.mount("/play", StaticFiles(directory=_runtime, html=True), name="play")

_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="spa")

__all__ = ["app"]
