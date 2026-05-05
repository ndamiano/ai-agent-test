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
        "short":  {"character_count": "2",   "scene_count": "3-4",  "lines_per_scene": "12-16"},
        "medium": {"character_count": "3",   "scene_count": "5-7",  "lines_per_scene": "16-22"},
        "long":   {"character_count": "3-4", "scene_count": "8-12", "lines_per_scene": "20-28"},
    }
    length = brief.get("length", "medium")
    config = length_config.get(length, length_config["medium"])
    return {**config, **brief}


def _enrich_character_brief(brief: dict) -> dict:
    return {
        "tone":    "balanced — neither too dark nor too light",
        "role":    "unspecified",
        "setting": "unspecified",
        "notes":   "",
        **brief,
    }


def _enrich_ttrpg_brief(brief: dict) -> dict:
    scale_config = {
        "one-shot": {"npc_count": "4", "encounter_count": "3"},
        "short":    {"npc_count": "6", "encounter_count": "5"},
        "campaign": {"npc_count": "10", "encounter_count": "8"},
    }
    scale = brief.get("scale", "short")
    config = scale_config.get(scale, scale_config["short"])
    return {
        "scale":        scale,
        "player_count": "4",
        "notes":        "",
        **config,
        **brief,
    }


def _build_registry() -> Dict[str, PipelineDefinition]:
    from pipelines.renpy.pipeline import RENPY_PIPELINE
    from pipelines.character.pipeline import CHARACTER_PIPELINE
    from pipelines.ttrpg.pipeline import TTRPG_PIPELINE
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
        "character": PipelineDefinition(
            name="character",
            description="Generate a richly detailed character — identity, personality, appearance, voice, and portrait image.",
            pipeline=CHARACTER_PIPELINE,
            brief_schema={
                "required": ["concept"],
                "optional": {
                    "tone":    "emotional register (default: balanced)",
                    "role":    "narrative function hint — hero, villain, mentor, etc.",
                    "setting": "world or genre context",
                    "notes":   "additional constraints or details",
                },
            },
            enrich_brief=_enrich_character_brief,
        ),
        "ttrpg": PipelineDefinition(
            name="ttrpg",
            description="Generate a complete TTRPG campaign — world, factions, NPCs, encounters, plot hooks, and a printable campaign document.",
            pipeline=TTRPG_PIPELINE,
            brief_schema={
                "required": ["title", "genre", "tone", "premise"],
                "optional": {
                    "scale":        "one-shot | short | campaign (default: short)",
                    "npc_count":    "overrides scale default",
                    "player_count": "number of players (default: 4)",
                    "notes":        "additional constraints or themes",
                },
            },
            enrich_brief=_enrich_ttrpg_brief,
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
