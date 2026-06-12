import json
from pathlib import Path
from typing import Dict

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, JSON_SYSTEM as _SYSTEM, json_with_correction as _json_with_correction
from utils.image import write_solid_png

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def generate_identity(inputs: Dict, working_dir: Path) -> Dict:
    brief = inputs.get("brief", {})
    concept = inputs.get("concept", {})
    ctx = {
        **brief,
        "name":            concept.get("name", ""),
        "role":            concept.get("role", ""),
        "archetype":       concept.get("archetype", ""),
        "backstory_hook":  concept.get("backstory_hook", ""),
        "goal":            concept.get("goal", ""),
        "flaw":            concept.get("flaw", ""),
        "setting_context": concept.get("setting_context", ""),
    }
    prompt = render_template(_PROMPTS_DIR / "identity.txt", ctx)
    agent = PipelineAgent(_SYSTEM)
    return _json_with_correction(agent, prompt, "character identity")


def generate_appearance(inputs: Dict, working_dir: Path) -> Dict:
    identity = inputs.get("identity", {})
    brief = inputs.get("brief", {})
    ctx = {
        "name":        identity.get("name", ""),
        "description": identity.get("description", ""),
        "personality": json.dumps(identity.get("personality", [])),
        "setting":     brief.get("setting", ""),
        "tone":        brief.get("tone", ""),
    }
    prompt = render_template(_PROMPTS_DIR / "appearance.txt", ctx)
    agent = PipelineAgent(_SYSTEM)
    return _json_with_correction(agent, prompt, "character appearance")


def generate_voice(inputs: Dict, working_dir: Path) -> Dict:
    identity = inputs.get("identity", {})
    ctx = {
        "name":        identity.get("name", ""),
        "description": identity.get("description", ""),
        "personality": json.dumps(identity.get("personality", [])),
        "motivation":  identity.get("motivation", ""),
        "conflict":    identity.get("conflict", ""),
    }
    prompt = render_template(_PROMPTS_DIR / "voice.txt", ctx)
    agent = PipelineAgent(_SYSTEM)
    return _json_with_correction(agent, prompt, "character voice")


def generate_examples(inputs: Dict, working_dir: Path) -> Dict:
    identity = inputs.get("identity", {})
    voice    = inputs.get("voice", {})
    brief    = inputs.get("brief", {})
    ctx = {
        "name":            identity.get("name", ""),
        "description":     identity.get("description", ""),
        "personality":     json.dumps(identity.get("personality", [])),
        "speech_patterns": voice.get("speech_patterns", ""),
        "tone":            brief.get("tone", "balanced"),
    }
    prompt = render_template(_PROMPTS_DIR / "examples.txt", ctx)
    agent  = PipelineAgent(_SYSTEM)
    result = _json_with_correction(agent, prompt, "character examples")
    return {"example_dialogue": result.get("example_dialogue", [])}


def assemble_character(inputs: Dict, working_dir: Path) -> Dict:
    identity = inputs.get("identity", {})
    # Read directly to avoid _load stem-key collision
    appearance = json.loads((working_dir / "appearance.json").read_text())
    voice      = json.loads((working_dir / "voice.json").read_text())
    examples   = json.loads((working_dir / "examples.json").read_text())
    return {**identity, **appearance, **voice, **examples}


def generate_portrait(inputs: Dict, working_dir: Path) -> Dict:
    import shutil
    from tools.comfyui_tools import generate_image
    from tools.execution_context import track_written_file

    character = inputs.get("character", inputs)
    name = character.get("name", "character")
    appearance = character.get("appearance", f"A character named {name}")

    portrait_dir = working_dir / "portraits"
    portrait_dir.mkdir(parents=True, exist_ok=True)
    filepath = portrait_dir / f"{name.lower().replace(' ', '_')}.png"

    prompt = (
        f"{name}, {appearance}, "
        "character portrait, detailed face, expressive, high quality, digital art"
    )

    print(f"    [portrait]  generating: {filepath.name}")
    result = generate_image(prompt)

    if result.get("success") and result.get("saved_paths"):
        shutil.copy2(result["saved_paths"][0], filepath)
        track_written_file(str(filepath))
        print(f"    [portrait]  saved: {filepath.name}")
        return {"status": "ok", "file": str(filepath)}

    error = result.get("error", "unknown error")
    print(f"    [portrait]  failed ({error}), writing placeholder")
    write_solid_png(filepath, 512, 768, (92, 58, 92))
    track_written_file(str(filepath))
    return {"status": "placeholder", "file": str(filepath), "error": error}
