import json
import os
import sys
from pathlib import Path
from typing import Dict

from engine.pipeline_runner import render_template, strip_fences

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def dialogue(inputs: Dict, working_dir: Path) -> Dict:
    """One LLM call per scene to keep output size bounded and avoid truncation."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from llm_clients.connector_selector import get_connector
    connector = get_connector()

    template_path = _PROMPTS_DIR / "dialogue.txt"

    scenes = inputs.get("scenes", [])
    if isinstance(scenes, dict):
        scenes = scenes.get("scenes", [])

    characters = inputs.get("characters", [])
    if isinstance(characters, dict):
        characters = characters.get("characters", [])

    brief = inputs.get("brief", {})

    completed_scenes = []
    for i, scene in enumerate(scenes):
        label = scene.get("title", scene.get("id", str(i + 1)))
        print(f"    [dialogue]  scene {i + 1}/{len(scenes)}: {label}")

        scene_inputs = {
            "brief":      brief,
            "genre":      brief.get("genre", ""),
            "tone":       brief.get("tone", ""),
            "notes":      brief.get("notes", ""),
            "characters": characters,
            "scene":      scene,
        }
        prompt = render_template(template_path, scene_inputs)

        messages = [
            {
                "role": "system",
                "content": "You are a precise creative writing assistant. Output only valid JSON. "
                           "No markdown, no explanation, no code fences.",
            },
            {"role": "user", "content": prompt},
        ]

        success = False
        for attempt in range(1, 4):
            result = connector.generate_with_tools(messages, [])
            if "error" in result:
                print(f"    [dialogue]  LLM error on attempt {attempt}: {result['error']}")
                continue

            content = strip_fences(result["choices"][0]["message"]["content"].strip())
            try:
                completed_scenes.append(json.loads(content))
                success = True
                break
            except json.JSONDecodeError:
                print(f"    [dialogue]  invalid JSON on attempt {attempt}, retrying...")

        if not success:
            raise RuntimeError(f"Failed to generate dialogue for scene: {label}")

    return {"scenes": completed_scenes}


def package(inputs: Dict, working_dir: Path) -> Dict:
    brief = inputs.get("brief", {})

    characters = inputs.get("characters", [])
    if isinstance(characters, dict):
        characters = characters.get("characters", [])

    settings = inputs.get("settings", [])
    if isinstance(settings, dict):
        settings = settings.get("settings", [])

    # Read dialogue.json directly — both scenes.json and dialogue.json have a "scenes" key
    # so the merged inputs dict is ambiguous.
    dialogue_data = json.loads((working_dir / "dialogue.json").read_text(encoding="utf-8"))
    dialogue_scenes = dialogue_data.get("scenes", []) if isinstance(dialogue_data, dict) else []

    builder_characters = [
        {"id": c["id"], "name": c["name"], "color": c.get("color", "#ffffff")}
        for c in characters
    ]

    seen: set = set()
    builder_images = []
    for setting in settings:
        filename = setting["image_file"]
        if filename not in seen:
            builder_images.append({"id": setting["id"], "file": filename})
            seen.add(filename)

    builder_scenes = [
        {
            "background": scene.get("setting_id", ""),
            "lines": [
                {"who": line.get("character_id"), "say": line["text"]}
                for line in scene.get("lines", [])
            ],
        }
        for scene in dialogue_scenes
    ]

    return {
        "title":      brief.get("title", "Untitled"),
        "output_dir": str(working_dir / "game_output"),
        "characters": builder_characters,
        "images":     builder_images,
        "scenes":     builder_scenes,
    }


def build(inputs: Dict, working_dir: Path) -> Dict:
    builder_path = Path(__file__).parent / "renpy_builder.py"
    if not builder_path.exists():
        raise FileNotFoundError(f"renpy_builder.py not found at {builder_path}")

    sys.path.insert(0, str(builder_path.parent))
    from renpy_builder import build_game  # noqa: PLC0415

    game_definition = inputs.get("game_definition", inputs)
    sdk_path = os.environ.get("RENPY_SDK", "")

    build_game(
        game_definition,
        sdk_path=sdk_path if sdk_path else None,
        distribute=bool(sdk_path),
    )

    return {"status": "built", "output_dir": game_definition.get("output_dir", "")}
