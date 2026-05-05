import json
import struct
import zlib
from pathlib import Path
from typing import Dict

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, strip_fences

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)

_APPEARANCE_PROMPT = (
    "Output the appearance field: portrait description (age, build, features, clothing). "
    'Exactly: {"appearance": "..."}'
)

_VOICE_PROMPT = (
    "Output the speech_patterns field: how they talk (sentence length, vocabulary, habits, what they avoid). "
    'Exactly: {"speech_patterns": "..."}'
)


def _json_with_correction(agent: PipelineAgent, prompt: str, label: str) -> dict:
    content = strip_fences(agent.send(prompt))
    for _ in range(2):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            content = strip_fences(agent.send(
                "Invalid JSON. Return only the JSON object, no other text."
            ))
    raise RuntimeError(f"Failed to get valid JSON for {label}")


def generate_character(inputs: Dict, working_dir: Path) -> Dict:
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

    identity_prompt = render_template(_PROMPTS_DIR / "identity.txt", ctx)
    agent = PipelineAgent(_SYSTEM)

    identity   = _json_with_correction(agent, identity_prompt,    "character identity")
    appearance = _json_with_correction(agent, _APPEARANCE_PROMPT, "character appearance")
    voice      = _json_with_correction(agent, _VOICE_PROMPT,      "character voice")

    return {**identity, **appearance, **voice}


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
    _write_placeholder_png(filepath, 512, 768, (92, 58, 92))
    track_written_file(str(filepath))
    return {"status": "placeholder", "file": str(filepath), "error": error}


def _write_placeholder_png(path: Path, width: int, height: int, rgb: tuple):
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    idat_data = zlib.compress(row * height, level=1)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", idat_data)
        + chunk(b"IEND", b"")
    )
