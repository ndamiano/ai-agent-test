from pydantic import BaseModel
from typing import Optional, List

class AgentCreateRequest(BaseModel):
    name: str
    type: str
    capabilities: Optional[List[str]] = []

class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    capabilities: Optional[List[str]] = None

class CreateTaskRequest(BaseModel):
    goal: str
    execution_mode: Optional[str] = None

class AskRequest(BaseModel):
    question: str

class CreateAgentRequest(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    tools: List[str] = []
