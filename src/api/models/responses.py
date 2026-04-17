from pydantic import BaseModel
from typing import Optional, List, Dict, Any

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
    child_task_id: Optional[str] = None

class TaskResponse(BaseModel):
    id: str
    goal: str
    status: str
    execution_mode: str
    working_directory: Optional[str] = None
    parent_task_id: Optional[str] = None
    created_at: str
    created_at_relative: str
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
    child_tasks: List['TaskResponse'] = []

class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    tools: List[str]

class SettingsResponse(BaseModel):
    connector_type: str
    working_directory: Optional[str] = None
    refine_before_execution: bool = False
    renpy_sdk_path: Optional[str] = None
    lmstudio: Optional[Dict[str, Any]] = None
    cline: Optional[Dict[str, Any]] = None
    comfyui: Optional[Dict[str, Any]] = None
