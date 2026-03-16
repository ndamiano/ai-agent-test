from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Dict, Any
import logging
from database.schema import init_db
from llm_clients.lmstudio_client import LMStudioConnector
from config.settings_manager import settings_manager

# Initialize connectors with settings
settings = settings_manager.get_settings()
lmstudio_client = LMStudioConnector(settings=settings.get("lmstudio"))
embedding_client = None  # Removed: embeddings not currently used

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

from api.routers import tasks, system, settings as settings_router

@app.on_event("startup")
async def startup_event():
    """Startup event handler to initialize database and register tools."""
    try:
        # Initialize database
        init_db()
        logging.info("Database initialized successfully")

        # Register tools
        from tools.task_tools import register_task_tools
        from tools.agent_tools import register_agent_tools
        from tools.file_tools import register_file_tools
        register_task_tools()
        register_agent_tools()
        register_file_tools()
        logging.info("Tools registered successfully")

        # Health checks
        lmstudio_status = lmstudio_client.health_check()
        logging.info(f"LMStudio connectivity: {'Healthy' if lmstudio_status else 'Unhealthy'}")

    except Exception as e:
        logging.error(f"Startup error: {str(e)}")
        raise HTTPException(status_code=500, detail="Server startup failed")

@app.get("/", response_model=Dict[str, Any])
async def root():
    """Root endpoint that returns health check information."""
    try:
        lmstudio_status = lmstudio_client.health_check()
        return {
            "status": "healthy",
            "server": "running",
            "database": "healthy",
            "lmstudio": "healthy" if lmstudio_status else "unhealthy",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

# Mount routers
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])

def reinitialize_connectors():
    """Reinitialize connectors with updated settings (call after settings change)"""
    global lmstudio_client
    settings = settings_manager.get_settings()
    lmstudio_client = LMStudioConnector(settings=settings.get("lmstudio"))
    logging.info("Connectors reinitialized with updated settings")

__all__ = ["app", "lmstudio_client", "reinitialize_connectors"]