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

class LMStudioSettingsRequest(BaseModel):
    base_url: str
    model: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 50000

class ClineSettingsRequest(BaseModel):
    api_key: str
    model: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 50000

class UpdateSettingsRequest(BaseModel):
    connector_type: str
    lmstudio: Optional[LMStudioSettingsRequest] = None
    cline: Optional[ClineSettingsRequest] = None
