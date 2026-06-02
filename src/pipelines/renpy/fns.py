import json
import os
import shutil
from pathlib import Path
from typing import Dict, List

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, strip_fences
from pipelines.renpy._script import (
    _bible_summary,
    _character_vars_block,
    _build_cg_block,
    _validate_scene_script,
    _scenes_for_character,
    _postprocess_script,
    _validate_and_repair,
    _stitch_script,
    _write_options_rpy,
    run_final_lint,
    _SCRIPT_SYSTEM,
)
from pipelines.renpy.renpy_builder import _copy_templates, _distribute
from utils.image import write_solid_png

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)

_CHARACTER_COLORS = [
    "#c8ffc8",
    "#c8c8ff",
    "#ffc8c8",
    "#ffe0a3",
    "#d8b4ff",
    "#a7f3d0",
]

_BEAT_TO_EXPRESSION = {
    "tense":           "worried",
    "confrontational": "angry",
    "melancholic":     "sad",
    "revelatory":      "surprised",
    "hopeful":         "happy",
    "tender":          "happy",
    "ominous":         "scared",
}

_REQUIRED_BRIEF_FIELDS = ("genre", "tone", "setting")


def _validate_brief(brief: Dict) -> None:
    missing = [f for f in _REQUIRED_BRIEF_FIELDS if not brief.get(f)]
    if missing:
        raise ValueError(f"Brief missing required fields: {missing}")


def _parse_count(val) -> int:
    s = str(val).strip()
    if "-" in s:
        return int(s.split("-")[-1])
    return int(s)


def _get_sdk_path() -> str:
    try:
        from config.settings_manager import settings_manager
        sdk = settings_manager.get_settings().get("renpy_sdk_path") or ""
        if sdk:
            return sdk
    except Exception:
        pass
    return os.environ.get("RENPY_SDK", "")


def _bg_file(bg_id: str) -> str:
    return (bg_id[3:] if bg_id.startswith("bg_") else bg_id) + ".png"


def _ensure_bg_prefix(loc_id: str) -> str:
    return loc_id if loc_id.startswith("bg_") else f"bg_{loc_id}"


def _call_json(prompt: str, label: str, max_tokens: int, attempts: int = 1) -> dict:
    for _ in range(attempts):
        agent = PipelineAgent(_SYSTEM, max_tokens=max_tokens)
        content = strip_fences(agent.send(prompt))
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            content = strip_fences(agent.send(
                "Invalid JSON. Return only the JSON object, no other text."
            ))
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
    raise RuntimeError(f"Failed to get valid JSON for {label}")


# ---------------------------------------------------------------------------
# Stage 1: Bible
# ---------------------------------------------------------------------------

def _build_bible_prompt(brief: Dict) -> str:
    return render_template(_PROMPTS_DIR / "bible.txt", {
        "genre":           brief.get("genre", ""),
        "tone":            brief.get("tone", ""),
        "setting":         brief.get("setting", ""),
        "notes":           brief.get("notes", ""),
        "character_count": _parse_count(brief.get("character_count", 4)),
    })


