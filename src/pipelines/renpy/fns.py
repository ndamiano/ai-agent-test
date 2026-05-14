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


_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)

_DIALOGUE_FIELDS = {"id", "name", "description", "personality", "speech_patterns"}

_APPEARANCE_PROMPT = (
    'Output the appearance field: portrait description (age, build, features, clothing). '
    'Exactly: {"appearance": "..."}'
)

_VOICE_PROMPT = (
    'Output the speech_patterns field: how they talk (sentence length, vocabulary, habits, what they avoid). '
    'Exactly: {"speech_patterns": "..."}'
)

_SCENE_DIALOGUE_FIELDS = {"id", "setting_id", "summary", "character_states", "dramatic_question", "revelation", "what_changes", "setting_constraint"}


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


_CHARACTER_COLORS = [
    "#c8ffc8",
    "#c8c8ff",
    "#ffc8c8",
    "#ffe0a3",
    "#d8b4ff",
    "#a7f3d0",
]


def _renpy_character_concept(
    brief: Dict,
    story: Dict,
    settings: list,
    existing_characters: list,
    index: int,
    total: int,
) -> str:
    setting_summaries = [
        f"{s.get('name', s.get('id', 'setting'))}: {s.get('description', '')}"
        for s in settings
    ]
    existing = [
        f"{c.get('name')} ({c.get('role', 'unknown role')}): {c.get('description', '')}"
        for c in existing_characters
    ]

    return "\n".join([
        f"Create character {index} of {total} for a {brief.get('genre', '')} Ren'Py visual novel.",
        f"Tone: {brief.get('tone', '')}",
        f"Premise: {story.get('premise', '')}",
        f"Arc: {story.get('arc', '')}",
        f"Story beats: {json.dumps(story.get('story_beats', []), ensure_ascii=False)}",
        f"Settings: {' | '.join(setting_summaries)}",
        f"Existing characters: {' | '.join(existing) if existing else 'none yet'}",
        f"User notes: {brief.get('notes', '')}",
        "Make this character distinct from the existing cast and useful for the story's conflicts.",
    ])


def _normalize_renpy_character(character: Dict, index: int, portrait_file: str = "") -> Dict:
    char = dict(character)
    if not char.get("id") and char.get("name"):
        char["id"] = str(char["name"]).lower().replace(" ", "_")
    char.setdefault("name", f"Character {index}")
    char.setdefault("id", f"character_{index}")
    char.setdefault("role", "supporting")
    char.setdefault("description", "")
    char.setdefault("personality", [])
    char.setdefault("appearance", char.get("description", ""))
    char.setdefault("speech_patterns", "")
    char.setdefault("color", _CHARACTER_COLORS[(index - 1) % len(_CHARACTER_COLORS)])
    if portrait_file:
        char["portrait_file"] = portrait_file
    return char


def generate_characters(inputs: Dict, working_dir: Path) -> Dict:
    from pipelines.registry import run_subpipeline

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
        print(f"    [characters]  generating character subpipeline {label}")

        concept = _renpy_character_concept(brief, story, settings, characters, i + 1, total)
        sub_result = run_subpipeline(
            working_dir,
            "character",
            {
                "concept": concept,
                "tone": brief.get("tone", "balanced"),
                "role": "visual novel cast member",
                "setting": brief.get("setting", brief.get("genre", "")),
                "notes": brief.get("notes", ""),
            },
            run_id=f"character_{i + 1}",
        )

        generated = sub_result.get("outputs", {}).get("character", {})
        portrait = sub_result.get("outputs", {}).get("portrait_result", {})
        portrait_file = portrait.get("file") if isinstance(portrait, dict) else ""
        characters.append(_normalize_renpy_character(generated, i + 1, portrait_file or ""))

    return {"characters": characters}


