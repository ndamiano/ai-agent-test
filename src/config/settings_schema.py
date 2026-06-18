"""Pydantic schemas for settings validation"""

from pydantic import BaseModel, Field
from typing import Dict, Literal, Optional

# Reasoning-effort knob for reasoning models. Only takes effect with api_style="responses"
# (reasoning.effort on the Responses API); chat/completions silently ignores it. "none"
# disables reasoning — the lever that stops a local model spending ~30k tokens thinking per
# node. None = the model's own default. ("off" is NOT a valid value — it errors.)
ReasoningLevel = Literal["none", "minimal", "low", "medium", "high", "xhigh"]

# Which OpenAI-compatible endpoint to use. "responses" (/v1/responses) is the only LM Studio
# path that honors reasoning effort; "chat" is /chat/completions.
ApiStyle = Literal["chat", "responses"]


class LLMStudioSettings(BaseModel):
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    max_tokens: int = Field(50000, ge=1)
    frequency_penalty: float = Field(0.5, ge=0.0, le=2.0)
    reasoning: Optional[ReasoningLevel] = None
    api_style: ApiStyle = "chat"


class ClineSettings(BaseModel):
    api_key: str
    base_url: str = Field("https://api.cline.bot/api")
    model: str = Field(..., min_length=1)
    max_tokens: int = Field(50000, ge=1)
    frequency_penalty: float = Field(0.5, ge=0.0, le=2.0)
    reasoning: Optional[ReasoningLevel] = None
    api_style: ApiStyle = "chat"


class ComfyUISettings(BaseModel):
    endpoint: str = Field("http://localhost:8188", min_length=1)
    vram_management: bool = Field(False)


class ModelCategorySettings(BaseModel):
    message_budget_chars: int = Field(30000, ge=1000)
    max_iterations: int = Field(10, ge=1)
    max_waves: int = Field(20, ge=1)
    use_json_mode: bool = Field(False)


DEFAULT_MODEL_CATEGORIES: Dict[str, ModelCategorySettings] = {
    "large":  ModelCategorySettings(message_budget_chars=30000, max_iterations=10, max_waves=20, use_json_mode=False),
    "medium": ModelCategorySettings(message_budget_chars=20000, max_iterations=8,  max_waves=15, use_json_mode=False),
    "small":  ModelCategorySettings(message_budget_chars=250000, max_iterations=6,  max_waves=10, use_json_mode=True),
}


class AppSettings(BaseModel):
    connector_type: Literal["lmstudio", "cline"]
    working_directory: Optional[str] = "outputs"
    refine_before_execution: bool = False
    renpy_sdk_path: Optional[str] = None
    model_category: Literal["large", "medium", "small"] = "large"
    lmstudio: LLMStudioSettings
    cline: Optional[ClineSettings] = None
    comfyui: Optional[ComfyUISettings] = None
