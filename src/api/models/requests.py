from pydantic import BaseModel
from typing import Optional, List

class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    capabilities: Optional[List[str]] = None

class CreateTaskRequest(BaseModel):
    goal: str
    execution_mode: Optional[str] = None
    working_directory: Optional[str] = None

class LMStudioSettingsRequest(BaseModel):
    base_url: str
    model: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 50000

class ClineSettingsRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.cline.bot/api"
    model: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 50000

class ComfyUISettingsRequest(BaseModel):
    endpoint: str = "http://localhost:8188"
    vram_management: bool = False

class UpdateSettingsRequest(BaseModel):
    connector_type: str
    working_directory: Optional[str] = None
    refine_before_execution: bool = False
    renpy_sdk_path: Optional[str] = None
    lmstudio: Optional[LMStudioSettingsRequest] = None
    cline: Optional[ClineSettingsRequest] = None
    comfyui: Optional[ComfyUISettingsRequest] = None
