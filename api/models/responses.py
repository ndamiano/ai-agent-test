from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class TaskResponse(BaseModel):
    id: str
    goal: str
    status: str
    execution_mode: str
    created_at: str
    updated_at: str
    subtasks: Optional[List[Dict[str, Any]]] = []

class AgentResponse(BaseModel):
    id: str
    name: str
    type: str
    capabilities: List[str]
    created_at: str
    updated_at: str

class HealthResponse(BaseModel):
    status: str
    server: str
    database: str
    lmstudio: str
    embedding: str

class ErrorResponse(BaseModel):
    detail: str
    code: Optional[int] = None