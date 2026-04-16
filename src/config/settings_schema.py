"""Pydantic schemas for settings validation"""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class LLMStudioSettings(BaseModel):
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: int = Field(50000, ge=1)


class ClineSettings(BaseModel):
    api_key: str
    base_url: str = Field("https://api.cline.bot/api")
    model: str = Field(..., min_length=1)
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: int = Field(50000, ge=1)


class ComfyUISettings(BaseModel):
    endpoint: str = Field("http://localhost:8188", min_length=1)
    vram_management: bool = Field(False)


class AppSettings(BaseModel):
    connector_type: Literal["lmstudio", "cline"]
    working_directory: Optional[str] = "outputs"
    refine_before_execution: bool = False
    renpy_sdk_path: Optional[str] = None
    lmstudio: LLMStudioSettings
    cline: Optional[ClineSettings] = None
    comfyui: Optional[ComfyUISettings] = None
