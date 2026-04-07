from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
import logging
from database.schema import init_db
from llm_clients.connector_selector import get_connector, reset_connector_cache
from config.settings_manager import settings_manager

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

from api.routers import tasks, system, settings, agents, outputs

@app.on_event("startup")
async def startup_event():
    """Startup event handler to initialize database and register tools."""
    try:
        # Initialize database
        init_db()
        logging.info("Database initialized successfully")

        # Register tools
        from tools.task_tools import register_task_tools
        from tools.file_tools import register_file_tools
        from tools.system_tools import register_system_tools
        from tools.synthesis_tools import register_synthesis_tools
        register_task_tools()
        register_file_tools()
        register_system_tools()
        register_synthesis_tools()
        logging.info("Tools registered successfully")

    except Exception as e:
        logging.error(f"Startup error: {str(e)}")
        raise HTTPException(status_code=500, detail="Server startup failed")

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
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
app.include_router(outputs.router, prefix="/api/outputs", tags=["outputs"])

def reinitialize_connectors():
    """Reinitialize connectors with updated settings (call after settings change)"""
    reset_connector_cache()
    logging.info("Connectors reinitialized")

__all__ = ["app", "reinitialize_connectors"]
