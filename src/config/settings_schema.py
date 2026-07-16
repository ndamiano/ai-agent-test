"""Pydantic schemas for settings validation"""

from pydantic import BaseModel, Field
from typing import Dict, Literal, Optional

# Reasoning-effort knob for reasoning models, sent as reasoning.effort on the Responses API
# (the only endpoint this connector speaks). "none" disables reasoning — the lever that stops
# a local model spending ~30k tokens thinking per call. None = the model's own default.
# ("off" is NOT a valid value — it errors.)
ReasoningLevel = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class LLMStudioSettings(BaseModel):
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    dialogue_model: str = Field("", description=(
        "Optional second model for DIALOGUE inference only (the scene turn loop + closer). "
        "Lets a strong prose model speak the lines while `model` keeps doing tools/JSON. "
        "Empty = use `model` for everything."))
    max_tokens: int = Field(50000, ge=1)
    n_ctx: int = Field(32768, ge=1, description=(
        "The server's runtime context window in tokens (llama-server `-c`). The local router does not "
        "report it, so set it to match your launch flag. MessageBuilder budgets input against this so "
        "it trims the transcript BEFORE the prompt overflows the window; too-large a value = no trim."))
    frequency_penalty: float = Field(0.5, ge=0.0, le=2.0)
    reasoning: Optional[ReasoningLevel] = None


class ClineSettings(BaseModel):
    api_key: str
    base_url: str = Field("https://api.cline.bot/api")
    model: str = Field(..., min_length=1)
    max_tokens: int = Field(50000, ge=1)
    frequency_penalty: float = Field(0.5, ge=0.0, le=2.0)
    reasoning: Optional[ReasoningLevel] = None


class ComfyUISettings(BaseModel):
    endpoint: str = Field("http://localhost:8188", min_length=1)
    vram_management: bool = Field(False)


class TrellisSettings(BaseModel):
    """TRELLIS.2-4B runs as a standalone HTTP server (tools/trellis_server.py, launched by its own
    cu128 venv + prebuilt Blackwell CUDA wheels); maestro POSTs sprites to it. It is THE mesh
    backend for the reskin 3D path: when this endpoint is down the mesh render is skipped and the
    entity falls back to its primitive shape. The repo/weights paths live on the server's launch args."""
    endpoint: str = Field("http://localhost:8189", min_length=1)


class ModelCategorySettings(BaseModel):
    message_budget_chars: int = Field(30000, ge=1000)
    max_iterations: int = Field(10, ge=1)
    use_json_mode: bool = Field(False)


DEFAULT_MODEL_CATEGORIES: Dict[str, ModelCategorySettings] = {
    "large":  ModelCategorySettings(message_budget_chars=30000, max_iterations=10, use_json_mode=False),
    "medium": ModelCategorySettings(message_budget_chars=20000, max_iterations=8,  use_json_mode=False),
    "small":  ModelCategorySettings(message_budget_chars=250000, max_iterations=6,  use_json_mode=True),
}


class AppSettings(BaseModel):
    connector_type: Literal["lmstudio", "cline", "openrouter"]
    working_directory: Optional[str] = "outputs"
    # Concurrent build fixes (parallel LLM calls per loop step). >1 needs an inference server that
    # batches concurrent requests (LM Studio does); tool writes stay serialized either way.
    parallel_fixes: int = Field(1, ge=1, le=8)
    model_category: Literal["large", "medium", "small"] = "large"
    lmstudio: LLMStudioSettings
    cline: Optional[ClineSettings] = None
    comfyui: Optional[ComfyUISettings] = None
    trellis: Optional[TrellisSettings] = None
