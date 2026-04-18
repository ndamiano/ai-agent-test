import json
import os
import struct
import sys
import zlib
from pathlib import Path
from typing import Dict

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, strip_fences

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _parse_count(val) -> int:
    s = str(val).strip()
    if "-" in s:
        return int(s.split("-")[-1])
    return int(s)


_CHARACTER_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)

_APPEARANCE_PROMPT = (
    'Now output the appearance field only — physical description specific enough to '
    'generate a portrait: age, build, notable features, typical clothing. '
    'Output exactly: {"appearance": "your description here"}'
)

_VOICE_PROMPT = (
    'Now output the speech_patterns field only — concrete notes on how this character talks: '
    'sentence length, vocabulary, habits, what they avoid saying. '
    'Output exactly: {"speech_patterns": "your notes here"}'
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


def generate_characters(inputs: Dict, working_dir: Path) -> Dict:
    brief = inputs.get("brief", {})

    story = inputs.get("story", {})
    if isinstance(story, dict) and "arc" not in story:
        story = story.get("story", {})

    settings = inputs.get("settings", [])
    if isinstance(settings, dict):
        settings = settings.get("settings", [])

    total = _parse_count(brief.get("character_count", 2))
    characters = []

    for i in range(total):
        label = f"{i + 1}/{total}"
        print(f"    [characters]  generating character {label}")

        ctx = {
            **brief,
            "index":               i + 1,
            "total":               total,
            "arc":                 story.get("arc", ""),
            "premise":             story.get("premise", ""),
            "story_beats":         story.get("story_beats", []),
            "settings":            settings,
            "existing_characters": characters,
        }
        identity_prompt = render_template(_PROMPTS_DIR / "characters_identity.txt", ctx)
        agent = PipelineAgent(_CHARACTER_SYSTEM)

        identity   = _json_with_correction(agent, identity_prompt,   f"character {label} identity")
        appearance = _json_with_correction(agent, _APPEARANCE_PROMPT, f"character {label} appearance")
        voice      = _json_with_correction(agent, _VOICE_PROMPT,      f"character {label} voice")

        characters.append({**identity, **appearance, **voice})

    return {"characters": characters}


def dialogue(inputs: Dict, working_dir: Path) -> Dict:
    template_path = _PROMPTS_DIR / "dialogue.txt"

    scenes = inputs.get("scenes", [])
    if isinstance(scenes, dict):
        scenes = scenes.get("scenes", [])

    characters = inputs.get("characters", [])
    if isinstance(characters, dict):
        characters = characters.get("characters", [])

    settings = inputs.get("settings", [])
    if isinstance(settings, dict):
        settings = settings.get("settings", [])
    settings_by_id = {s["id"]: s for s in settings}

    brief = inputs.get("brief", {})
    _system = (
        "You are a precise creative writing assistant. Output only valid JSON. "
        "No markdown, no explanation, no code fences."
    )

    completed_scenes = []
    for i, scene in enumerate(scenes):
        label = scene.get("title", scene.get("id", str(i + 1)))
        print(f"    [dialogue]  scene {i + 1}/{len(scenes)}: {label}")

        scene_inputs = {
            "brief":           brief,
            "genre":           brief.get("genre", ""),
            "tone":            brief.get("tone", ""),
            "notes":           brief.get("notes", ""),
            "lines_per_scene": brief.get("lines_per_scene", "4-6"),
            "characters":      characters,
            "scene":           scene,
            "setting":         settings_by_id.get(scene.get("setting_id", ""), {}),
        }
        prompt = render_template(template_path, scene_inputs)
        agent = PipelineAgent(_system)

        content = strip_fences(agent.send(prompt))
        success = False
        for _ in range(2):
            try:
                completed_scenes.append(json.loads(content))
                success = True
                break
            except json.JSONDecodeError:
                print(f"    [dialogue]  invalid JSON, sending correction...")
                content = strip_fences(agent.send(
                    "Invalid JSON. Return only the JSON object, no other text."
                ))

        if not success:
            raise RuntimeError(f"Failed to generate dialogue for scene: {label}")

    return {"dialogue_scenes": completed_scenes}


def package(inputs: Dict, working_dir: Path) -> Dict:
    brief = inputs.get("brief", {})

    characters = inputs.get("characters", [])
    if isinstance(characters, dict):
        characters = characters.get("characters", [])

    settings = inputs.get("settings", [])
    if isinstance(settings, dict):
        settings = settings.get("settings", [])

    dialogue_scenes = inputs.get("dialogue_scenes", [])

    builder_characters = [
        {"id": c["id"], "name": c["name"], "color": c.get("color", "#ffffff"), "image_file": f"{c['id']}.png"}
        for c in characters
    ]

    seen: set = set()
    builder_images = []
    for setting in settings:
        filename = setting["image_file"]
        if filename not in seen:
            builder_images.append({"id": setting["id"], "file": filename})
            seen.add(filename)

    valid_setting_ids = {img["id"] for img in builder_images}
    builder_scenes = []
    for scene in dialogue_scenes:
        setting_id = scene.get("setting_id", "")
        if setting_id and setting_id not in valid_setting_ids:
            raise ValueError(
                f"scene references unknown setting_id {setting_id!r}. "
                f"Available: {sorted(valid_setting_ids)}"
            )
        builder_scenes.append({
            "background": setting_id,
            "lines": [
                {"who": line.get("character_id"), "say": line["text"]}
                for line in scene.get("lines", [])
            ],
        })

    return {
        "title":      brief.get("title", "Untitled"),
        "output_dir": str(working_dir / "game_output"),
        "characters": builder_characters,
        "images":     builder_images,
        "scenes":     builder_scenes,
    }


def generate_images(inputs: Dict, working_dir: Path) -> Dict:
    import shutil
    from tools.comfyui_tools import generate_image

    game_def = inputs.get("game_definition", inputs)
    output_dir = Path(game_def.get("output_dir", str(working_dir / "game_output")))
    images_dir = output_dir / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    raw_settings = inputs.get("settings", [])
    if isinstance(raw_settings, dict):
        raw_settings = raw_settings.get("settings", [])
    settings_by_id = {s["id"]: s for s in raw_settings}

    raw_characters = inputs.get("characters", [])
    if isinstance(raw_characters, dict):
        raw_characters = raw_characters.get("characters", [])
    characters_by_id = {c["id"]: c for c in raw_characters}

    generated = []
    failed = []
    for img in game_def.get("images", []):
        filepath = images_dir / img["file"]
        if filepath.exists():
            print(f"    [images]  skip (exists): {img['file']}")
            continue

        setting = settings_by_id.get(img["id"], {})
        description = setting.get("description", f"A scene called {img['id']}")
        prompt = f"{description}, visual novel background, high quality, detailed"

        print(f"    [images]  generating: {img['file']}")
        result = generate_image(prompt)

        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], filepath)
            generated.append(img["file"])
            print(f"    [images]  saved: {img['file']}")
        else:
            error = result.get("error", "unknown error")
            print(f"    [images]  failed ({error}), writing placeholder: {img['file']}")
            _write_solid_png(filepath, 1280, 720, (58, 58, 92))
            failed.append({"file": img["file"], "error": error})

    for char in game_def.get("characters", []):
        image_file = char.get("image_file")
        if not image_file:
            continue
        filepath = images_dir / image_file
        if filepath.exists():
            print(f"    [images]  skip (exists): {image_file}")
            continue

        char_data = characters_by_id.get(char["id"], {})
        appearance = char_data.get("appearance", f"A character named {char['name']}")
        prompt = (
            f"{char['name']}, {appearance}, "
            "visual novel character portrait, full body, simple background, high quality, detailed"
        )

        print(f"    [images]  generating portrait: {image_file}")
        result = generate_image(prompt)

        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], filepath)
            generated.append(image_file)
            print(f"    [images]  saved: {image_file}")
        else:
            error = result.get("error", "unknown error")
            print(f"    [images]  portrait failed ({error}), writing placeholder: {image_file}")
            _write_solid_png(filepath, 512, 768, (92, 58, 92))
            failed.append({"file": image_file, "error": error})

    return {"status": "ok", "generated": generated, "failed": failed}


def _write_solid_png(path: Path, width: int, height: int, rgb: tuple):
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    raw = row * height
    idat_data = zlib.compress(raw, level=1)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", idat_data)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _get_sdk_path() -> str:
    try:
        from config.settings_manager import settings_manager
        sdk = settings_manager.get_settings().get("renpy_sdk_path") or ""
        if sdk:
            return sdk
    except Exception:
        pass
    return os.environ.get("RENPY_SDK", "")


def build(inputs: Dict, working_dir: Path) -> Dict:
    builder_path = Path(__file__).parent / "renpy_builder.py"
    if not builder_path.exists():
        raise FileNotFoundError(f"renpy_builder.py not found at {builder_path}")

    if str(builder_path.parent) not in sys.path:
        sys.path.insert(0, str(builder_path.parent))
    from renpy_builder import build_game  # noqa: PLC0415

    game_definition = inputs.get("game_definition", inputs)
    sdk_path = _get_sdk_path()

    result = build_game(
        game_definition,
        sdk_path=sdk_path if sdk_path else None,
        distribute=bool(sdk_path),
    )

    return {
        "status": "built",
        "output_dir": game_definition.get("output_dir", ""),
        **(result or {}),
    }
