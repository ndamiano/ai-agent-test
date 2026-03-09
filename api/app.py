from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Dict, Any
import logging
from database.schema import init_db
from connectors.lmstudio_client import LMStudioConnector
from connectors.embedding_client import EmbeddingClient

# Initialize connectors
lmstudio_client = LMStudioConnector()
embedding_client = EmbeddingClient()

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

# Import routers (they will be mounted in the startup event)
from api.routers import tasks, agents, system

@app.on_event("startup")
async def startup_event():
    """Startup event handler to initialize database and perform health checks."""
    try:
        # Initialize database
        init_db()
        logging.info("Database initialized successfully")
        
        # Perform health checks
        lmstudio_status = lmstudio_client.health_check()
        embedding_status = embedding_client.health_check()
        
        # Log health check results
        logging.info(f"LMStudio connectivity: {'Healthy' if lmstudio_status else 'Unhealthy'}")
        logging.info(f"Embedding client connectivity: {'Healthy' if embedding_status else 'Unhealthy'}")
        
    except Exception as e:
        logging.error(f"Startup error: {str(e)}")
        raise HTTPException(status_code=500, detail="Server startup failed")

@app.get("/", response_model=Dict[str, Any])
async def root():
    """Root endpoint that returns health check information."""
    try:
        # Check database connectivity
        db_status = "Healthy"
        
        # Check LMStudio connectivity
        lmstudio_status = lmstudio_client.health_check()
        
        # Check embedding client connectivity
        embedding_status = embedding_client.health_check()
        
        return {
            "status": "healthy",
            "server": "running",
            "database": db_status,
            "lmstudio": "healthy" if lmstudio_status else "unhealthy",
            "embedding": "healthy" if embedding_status else "unhealthy"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

# Mount routers
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(system.router, prefix="/system", tags=["system"])

# Export the app for use in other modules
__all__ = ["app"]