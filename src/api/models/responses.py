from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    tools: List[str]

class SettingsResponse(BaseModel):
    connector_type: str
    model_category: str = "large"
    working_directory: Optional[str] = None
    refine_before_execution: bool = False
    renpy_sdk_path: Optional[str] = None
    parallel_fixes: Optional[int] = None
    tts: Optional[Dict[str, Any]] = None
    lmstudio: Optional[Dict[str, Any]] = None
    cline: Optional[Dict[str, Any]] = None
    comfyui: Optional[Dict[str, Any]] = None