def generate_scenes(inputs: Dict, working_dir: Path) -> Dict:
    brief = inputs.get("brief", {})

    story = inputs.get("story", {})
    if isinstance(story, dict) and "arc" not in story:
        story = story.get("story", {})

    characters = inputs.get("characters", [])
    if isinstance(characters, dict):
        characters = characters.get("characters", [])

    settings = inputs.get("settings", [])
    if isinstance(settings, dict):
        settings = settings.get("settings", [])

    beats = story.get("story_beats", [])
    character_refs = [
        {"id": c["id"], "name": c["name"], "role": c.get("role", ""), "personality": c.get("personality", [])}
        for c in characters
    ]
    setting_refs = [{"id": s["id"], "name": s["name"]} for s in settings]

    scenes = []
    for i, beat in enumerate(beats):
        label = beat.get("label", beat.get("beat_id", str(i + 1)))
        print(f"    [scenes]  generating scene {i + 1}/{len(beats)}: {label}")

        ctx = {
            **brief,
            "arc":              story.get("arc", ""),
            "premise":          story.get("premise", ""),
            "character_refs":   character_refs,
            "setting_refs":     setting_refs,
            "beat":             beat,
            "completed_scenes": [{"id": s.get("id"), "setting_id": s.get("setting_id"), "character_ids": s.get("character_ids", [])} for s in scenes],
        }
        prompt = render_template(_PROMPTS_DIR / "scene.txt", ctx)
        agent = PipelineAgent(_SYSTEM)
        scene = _json_with_correction(agent, prompt, f"scene {i + 1}")
        scenes.append(scene)

    return {"scenes": scenes}


def _generate_dialogue_json(prompt: str, label: str, max_attempts: int = 3) -> Dict:
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        agent = PipelineAgent(_SYSTEM)
        content = strip_fences(agent.send(prompt))

        for correction_attempt in range(2):
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                last_error = str(e)
                if correction_attempt == 1:
                    break
                print(f"    [dialogue]  invalid JSON for {label}, sending correction...")
                content = strip_fences(agent.send(
                    "Invalid JSON. Return only the JSON object, no other text."
                ))

        if attempt < max_attempts:
            print(f"    [dialogue]  retrying {label} ({attempt + 1}/{max_attempts})")

    raise RuntimeError(f"Failed to generate dialogue JSON for {label} ({last_error})")


def _dialogue_chunk_prompt(scene_inputs: Dict, previous_lines: list, line_count: int, chunk_index: int, chunk_total: int) -> str:
    return "\n".join([
        f"You are writing a small chunk of dialogue for a {scene_inputs['genre']} visual novel.",
        f"Tone: {scene_inputs['tone']}",
        "",
        "Characters in this scene:",
        json.dumps(scene_inputs["characters"], indent=2, ensure_ascii=False),
        "",
        "Setting:",
        json.dumps(scene_inputs["setting"], indent=2, ensure_ascii=False),
        "",
        "Scene:",
        json.dumps(scene_inputs["scene"], indent=2, ensure_ascii=False),
        "",
        f"Previously written lines for this scene ({len(previous_lines)}):",
        json.dumps(previous_lines, indent=2, ensure_ascii=False),
        "",
        f"Write the next {line_count} lines only. This is chunk {chunk_index} of {chunk_total}.",
        "Each line must have character_id and text. character_id must match a character id above, or null for narrator.",
        "Keep each text value concise enough for a visual novel dialogue box.",
        "Do not repeat previous lines.",
        "Maintain continuity from previous_lines.",
        "If this is the final chunk, make what_changes felt by the final line.",
        "",
        'Output exactly: {"lines": [{"character_id": null, "text": "..."}, {"character_id": "char_id", "text": "..."}]}',
    ])


