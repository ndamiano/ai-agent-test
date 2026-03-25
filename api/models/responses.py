from pydantic import BaseModel
from typing import Optional, List, Dict, Any
class HealthResponse(BaseModel):
    status: str
    server: str
    database: str
    lmstudio: str
    embedding: str

class ErrorResponse(BaseModel):
    detail: str
    code: Optional[int] = None

class SubtaskResponse(BaseModel):
    id: str
    agent_id: str
    goal: str
    status: str
    position: int
    output: Optional[str]
    depends_on: List[str]
    name: Optional[str] = None
    description: Optional[str] = None

class TaskResponse(BaseModel):
    id: str
    goal: str
    status: str
    execution_mode: str
    created_at: str
    updated_at: str
    subtasks: List[SubtaskResponse] = []

class EventResponse(BaseModel):
    id: str
    event_type: str
    message: str
    subtask_id: Optional[str]
    created_at: str

class TaskDetailResponse(TaskResponse):
    events: List[EventResponse]
    context_keys: List[str]

class AskResponse(BaseModel):
    question: str
    answer: str
    context_used: List[str]

class SystemStatusResponse(BaseModel):
    status: str
    lmstudio_connected: bool
    embedding_connected: bool
    lmstudio_url: str
    agent_count: int
    task_count: int

class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    tools: List[str]

class SettingsResponse(BaseModel):
    connector_type: str
    lmstudio: Optional[Dict[str, Any]] = None
    cline: Optional[Dict[str, Any]] = None
