"""Pydantic schemas for settings validation"""

from pydantic import BaseModel, Field
from typing import Dict, Literal, Optional

# Reasoning-effort knob for reasoning models, sent as reasoning.effort on the Responses API
# (the only endpoint this connector speaks). "none" disables reasoning — the lever that stops
# a local model spending ~30k tokens thinking per node. None = the model's own default.
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
    # Second ComfyUI install carrying the ideogram4 stack; when set, terrain-tile jobs render
    # there with structured JSON captions (the tile-lab quality winner) instead of the default
    # endpoint's DreamShaper formulas.
    tile_endpoint: str = Field("")
    # ComfyUI endpoint carrying the Hunyuan3D-2.1 checkpoint; when set (and the checkpoint is
    # present), feature sprites are turned into .glb meshes for the HD-2D world. Falls back to
    # tile_endpoint. Empty disables mesh generation (features stay billboards).
    mesh_endpoint: str = Field("")
    # Feature-mesh backend: "hunyuan" (ComfyUI, fast, shape-only — the sprite is projected on
    # for colour) or "trellis" (standalone TRELLIS.2-4B, slower, native PBR textures). Bake-off
    # winner is trellis; hunyuan stays the fast fallback and the default. Requires `trellis`
    # below when set to "trellis".
    mesh_backend: str = Field("hunyuan")


class TrellisSettings(BaseModel):
    """TRELLIS.2-4B runs standalone (its own cu128 venv + prebuilt Blackwell CUDA wheels), so
    maestro shells out to it. All three paths must exist for the trellis mesh backend to engage;
    otherwise the build silently falls back to hunyuan/billboards."""
    python: str = Field("", description="Path to the TRELLIS venv python (cu128).")
    repo: str = Field("", description="Path to the microsoft/TRELLIS.2 checkout.")
    weights: str = Field("", description="Path to the TRELLIS.2-4B weights dir.")


class TTSSettings(BaseModel):
    endpoint: str = Field("http://localhost:8880", min_length=1)
    model: str = Field("", description="Optional model id the TTS server expects.")
    # Speaker presets the local TTS server exposes; cast members are mapped onto these
    # deterministically by id. Empty -> the server's default voice for every speaker.
    voices: list[str] = Field(default_factory=list)
    format: Literal["wav", "ogg", "mp3", "opus"] = "wav"


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
    connector_type: Literal["lmstudio", "cline", "openrouter"]
    working_directory: Optional[str] = "outputs"
    refine_before_execution: bool = False
    # Concurrent build fixes (parallel LLM calls per loop step). >1 needs an inference server that
    # batches concurrent requests (LM Studio does); tool writes stay serialized either way.
    parallel_fixes: int = Field(1, ge=1, le=8)
    renpy_sdk_path: Optional[str] = None
    model_category: Literal["large", "medium", "small"] = "large"
    lmstudio: LLMStudioSettings
    cline: Optional[ClineSettings] = None
    comfyui: Optional[ComfyUISettings] = None
    trellis: Optional[TrellisSettings] = None
    tts: Optional[TTSSettings] = None
