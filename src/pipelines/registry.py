"""Pipeline registry — single source of truth for available pipelines."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from pipelines.runner import Pipeline, PipelineRunner
from config.settings_manager import settings_manager


@dataclass
class PipelineDefinition:
    name: str
    description: str
    pipeline: Pipeline
    brief_schema: Dict[str, Any]       # documents required/optional brief fields
    enrich_brief: Optional[Callable[[dict], dict]] = None


def _enrich_renpy_brief(brief: dict) -> dict:
    length_config = {
        "short":  {"character_count": "2",   "scene_count": "3-4",  "lines_per_scene": "3-4"},
        "medium": {"character_count": "3",   "scene_count": "5-7",  "lines_per_scene": "5-7"},
        "long":   {"character_count": "3-4", "scene_count": "8-12", "lines_per_scene": "6-10"},
    }
    length = brief.get("length", "medium")
    config = length_config.get(length, length_config["medium"])
    return {**config, **brief}


def _build_registry() -> Dict[str, PipelineDefinition]:
    from pipelines.renpy.pipeline import RENPY_PIPELINE
    return {
        "renpy": PipelineDefinition(
            name="renpy",
            description="Generate a complete Ren'Py visual novel — story, characters, dialogue, background images, and a playable build.",
            pipeline=RENPY_PIPELINE,
            brief_schema={
                "required": ["title", "genre", "tone", "setting", "notes"],
                "optional": {
                    "length": "short | medium | long (default: medium)",
                    "character_count": "overrides length default",
                    "scene_count": "overrides length default",
                    "lines_per_scene": "overrides length default",
                },
            },
            enrich_brief=_enrich_renpy_brief,
        ),
    }


# Lazy singleton so imports don't trigger pipeline module loading at startup
_registry: Optional[Dict[str, PipelineDefinition]] = None


def get_registry() -> Dict[str, PipelineDefinition]:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def run_pipeline(name: str, brief: dict, working_dir: str) -> dict:
    """Run a named pipeline synchronously. Returns the final output files as a dict."""
    registry = get_registry()
    if name not in registry:
        available = list(registry.keys())
        raise ValueError(f"Unknown pipeline {name!r}. Available: {available}")

    defn = registry[name]
    if defn.enrich_brief:
        brief = defn.enrich_brief(brief)

    runner = PipelineRunner(working_dir=working_dir)
    success = runner.run(defn.pipeline, brief)
    if not success:
        raise RuntimeError(f"Pipeline {name!r} failed.")

    # Return all JSON outputs from the working dir
    import json
    from pathlib import Path
    outputs = {}
    for path in sorted(Path(working_dir).glob("*.json")):
        try:
            outputs[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return outputs
