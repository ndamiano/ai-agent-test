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


class TrellisSettings(BaseModel):
    """TRELLIS.2-4B runs as a standalone HTTP server (tools/trellis_server.py, launched by its own
    cu128 venv + prebuilt Blackwell CUDA wheels); maestro POSTs sprites to it. It is THE mesh
    backend: an hd2d build FAILS LOUDLY (MeshBackendError) when this endpoint does not answer
    /health — no silent downgrade. The repo/weights paths live on the server's launch args."""
    endpoint: str = Field("http://localhost:8189", min_length=1)


class TTSSettings(BaseModel):
    endpoint: str = Field("http://localhost:8880", min_length=1)
    model: str = Field("", description="Optional model id the TTS server expects.")
    # Speaker presets the local TTS server exposes; cast members are mapped onto these
    # deterministically by id. Empty -> the server's default voice for every speaker.
    voices: list[str] = Field(default_factory=list)
    format: Literal["wav", "ogg", "mp3", "opus"] = "wav"


class MusicSettings(BaseModel):
    """Ambient/score generation. `backend` names a real model server (e.g. "musicgen") whose
    `endpoint` gets POSTed each track's prompt; empty (the default) skips the pass entirely and
    tracks ship as silent placeholders — no music beats a procedural tone. An unreachable backend
    degrades to silent placeholders too — music never blocks a build (mirrors the voice pass)."""
    backend: str = Field("", description="Real music model backend; empty = no music.")
    endpoint: str = Field("", description="HTTP endpoint for the music model.")
    seconds: int = Field(30, ge=1, description="Requested track length for a real backend.")


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


class AssetSettings(BaseModel):
    """tile_source "library" ships terrain from the curated pack in src/assets/tiles (per-run
    generated tiles only fill classes the library lacks); "generated" prefers the run's own
    tiles — flip it when generation clears the quality bar."""
    tile_source: Literal["library", "generated"] = "library"


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
    assets: Optional[AssetSettings] = None
    tts: Optional[TTSSettings] = None
    music: Optional[MusicSettings] = None