def generate_bible(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    brief = inputs.get("brief", {})
    _validate_brief(brief)
    prompt = _build_bible_prompt(brief)

    print("    [bible]  generating story bible")
    result = _call_json(prompt, "bible", max_tokens)

    chars = result.get("characters", [])
    for i, char in enumerate(chars):
        if not char.get("id") and char.get("name"):
            char["id"] = char["name"].lower().replace(" ", "_").replace("-", "_")
        char.setdefault("color", _CHARACTER_COLORS[i % len(_CHARACTER_COLORS)])

    return result


# ---------------------------------------------------------------------------
# Stage 2: Scene Plan
# ---------------------------------------------------------------------------

def _build_scene_plan_prompt(brief: Dict, bible: Dict) -> str:
    bible_slim = {
        "premise": bible.get("premise", ""),
        "tone": [d.get("adjective", "") for d in bible.get("tone_directives", [])],
        "setting": {
            "name":  bible.get("setting", {}).get("name", ""),
            "rules": bible.get("setting", {}).get("rules", ""),
        },
        "characters": [
            {"id": c["id"], "name": c["name"], "role": c.get("role", ""), "secret": c.get("secret", "")}
            for c in bible.get("characters", [])
        ],
    }
    return render_template(_PROMPTS_DIR / "scene_plan.txt", {
        "bible_slim":  bible_slim,
        "scene_count": _parse_count(brief.get("scene_count", "15")),
    })


def generate_scene_plan(inputs: Dict, working_dir: Path, max_tokens: int = 32000) -> Dict:
    brief  = inputs.get("brief", {})
    bible  = inputs.get("bible", {})
    prompt = _build_scene_plan_prompt(brief, bible)

    print("    [scene_plan]  planning scene structure")
    result = _call_json(prompt, "scene plan", max_tokens, attempts=3)

    scenes = result.get("scenes", [])
    for i, scene in enumerate(scenes):
        scene.setdefault("id", f"scene_{i + 1:03d}")
        scene.setdefault("choices", [])

    return {"scenes": scenes}


# ---------------------------------------------------------------------------
# Stage 3: Asset Manifest
# ---------------------------------------------------------------------------

def _build_asset_manifest_prompt(brief: Dict, bible: Dict, scene_plan: Dict) -> str:
    scenes   = scene_plan.get("scenes", [])
    setting  = bible.get("setting", {})
    location_ids = [
        loc for loc in dict.fromkeys(s.get("location_id", "") for s in scenes) if loc
    ]
    tone = ", ".join(d.get("adjective", "") for d in bible.get("tone_directives", []))

    location_scene_map: Dict[str, list] = {}
    for loc in location_ids:
        location_scene_map[loc] = [
            s.get("summary", "") for s in scenes if s.get("location_id") == loc and s.get("summary")
        ]
    location_list = "\n".join(
        f"- {loc} (id: {_ensure_bg_prefix(loc)})\n  " + "; ".join(summaries[:3])
        for loc, summaries in location_scene_map.items()
    )

    cg_scenes_text = "\n".join(
        f"- {s['id']}: {s.get('summary', '')} (beat: {s.get('emotional_beat', '')})"
        for s in scenes if s.get("has_cg")
    ) or "(none)"

    character_list = "\n".join(
        f"  {c['name']} ({c.get('role', '')}): {c.get('appearance', '')}"
        for c in bible.get("characters", [])
    )

    return render_template(_PROMPTS_DIR / "asset_manifest.txt", {
        "title":               brief.get("title", "Untitled"),
        "premise":             bible.get("premise", ""),
        "setting_name":        setting.get("name", ""),
        "setting_description": setting.get("physical_description", ""),
        "setting_rules":       setting.get("rules", ""),
        "setting_atmosphere":  setting.get("atmosphere", ""),
        "tone":                tone,
        "character_list":      character_list,
        "location_list":       location_list,
        "cg_scenes":           cg_scenes_text,
    })


def generate_asset_manifest(inputs: Dict, working_dir: Path, max_tokens: int = 8000) -> Dict:
    brief      = inputs.get("brief", {})
    bible      = inputs.get("bible", {})
    scene_plan = inputs.get("scene_plan", {})
    scenes     = scene_plan.get("scenes", [])
    setting    = bible.get("setting", {})

    location_ids = [
        loc for loc in dict.fromkeys(s.get("location_id", "") for s in scenes) if loc
    ]

    char_expressions: Dict[str, Dict] = {}
    music_cues: Dict[str, str] = {}
    for scene in scenes:
        sid  = scene["id"]
        beat = scene.get("emotional_beat", "tense")
        char_expressions[sid] = {c: _BEAT_TO_EXPRESSION.get(beat, "neutral") for c in scene.get("characters_present", [])}
        music_cues[sid]        = beat

    cg_scene_list = [s for s in scenes if s.get("has_cg")]
    prompt = _build_asset_manifest_prompt(brief, bible, scene_plan)

    print("    [asset_manifest]  generating art direction, backgrounds, and title card")
    bg_by_id:      Dict[str, Dict] = {}
    title_card:    Dict            = {}
    art_direction: Dict            = {}
    cg_results:    list            = []
    try:
        result        = _call_json(prompt, "asset manifest", max_tokens, attempts=3)
        bg_by_id      = {b["id"]: b for b in result.get("backgrounds", [])}
        title_card    = result.get("title_card", {})
        art_direction = result.get("art_direction", {})
        cg_results    = result.get("cgs", [])
    except RuntimeError:
        print("    [asset_manifest]  LLM failed, using code-derived descriptions")

    for loc_id in location_ids:
        bg_id = _ensure_bg_prefix(loc_id)
        if bg_id not in bg_by_id:
            bg_by_id[bg_id] = {
                "id":          bg_id,
                "name":        loc_id.replace("_", " ").title(),
                "description": (
                    f"{loc_id.replace('_', ' ').title()}. "
                    f"{setting.get('physical_description', '')} "
                    f"{setting.get('atmosphere', '')}"
                ).strip(),
            }

    if title_card:
        title_card["image_file"] = "title_card.png"
    else:
        char_desc = ", ".join(c.get("name", "") for c in bible.get("characters", [])[:3])
        title_card = {
            "image_file":  "title_card.png",
            "description": (
                f"Wide cinematic visual novel title card. "
                f"{setting.get('physical_description', '')} "
                f"Characters: {char_desc}. Mood: {tone_directives}. Atmospheric, detailed."
            ),
        }

    cg_desc_by_scene = {c["scene_id"]: c["description"] for c in cg_results if c.get("scene_id") and c.get("description")}
    cgs = [
        {
            "id":          f"cg_{s['id']}",
            "image_file":  f"cg_{s['id']}.png",
            "description": cg_desc_by_scene.get(s["id"], f"{s.get('summary', '')} — {s.get('emotional_beat', '')} moment"),
        }
        for s in cg_scene_list
    ][:4]

    return {
        "art_direction":         art_direction,
        "backgrounds":           list(bg_by_id.values()),
        "characters":            [
            {
                "id":         c["id"],
                "name":       c["name"],
                "appearance": c.get("appearance", ""),
                "color":      c.get("color", "#c8ffc8"),
                "image_file": f"{c['id']}.png",
            }
            for c in bible.get("characters", [])
        ],
        "character_expressions": char_expressions,
        "music_cues":            music_cues,
        "cgs":                   cgs,
        "title_card":            title_card,
    }


# ---------------------------------------------------------------------------
# Stage 4: Scene Scripts
# ---------------------------------------------------------------------------

def _build_scene_script_prompts(bible: Dict, scene_plan: Dict, manifest: Dict):
    """Yield (scene_id, system_prompt, user_prompt) for each scene."""
    scenes      = scene_plan.get("scenes", [])
    expressions = manifest.get("character_expressions", {})
    scene_ids   = [s["id"] for s in scenes]
    cg_scene_ids: set = (
        {s["id"] for s in scenes if s.get("has_cg")}
        or {cg["id"][3:] for cg in manifest.get("cgs", []) if cg.get("id", "").startswith("cg_")}
    )
    for i, scene in enumerate(scenes):
        sid              = scene["id"]
        chars_present    = scene.get("characters_present", [])
        bible_sum = _bible_summary(bible, chars_present)
        char_vars = _character_vars_block(bible, chars_present)
        next_sid      = scene_ids[i + 1] if i + 1 < len(scene_ids) else None
        loc_id        = scene.get("location_id", "")
        background_id = _ensure_bg_prefix(loc_id) if loc_id else ""

        prev_parts = []
        if i > 1:
            prev_parts.append(f"Two scenes ago: {scenes[i - 2].get('summary', '')}")
        if i > 0:
            prev_parts.append(f"Previous scene: {scenes[i - 1].get('summary', '')}")
        prev_summary = "\n".join(prev_parts) if prev_parts else "(story begins)"

        scene_ctx = {k: v for k, v in scene.items() if k not in ("purpose", "has_cg")}
        scene_ctx["scene_id"]      = sid
        scene_ctx["is_last_scene"] = (i + 1 == len(scenes))
        if next_sid and not scene.get("choices"):
            scene_ctx["next_scene"] = next_sid

        prompt = render_template(_PROMPTS_DIR / "scene_script.txt", {
            "bible_summary":  bible_sum,
            "scene":          scene_ctx,
            "scene_id":       sid,
            "background_id":  background_id,
            "expressions":    expressions.get(sid, {}),
            "prev_summary":   prev_summary,
            "character_vars": char_vars,
            "opening_hint":   (
                "Opening scene: begin with 2-4 narration lines establishing atmosphere and "
                "hooking the player before any characters appear or speak."
                if i == 0 else ""
            ),
            "cg_block": _build_cg_block(f"cg_{sid}", background_id) if sid in cg_scene_ids else "",
        })
        yield sid, _SCRIPT_SYSTEM, prompt


def write_scene_scripts(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    bible      = inputs.get("bible", {})
    scene_plan = inputs.get("scene_plan", {})
    manifest   = inputs.get("asset_manifest", {})
    scenes     = scene_plan.get("scenes", [])
    scene_ids  = [s["id"] for s in scenes]
    scripts: Dict[str, str] = {}

    for i, (sid, _, prompt) in enumerate(_build_scene_script_prompts(bible, scene_plan, manifest)):
        scene = scenes[i]
        print(f"    [scene_scripts]  {i + 1}/{len(scenes)}: {scene.get('title', sid)}")

        script = None
        agent  = PipelineAgent(_SCRIPT_SYSTEM, max_tokens=max_tokens)
        raw    = strip_fences(agent.send(prompt)).strip()

        valid, error = _validate_scene_script(sid, raw)
        if valid:
            script = raw
        else:
            for attempt in range(1, 3):
                print(f"    [scene_scripts]  {sid} retry {attempt}: {error}")
                raw = strip_fences(agent.send(
                    f"Error: {error}\n"
                    f"Rewrite the script. It must start with 'label {sid}:'. "
                    "Output only Ren'Py script."
                )).strip()
                valid, error = _validate_scene_script(sid, raw)
                if valid:
                    script = raw
                    break

        if script is None:
            print(f"    [scene_scripts]  {sid} using fallback")
            next_sid      = scene_ids[i + 1] if i + 1 < len(scene_ids) else None
            background_id = _ensure_bg_prefix(scene.get("location_id", ""))
            end_line      = f"    jump {next_sid}" if next_sid else '    "The End."\n    return'
            script = (
                f"label {sid}:\n"
                f"    scene {background_id} with dissolve\n"
                f'    "{scene.get("summary", "...")}"\n'
                f"{end_line}\n"
            )

        scripts[sid] = script

    return {"scripts": scripts, "scene_ids": scene_ids}


# ---------------------------------------------------------------------------
# Stage 5: Continuity Pass
# ---------------------------------------------------------------------------

def continuity_pass(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    bible        = inputs.get("bible", {})
    scripts_data = inputs.get("scene_scripts", {})
    scripts      = scripts_data.get("scripts", {})
    scene_ids    = scripts_data.get("scene_ids", list(scripts.keys()))
    bible_sum    = _bible_summary(bible)

    revised = dict(scripts)

    for char in bible.get("characters", []):
        char_id     = char["id"]
        char_name   = char["name"]
        char_scenes = _scenes_for_character(char_id, scripts)

        if not char_scenes:
            continue

        print(f"    [continuity]  checking {char_name} ({len(char_scenes)} scenes)")

        prompt = render_template(_PROMPTS_DIR / "continuity_check.txt", {
            "bible_summary":    bible_sum,
            "character_id":     char_id,
            "character_name":   char_name,
            "character_role":   char.get("role", ""),
            "character_secret": char.get("secret", ""),
            "character_speech": char.get("speech_pattern", ""),
            "character_scenes": "\n\n".join(
                f"--- {sid} ---\n{script}"
                for sid, script in char_scenes.items()
            ),
        })

        try:
            result = _call_json(prompt, f"continuity {char_id}", max_tokens)
        except RuntimeError:
            print(f"    [continuity]  {char_name}: check failed, skipping")
            continue

        for fix in result.get("corrections", []):
            sid      = fix.get("scene_id", "")
            original = fix.get("original_line", "")
            replaced = fix.get("corrected_line", "")
            if sid in revised and original and replaced and original in revised[sid]:
                revised[sid] = revised[sid].replace(original, replaced, 1)
            elif sid and original:
                print(f"    [continuity]  WARNING: could not match line in {sid}")

    return {"scripts": revised, "scene_ids": scene_ids}


# ---------------------------------------------------------------------------
# Stage 6a: Images
# ---------------------------------------------------------------------------

def generate_images(inputs: Dict, working_dir: Path) -> Dict:
    from tools.comfyui_tools import (
        build_character_job, build_background_job,
        build_cg_job, build_title_card_job,
        generate_images_batch,
    )
    from tools.execution_context import track_written_file

    bible    = inputs.get("bible", {})
    manifest = inputs.get("asset_manifest", {})

    images_dir = working_dir / "game_output" / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    bible_chars = {c["id"]: c for c in bible.get("characters", [])}
    job_meta: List[Dict] = []
    jobs:     List[Dict] = []

    for bg in manifest.get("backgrounds", []):
        bg_id   = bg["id"]
        bg_file = _bg_file(bg_id)
        bg["image_file"] = bg_file
        job_meta.append({"file": bg_file, "dest": images_dir / bg_file, "kind": "bg"})
        jobs.append(build_background_job(bg.get("description", bg.get("name", bg_id))))

    for char in manifest.get("characters", []):
        char_id  = char["id"]
        img_file = char.get("image_file", f"{char_id}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "char"})
        jobs.append(build_character_job(bible_chars.get(char_id, {})))

    for cg in manifest.get("cgs", []):
        cg_id    = cg["id"]
        img_file = cg.get("image_file", f"{cg_id}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "cg"})
        jobs.append(build_cg_job(cg.get("description", cg_id)))

    title_card = manifest.get("title_card", {})
    if title_card.get("description"):
        tc_file = title_card.get("image_file", "title_card.png")
        job_meta.append({"file": tc_file, "dest": images_dir / tc_file, "kind": "title_card"})
        jobs.append(build_title_card_job(title_card["description"]))

    print(f"    [images]  generating {len(jobs)} image(s)")
    results   = generate_images_batch(jobs)
    generated: List[str] = []
    failed:    List[Dict] = []

    for meta, result in zip(job_meta, results):
        filepath = meta["dest"]
        img_file = meta["file"]
        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], filepath)
            track_written_file(str(filepath))
            generated.append(img_file)
            print(f"    [images]  ok: {img_file}")
        else:
            error = result.get("error", "unknown")
            print(f"    [images]  failed ({error}), placeholder: {img_file}")
            kind = meta["kind"]
            if kind == "char":
                w, h, color = 512, 768, (92, 58, 92)
            elif kind in ("cg", "title_card"):
                w, h, color = 1280, 720, (40, 20, 60) if kind == "cg" else (20, 30, 60)
            else:
                w, h, color = 1280, 720, (58, 58, 92)
            write_solid_png(filepath, w, h, color)
            track_written_file(str(filepath))
            failed.append({"file": img_file, "error": error})

    return {"status": "ok", "generated": generated, "failed": failed}


# ---------------------------------------------------------------------------
# Stage 6b: Build
# ---------------------------------------------------------------------------

def build(inputs: Dict, working_dir: Path) -> Dict:
    from tools.execution_context import track_written_file

    brief        = inputs.get("brief", {})
    bible        = inputs.get("bible", {})
    manifest     = inputs.get("asset_manifest", {})
    scripts_data = inputs.get("scene_scripts_revised") or inputs.get("scene_scripts", {})
    scripts      = scripts_data.get("scripts", {})
    scene_ids    = scripts_data.get("scene_ids", list(scripts.keys()))

    title      = brief.get("title", "Untitled")
    output_dir = str(working_dir / "game_output")

    valid_backgrounds = {bg["id"] for bg in manifest.get("backgrounds", [])}
    valid_characters  = {c["id"] for c in manifest.get("characters", [])}
    valid_cgs         = {cg["id"] for cg in manifest.get("cgs", [])}
    valid_labels      = set(scene_ids) | {"start", "splashscreen", "main_menu"}

    _validate_and_repair(scripts, scene_ids, valid_labels, valid_backgrounds, valid_characters, valid_cgs)

    for sid in scene_ids:
        if scripts.get(sid):
            scripts[sid] = _postprocess_script(scripts[sid], valid_characters)

    full_script = _stitch_script(bible, manifest, scripts, scene_ids)

    game_dir = os.path.join(output_dir, "game")
    os.makedirs(game_dir, exist_ok=True)

    first_bg      = manifest.get("backgrounds", [{}])[0]
    first_bg_id   = first_bg.get("id", "")
    first_bg_file = first_bg.get("image_file", _bg_file(first_bg_id) if first_bg_id else "")
    title_card_file = manifest.get("title_card", {}).get("image_file", "")
    _write_options_rpy(game_dir, title, main_menu_bg_file=title_card_file or first_bg_file)

    script_path = os.path.join(game_dir, "script.rpy")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(full_script)
    track_written_file(script_path)
    _copy_templates(game_dir)

    # Overwrite placeholder main menu background with generated title card
    title_card_src = os.path.join(game_dir, "images", "title_card.png")
    gui_main_menu  = os.path.join(game_dir, "gui", "main_menu.png")
    if os.path.exists(title_card_src) and os.path.exists(os.path.dirname(gui_main_menu)):
        shutil.copy2(title_card_src, gui_main_menu)
        print("    [build]  title card → gui/main_menu.png")

    print(f"    [build]  project written to: {output_dir}")

    result   = {"project_dir": os.path.abspath(output_dir)}
    sdk_path = _get_sdk_path()
    if sdk_path:
        lint_summary = run_final_lint(output_dir, sdk_path)
        result["lint"] = lint_summary
        print(f"    [build]  final lint: {lint_summary['error_count']} error(s)")
        result.update(_distribute(output_dir, sdk_path))
    else:
        result["lint"] = {"error_count": None}

    return {"status": "built", "output_dir": output_dir, **result}
