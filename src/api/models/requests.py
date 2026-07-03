from pydantic import BaseModel
from typing import Optional

class LMStudioSettingsRequest(BaseModel):
    base_url: str
    model: str
    max_tokens: Optional[int] = 50000

class ClineSettingsRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.cline.bot/api"
    model: str
    max_tokens: Optional[int] = 50000

class ComfyUISettingsRequest(BaseModel):
    endpoint: str = "http://localhost:8188"
    vram_management: bool = False

class UpdateSettingsRequest(BaseModel):
    # Optional fields default to None: the update merges with exclude_none, so a non-None
    # default would silently reset a setting the request didn't mention.
    connector_type: str
    model_category: Optional[str] = None
    working_directory: Optional[str] = None
    refine_before_execution: Optional[bool] = None
    renpy_sdk_path: Optional[str] = None
    parallel_fixes: Optional[int] = None
    lmstudio: Optional[LMStudioSettingsRequest] = None
    cline: Optional[ClineSettingsRequest] = None
    comfyui: Optional[ComfyUISettingsRequest] = None
