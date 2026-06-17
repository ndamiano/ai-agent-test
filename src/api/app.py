from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import logging
from llm_clients.connector_selector import reset_connector_cache

# Create FastAPI app
app = FastAPI(
    title="AI Agent API",
    description="API for AI agent management and task execution",
    version="1.0.0"
)

# Configure CORS to allow all origins, methods, and headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routers import system, settings, agents, outputs, websocket, chat, games

@app.on_event("startup")
async def startup_event():
    """Startup event handler to register tools and start the event bus."""
    try:
        # Start event bus
        from api.websocket.event_bus import event_bus
        await event_bus.start()
        logging.info("Event bus started")

        # Import tool modules — decorators register tools at import time
        import tools.file_tools  # noqa: F401
        import tools.system_tools  # noqa: F401
        import tools.comfyui_tools  # noqa: F401
        import maestro.chat_tools  # noqa: F401
        from renpy.checks import register_all as register_renpy_checks
        register_renpy_checks()  # story-structure check types for specs
        logging.info("Tools registered successfully")

    except Exception as e:
        logging.error(f"Startup error: {str(e)}")
        raise HTTPException(status_code=500, detail="Server startup failed")

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event handler to clean up resources."""
    try:
        from api.websocket.event_bus import event_bus
        await event_bus.shutdown()
        logging.info("Event bus stopped")
    except Exception as e:
        logging.error(f"Shutdown error: {str(e)}")

@app.get("/", response_model=Dict[str, Any])
async def root(request: Request):
    """Root endpoint that returns health check information."""
    try:
        return {
            "status": "healthy",
            "server": "running",
            "database": "healthy",
            "lmstudio": "healthy",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

# Mount routers
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(outputs.router, prefix="/api/outputs", tags=["outputs"])
app.include_router(websocket.router, prefix="/api", tags=["websocket"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(games.router, prefix="/api/games", tags=["games"])

def reinitialize_connectors():
    """Reinitialize connectors with updated settings (call after settings change)"""
    reset_connector_cache()
    logging.info("Connectors reinitialized")

__all__ = ["app", "reinitialize_connectors"]
