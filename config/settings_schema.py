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


class AppSettings(BaseModel):
    connector_type: Literal["lmstudio", "cline"]
    working_directory: Optional[str] = "outputs"
    refine_before_execution: bool = False
    lmstudio: LLMStudioSettings
    cline: Optional[ClineSettings] = None