def _generate_dialogue_scene(scene_inputs: Dict, label: str, max_attempts: int = 3) -> Dict:
    target_lines = max(1, _parse_count(scene_inputs.get("lines_per_scene", "16")))
    chunk_size = 4
    lines = []
    chunk_total = (target_lines + chunk_size - 1) // chunk_size

    for chunk_index in range(1, chunk_total + 1):
        remaining = target_lines - len(lines)
        count = min(chunk_size, remaining)
        prompt = _dialogue_chunk_prompt(scene_inputs, lines, count, chunk_index, chunk_total)
        chunk = _generate_dialogue_json(prompt, f"{label} chunk {chunk_index}", max_attempts=max_attempts)
        chunk_lines = chunk.get("lines")
        if not isinstance(chunk_lines, list) or not chunk_lines:
            raise RuntimeError(f"Dialogue chunk for {label} did not return lines")
        lines.extend(chunk_lines[:count])

    scene = scene_inputs["scene"]
    return {
        "scene_id": scene.get("id", label),
        "setting_id": scene.get("setting_id", ""),
        "lines": lines[:target_lines],
    }


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
    completed_scenes = []
    for i, scene in enumerate(scenes):
        label = scene.get("title", scene.get("id", str(i + 1)))
        print(f"    [dialogue]  scene {i + 1}/{len(scenes)}: {label}")

        character_ids = set(scene.get("character_ids", []))
        scene_characters = [
            {k: v for k, v in c.items() if k in _DIALOGUE_FIELDS}
            for c in characters if c["id"] in character_ids
        ] if character_ids else [
            {k: v for k, v in c.items() if k in _DIALOGUE_FIELDS}
            for c in characters
        ]

        setting_full = settings_by_id.get(scene.get("setting_id", ""), {})
        scene_inputs = {
            "genre":           brief.get("genre", ""),
            "tone":            brief.get("tone", ""),
            "lines_per_scene": brief.get("lines_per_scene", "16-20"),
            "characters":      scene_characters,
            "scene":           {k: v for k, v in scene.items() if k in _SCENE_DIALOGUE_FIELDS},
            "setting":         {k: v for k, v in setting_full.items() if k != "image_file"},
        }
        completed_scenes.append(_generate_dialogue_scene(scene_inputs, label))

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

    from tools.execution_context import track_written_file

    generated = []
    failed = []
    for img in game_def.get("images", []):
        filepath = images_dir / img["file"]
        setting = settings_by_id.get(img["id"], {})
        description = setting.get("description", f"A scene called {img['id']}")
        prompt = f"{description}, visual novel background, high quality, detailed"

        print(f"    [images]  generating: {img['file']}")
        result = generate_image(prompt)

        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], filepath)
            track_written_file(str(filepath))
            generated.append(img["file"])
            print(f"    [images]  saved: {img['file']}")
        else:
            error = result.get("error", "unknown error")
            print(f"    [images]  failed ({error}), writing placeholder: {img['file']}")
            _write_solid_png(filepath, 1280, 720, (58, 58, 92))
            track_written_file(str(filepath))
            failed.append({"file": img["file"], "error": error})

    for char in game_def.get("characters", []):
        image_file = char.get("image_file")
        if not image_file:
            continue
        filepath = images_dir / image_file
        char_data = characters_by_id.get(char["id"], {})
        appearance = char_data.get("appearance", f"A character named {char['name']}")
        prompt = (
            f"{char['name']}, {appearance}, "
            "visual novel character portrait, full body, simple background, high quality, detailed"
        )

        print(f"    [images]  generating portrait: {image_file}")
        portrait_file = char_data.get("portrait_file")
        if portrait_file and Path(portrait_file).exists():
            shutil.copy2(portrait_file, filepath)
            track_written_file(str(filepath))
            generated.append(image_file)
            print(f"    [images]  copied portrait: {image_file}")
            continue

        result = generate_image(prompt)

        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], filepath)
            track_written_file(str(filepath))
            generated.append(image_file)
            print(f"    [images]  saved: {image_file}")
        else:
            error = result.get("error", "unknown error")
            print(f"    [images]  portrait failed ({error}), writing placeholder: {image_file}")
            _write_solid_png(filepath, 512, 768, (92, 58, 92))
            track_written_file(str(filepath))
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
