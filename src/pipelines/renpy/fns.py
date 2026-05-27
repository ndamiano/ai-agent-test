import os
import re
import shutil
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Tuple

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, strip_fences

_CHARACTER_COLORS = [
    "#c8ffc8",
    "#c8c8ff",
    "#ffc8c8",
    "#ffe0a3",
    "#d8b4ff",
    "#a7f3d0",
]


def _json_with_correction(agent: PipelineAgent, prompt: str, label: str) -> dict:
    content = strip_fences(agent.send(prompt))
    for _ in range(2):
        try:
            import json as _json
            return _json.loads(content)
        except _json.JSONDecodeError:
            content = strip_fences(agent.send(
                "Invalid JSON. Return only the JSON object, no other text."
            ))
    raise RuntimeError(f"Failed to get valid JSON for {label}")


def _get_sdk_path() -> str:
    try:
        from config.settings_manager import settings_manager
        sdk = settings_manager.get_settings().get("renpy_sdk_path") or ""
        if sdk:
            return sdk
    except Exception:
        pass
    return os.environ.get("RENPY_SDK", "")

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)

_SCRIPT_SYSTEM = (
    "You are a Ren'Py script writer. Output only valid Ren'Py script. "
    "No JSON, no markdown fences, no explanation."
)

_REQUIRED_BRIEF_FIELDS = ("genre", "tone", "setting")

# Keys that are unique to the bible output (not in other stage outputs)
_BIBLE_KEYS = {"premise", "tone_directives", "setting", "themes"}


def _validate_brief(brief: Dict) -> None:
    missing = [f for f in _REQUIRED_BRIEF_FIELDS if not brief.get(f)]
    if missing:
        raise ValueError(f"Brief missing required fields: {missing}")


def _get_bible(inputs: Dict) -> Dict:
    """Get bible dict — handles both nested (pipeline runner) and flat (manual jq merge)."""
    b = inputs.get("bible", {})
    if not b:
        b = {k: inputs[k] for k in _BIBLE_KEYS if k in inputs}
        # characters in the flat merge may be overwritten by asset_manifest version;
        # bible chars have speech_pattern — use them if present, otherwise fall back
        chars = inputs.get("characters", [])
        if chars and isinstance(chars, list) and chars[0].get("speech_pattern") is not None:
            b["characters"] = chars
        elif "characters" in inputs:
            b["characters"] = inputs["characters"]
    return b


def _get_scene_plan(inputs: Dict) -> Dict:
    """Get scene_plan dict — handles nested and flat."""
    sp = inputs.get("scene_plan", {})
    if not sp and "scenes" in inputs:
        sp = {"scenes": inputs["scenes"]}
    return sp


def _get_manifest(inputs: Dict) -> Dict:
    """Get asset_manifest dict — handles nested and flat."""
    m = inputs.get("asset_manifest", {})
    if not m and "backgrounds" in inputs:
        m = {k: inputs[k] for k in ("backgrounds", "character_expressions", "music_cues") if k in inputs}
        # Reconstruct manifest characters from bible when flat-merged
        if "characters" not in m:
            bible = _get_bible(inputs)
            m["characters"] = [
                {
                    "id":         c["id"],
                    "name":       c["name"],
                    "appearance": c.get("appearance", ""),
                    "color":      c.get("color", "#c8ffc8"),
                    "image_file": f"{c['id']}.png",
                }
                for c in bible.get("characters", [])
            ]
    return m


def _get_scene_scripts(inputs: Dict) -> Dict:
    """Get scene_scripts dict — handles nested and flat."""
    ss = inputs.get("scene_scripts", {})
    if not ss and "scripts" in inputs:
        ss = {"scripts": inputs["scripts"], "scene_ids": inputs.get("scene_ids", [])}
    return ss


def _parse_count(val) -> int:
    s = str(val).strip()
    if "-" in s:
        return int(s.split("-")[-1])
    return int(s)


# ---------------------------------------------------------------------------
# Stage 1: Bible
# ---------------------------------------------------------------------------

def generate_bible(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    brief           = inputs.get("brief", {})
    _validate_brief(brief)
    character_count = _parse_count(brief.get("character_count", 4))

    prompt = render_template(_PROMPTS_DIR / "bible.txt", {
        "genre":           brief.get("genre", ""),
        "tone":            brief.get("tone", ""),
        "setting":         brief.get("setting", ""),
        "notes":           brief.get("notes", ""),
        "character_count": character_count,
    })

    print("    [bible]  generating story bible")
    agent  = PipelineAgent(_SYSTEM, max_tokens=max_tokens)
    result = _json_with_correction(agent, prompt, "bible")

    chars = result.get("characters", [])
    for i, char in enumerate(chars):
        if not char.get("id") and char.get("name"):
            char["id"] = char["name"].lower().replace(" ", "_").replace("-", "_")
        char.setdefault("color", _CHARACTER_COLORS[i % len(_CHARACTER_COLORS)])

    return result


# ---------------------------------------------------------------------------
# Stage 2: Scene Plan
# ---------------------------------------------------------------------------

def generate_scene_plan(inputs: Dict, working_dir: Path, max_tokens: int = 32000) -> Dict:
    brief       = inputs.get("brief", {})
    bible       = _get_bible(inputs)
    scene_count = _parse_count(brief.get("scene_count", "15"))

    prompt = render_template(_PROMPTS_DIR / "scene_plan.txt", {
        "bible":       bible,
        "scene_count": scene_count,
    })

    print("    [scene_plan]  planning scene structure")
    for attempt in range(1, 4):
        try:
            agent  = PipelineAgent(_SYSTEM, max_tokens=max_tokens)
            result = _json_with_correction(agent, prompt, "scene plan")
            break
        except RuntimeError:
            if attempt == 3:
                raise
            print(f"    [scene_plan]  retrying (attempt {attempt + 1}/3)")

    scenes = result.get("scenes", [])
    for i, scene in enumerate(scenes):
        scene.setdefault("id", f"scene_{i + 1:03d}")
        scene.setdefault("choices", [])

    return {"scenes": scenes}


# ---------------------------------------------------------------------------
# Stage 3: Asset Manifest
# ---------------------------------------------------------------------------

_BEAT_TO_EXPRESSION = {
    "tense":           "worried",
    "confrontational": "angry",
    "melancholic":     "sad",
    "revelatory":      "surprised",
    "hopeful":         "happy",
    "tender":          "happy",
    "ominous":         "scared",
}


def generate_asset_manifest(inputs: Dict, working_dir: Path, max_tokens: int = 8000) -> Dict:
    bible      = _get_bible(inputs)
    scene_plan = _get_scene_plan(inputs)
    scenes     = scene_plan.get("scenes", [])
    setting    = bible.get("setting", {})

    # Code: collect ordered unique location_ids
    seen_locs: set = set()
    location_ids: List[str] = []
    for scene in scenes:
        loc = scene.get("location_id", "")
        if loc and loc not in seen_locs:
            location_ids.append(loc)
            seen_locs.add(loc)

    # Code: derive character expressions + music from emotional_beat
    char_expressions: Dict[str, Dict] = {}
    music_cues: Dict[str, str] = {}
    for scene in scenes:
        sid  = scene["id"]
        beat = scene.get("emotional_beat", "tense")
        expr = _BEAT_TO_EXPRESSION.get(beat, "neutral")
        char_expressions[sid] = {c: expr for c in scene.get("characters_present", [])}
        music_cues[sid]        = beat

    # LLM: focused call — background descriptions only (small, reliable output)
    location_list = "\n".join(
        f"- {loc}: {'bg_' + loc if not loc.startswith('bg_') else loc}"
        for loc in location_ids
    )
    tone_directives = ", ".join(d.get("adjective", "") for d in bible.get("tone_directives", []))

    print("    [asset_manifest]  generating background descriptions")
    prompt = render_template(_PROMPTS_DIR / "asset_manifest.txt", {
        "setting_name":        setting.get("name", ""),
        "setting_description": setting.get("physical_description", ""),
        "setting_atmosphere":  setting.get("atmosphere", ""),
        "tone":                tone_directives,
        "location_list":       location_list,
    })

    bg_by_id: Dict[str, Dict] = {}
    for attempt in range(1, 4):
        try:
            agent  = PipelineAgent(_SYSTEM, max_tokens=max_tokens)
            result = _json_with_correction(agent, prompt, "asset manifest")
            bg_by_id = {b["id"]: b for b in result.get("backgrounds", [])}
            break
        except RuntimeError:
            if attempt == 3:
                print("    [asset_manifest]  LLM failed, using code-derived descriptions")
            else:
                print(f"    [asset_manifest]  retrying (attempt {attempt + 1}/3)")

    # Fill any missing locations
    for loc_id in location_ids:
        bg_id = f"bg_{loc_id}" if not loc_id.startswith("bg_") else loc_id
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

    return {
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
    }


# ---------------------------------------------------------------------------
# Stage 4: Scene Scripts
# ---------------------------------------------------------------------------

def _bible_summary(bible: Dict) -> str:
    chars = bible.get("characters", [])
    char_lines = "\n".join(
        f"  {c['name']} (id={c['id']}, {c.get('role', '')}): "
        f"speech={c.get('speech_pattern', '')} | secret={c.get('secret', '')}"
        for c in chars
    )
    setting = bible.get("setting", {})
    tone    = ", ".join(d.get("adjective", "") for d in bible.get("tone_directives", []))
    return (
        f"Premise: {bible.get('premise', '')}\n"
        f"Tone: {tone}\n"
        f"Setting: {setting.get('name', '')} — {setting.get('physical_description', '')}\n"
        f"Characters:\n{char_lines}"
    )


def _character_vars_block(bible: Dict) -> str:
    return "\n".join(
        f"  {c['id']} — {c['name']} ({c.get('role', '')})"
        for c in bible.get("characters", [])
    )


def _validate_scene_script(scene_id: str, script: str) -> Tuple[bool, str]:
    if not re.search(rf'\blabel\s+{re.escape(scene_id)}\s*:', script):
        return False, f"Missing 'label {scene_id}:'"
    if len(script.strip()) < 30:
        return False, "Script too short"
    return True, ""


def write_scene_scripts(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    bible      = _get_bible(inputs)
    scene_plan = _get_scene_plan(inputs)
    manifest   = _get_manifest(inputs)

    scenes      = scene_plan.get("scenes", [])
    expressions = manifest.get("character_expressions", {})
    scene_ids   = [s["id"] for s in scenes]

    bible_sum = _bible_summary(bible)
    char_vars = _character_vars_block(bible)

    scripts: Dict[str, str] = {}

    for i, scene in enumerate(scenes):
        sid   = scene["id"]
        title = scene.get("title", sid)
        print(f"    [scene_scripts]  {i + 1}/{len(scenes)}: {title}")

        prev_parts = []
        if i > 1:
            prev_parts.append(f"Two scenes ago: {scenes[i - 2].get('summary', '')}")
        if i > 0:
            prev_parts.append(f"Previous scene: {scenes[i - 1].get('summary', '')}")
        prev_summary = "\n".join(prev_parts) if prev_parts else "(story begins)"

        next_sid = scene_ids[i + 1] if i + 1 < len(scene_ids) else None

        scene_ctx = dict(scene)
        scene_ctx["scene_id"]     = sid
        scene_ctx["is_last_scene"] = (i + 1 == len(scenes))
        if next_sid and not scene.get("choices"):
            scene_ctx["next_scene"] = next_sid

        loc_id       = scene.get("location_id", "")
        background_id = f"bg_{loc_id}" if loc_id and not loc_id.startswith("bg_") else loc_id

        opening_hint = (
            "Opening scene: begin with 2-4 narration lines establishing atmosphere and "
            "hooking the player before any characters appear or speak."
            if i == 0 else ""
        )

        prompt = render_template(_PROMPTS_DIR / "scene_script.txt", {
            "bible_summary":  bible_sum,
            "scene":          scene_ctx,
            "scene_id":       sid,
            "background_id":  background_id,
            "expressions":    expressions.get(sid, {}),
            "prev_summary":   prev_summary,
            "character_vars": char_vars,
            "opening_hint":   opening_hint,
        })

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
            if next_sid:
                end_line = f"    jump {next_sid}"
            else:
                end_line = '    "The End."\n    return'
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

def _scenes_for_character(char_id: str, scripts: Dict[str, str]) -> List[Dict]:
    pattern = re.compile(rf'^\s*{re.escape(char_id)}\s+"', re.MULTILINE)
    return [
        {"scene_id": sid, "script": script}
        for sid, script in scripts.items()
        if pattern.search(script)
    ]


def continuity_pass(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    bible        = _get_bible(inputs)
    scripts_data = _get_scene_scripts(inputs)
    scripts      = scripts_data.get("scripts", {})
    scene_ids    = scripts_data.get("scene_ids", list(scripts.keys()))
    bible_sum    = _bible_summary(bible)

    revised = dict(scripts)

    for char in bible.get("characters", []):
        char_id    = char["id"]
        char_name  = char["name"]
        char_scenes = _scenes_for_character(char_id, scripts)

        if not char_scenes:
            continue

        print(f"    [continuity]  checking {char_name} ({len(char_scenes)} scenes)")

        scenes_text = "\n\n".join(
            f"--- {s['scene_id']} ---\n{s['script']}"
            for s in char_scenes
        )

        prompt = render_template(_PROMPTS_DIR / "continuity_check.txt", {
            "bible_summary":    bible_sum,
            "character_id":     char_id,
            "character_name":   char_name,
            "character_role":   char.get("role", ""),
            "character_secret": char.get("secret", ""),
            "character_speech": char.get("speech_pattern", ""),
            "character_scenes": scenes_text,
        })

        try:
            agent  = PipelineAgent(_SYSTEM, max_tokens=max_tokens)
            result = _json_with_correction(agent, prompt, f"continuity {char_id}")
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
    from tools.comfyui_tools import build_character_job, build_background_job, generate_images_batch
    from tools.execution_context import track_written_file

    bible    = _get_bible(inputs)
    manifest = _get_manifest(inputs)

    output_dir = working_dir / "game_output"
    images_dir = output_dir / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    bible_chars = {c["id"]: c for c in bible.get("characters", [])}

    # Build job list with destination metadata, then generate all at once
    # (single LLM unload/reload for the entire batch)
    job_meta: List[Dict] = []
    jobs:     List[Dict] = []

    for bg in manifest.get("backgrounds", []):
        bg_id   = bg["id"]
        bg_file = bg_id[3:] + ".png" if bg_id.startswith("bg_") else bg_id + ".png"
        bg["image_file"] = bg_file
        job_meta.append({"file": bg_file, "dest": images_dir / bg_file, "kind": "bg"})
        jobs.append(build_background_job(bg.get("description", bg.get("name", bg_id))))

    for char in manifest.get("characters", []):
        char_id  = char["id"]
        img_file = char.get("image_file", f"{char_id}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "char"})
        jobs.append(build_character_job(bible_chars.get(char_id, {})))

    print(f"    [images]  generating {len(jobs)} image(s)")
    results  = generate_images_batch(jobs)
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
            w, h = (1280, 720) if meta["kind"] == "bg" else (512, 768)
            color = (58, 58, 92) if meta["kind"] == "bg" else (92, 58, 92)
            _write_solid_png(filepath, w, h, color)
            track_written_file(str(filepath))
            failed.append({"file": img_file, "error": error})

    return {"status": "ok", "generated": generated, "failed": failed}


# ---------------------------------------------------------------------------
# Stage 6b: Build
# ---------------------------------------------------------------------------

def build(inputs: Dict, working_dir: Path) -> Dict:
    from tools.execution_context import track_written_file

    brief        = inputs.get("brief", {})
    bible        = _get_bible(inputs)
    manifest     = _get_manifest(inputs)
    scripts_data = inputs.get("scene_scripts_revised") or _get_scene_scripts(inputs)
    scripts      = scripts_data.get("scripts", {})
    scene_ids    = scripts_data.get("scene_ids", list(scripts.keys()))

    title      = brief.get("title", "Untitled")
    output_dir = str(working_dir / "game_output")

    valid_backgrounds = {bg["id"] for bg in manifest.get("backgrounds", [])}
    valid_characters  = {c["id"] for c in manifest.get("characters", [])}
    valid_labels      = set(scene_ids) | {"start", "splashscreen", "main_menu"}

    # Validate and repair
    for sid in scene_ids:
        script = scripts.get(sid, "")
        if not script:
            continue
        issues = _find_script_issues(script, valid_labels, valid_backgrounds, valid_characters)
        if issues:
            print(f"    [build]  repairing {sid}: {issues}")
            scripts[sid] = _repair_broken_scene(
                sid, script, issues, valid_labels, valid_backgrounds, valid_characters
            )

    for sid in scene_ids:
        if scripts.get(sid):
            scripts[sid] = _inject_speaker_highlighting(scripts[sid], valid_characters)

    full_script = _stitch_script(title, bible, manifest, scripts, scene_ids)

    game_dir   = os.path.join(output_dir, "game")
    images_dir = os.path.join(game_dir, "images")
    os.makedirs(game_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)

    _write_options_rpy(game_dir, title)
    script_path = os.path.join(game_dir, "script.rpy")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(full_script)
    track_written_file(script_path)

    _copy_templates(game_dir)

    print(f"    [build]  project written to: {output_dir}")

    result   = {"project_dir": os.path.abspath(output_dir)}
    sdk_path = _get_sdk_path()
    if sdk_path:
        lint_output = _run_renpy_lint(output_dir, sdk_path)
        if lint_output:
            lint_errors = _parse_lint_errors(lint_output)
            if lint_errors:
                print(f"    [build]  lint: {len(lint_errors)} issue(s) — attempting repair")
                # Group errors by scene
                errors_by_scene: Dict[str, List[str]] = {}
                for lineno, context in lint_errors:
                    sid = _scene_id_at_line(full_script, lineno)
                    if sid and sid in scripts:
                        errors_by_scene.setdefault(sid, []).append(context)
                if errors_by_scene:
                    for sid, errs in errors_by_scene.items():
                        issue = "; ".join(errs[:5])
                        print(f"    [build]  lint repair: {sid}")
                        scripts[sid] = _repair_broken_scene(
                            sid, scripts[sid], issue,
                            valid_labels, valid_backgrounds, valid_characters,
                        )
                    full_script = _stitch_script(title, bible, manifest, scripts, scene_ids)
                    with open(script_path, "w", encoding="utf-8") as f:
                        f.write(full_script)
                    track_written_file(script_path)
                    print(f"    [build]  rebuilt after lint repair")

        import sys
        builder_dir = str(Path(__file__).parent)
        if builder_dir not in sys.path:
            sys.path.insert(0, builder_dir)
        from renpy_builder import _distribute  # noqa: PLC0415
        result.update(_distribute(output_dir, sdk_path))

    return {"status": "built", "output_dir": output_dir, **result}


def _run_renpy_lint(output_dir: str, sdk_path: str) -> str:
    """Run renpy lint; return raw output text. Empty string if SDK unavailable."""
    import platform
    import subprocess
    sdk_path = os.path.abspath(sdk_path)
    renpy_bin = os.path.join(sdk_path, "renpy.exe" if platform.system() == "Windows" else "renpy.sh")
    if not os.path.exists(renpy_bin):
        return ""
    try:
        proc = subprocess.run(
            [renpy_bin, os.path.abspath(output_dir), "lint"],
            capture_output=True, text=True, timeout=120,
        )
        return proc.stdout + proc.stderr
    except Exception as e:
        print(f"    [build]  lint error: {e}")
        return ""


def _parse_lint_errors(lint_output: str) -> List[Tuple[int, str]]:
    """Extract (line_number, error_message) pairs from renpy lint output."""
    results = []
    lines   = lint_output.splitlines()
    for i, line in enumerate(lines):
        m = re.search(r'[Ll]ine\s+(\d+)', line)
        if m:
            lineno  = int(m.group(1))
            context = " ".join(lines[i:i + 3]).strip()
            results.append((lineno, context))
    return results


def _scene_id_at_line(stitched_script: str, target_lineno: int) -> str | None:
    """Return the scene label that contains target_lineno in the stitched script."""
    current = None
    for lineno, line in enumerate(stitched_script.splitlines(), 1):
        m = re.match(r'^label\s+(\w+)\s*:', line)
        if m:
            current = m.group(1)
        if lineno == target_lineno:
            return current
    return current


def _find_script_issues(
    script: str,
    valid_labels: set,
    valid_backgrounds: set,
    valid_characters: set,
) -> str:
    parts = []
    broken_jumps = set(re.findall(r'\bjump\s+(\w+)', script)) - valid_labels
    broken_bgs   = set(re.findall(r'\bscene\s+(bg_\w+)', script)) - valid_backgrounds - {"black"}
    broken_chars = set(re.findall(r'\bshow\s+(\w+)', script)) - valid_characters
    if broken_jumps:
        parts.append(f"unknown jump targets: {sorted(broken_jumps)}")
    if broken_bgs:
        parts.append(f"unknown backgrounds: {sorted(broken_bgs)}")
    if broken_chars:
        parts.append(f"unknown characters: {sorted(broken_chars)}")
    return "; ".join(parts)


def _repair_broken_scene(
    scene_id: str,
    script: str,
    issue: str,
    valid_labels: set,
    valid_backgrounds: set,
    valid_characters: set,
) -> str:
    prompt = render_template(_PROMPTS_DIR / "bridge.txt", {
        "scene_id":              scene_id,
        "issue":                 issue,
        "script":                script,
        "available_labels":      sorted(valid_labels),
        "available_backgrounds": sorted(valid_backgrounds),
        "available_characters":  sorted(valid_characters),
    })
    agent = PipelineAgent(_SCRIPT_SYSTEM, max_tokens=25000)
    raw   = strip_fences(agent.send(prompt)).strip()
    return raw if raw else script


def _inject_speaker_highlighting(script: str, valid_characters: set) -> str:
    """Dim non-speaking characters and brighten the speaker on each dialogue line."""
    result = []
    shown = {}  # cid -> last position string

    for line in script.split("\n"):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        show_m = re.match(r'show\s+(\w+)(?:\s+at\s+(\w+))?', stripped)
        if show_m:
            cid, pos = show_m.group(1), show_m.group(2) or "center"
            if cid in valid_characters:
                shown[cid] = pos
            result.append(line)
            continue

        hide_m = re.match(r'hide\s+(\w+)', stripped)
        if hide_m:
            shown.pop(hide_m.group(1), None)
            result.append(line)
            continue

        if stripped.startswith("scene "):
            shown.clear()
            result.append(line)
            continue

        dialogue_m = re.match(r'(\w+)\s+"', stripped)
        if dialogue_m and len(shown) > 1:
            speaker = dialogue_m.group(1)
            if speaker in valid_characters and speaker in shown:
                for cid, pos in shown.items():
                    t = "speaking" if cid == speaker else "not_speaking"
                    result.append(f"{indent}show {cid} at {pos}, {t}")

        result.append(line)

    return "\n".join(result)


def _stitch_script(
    title: str,
    bible: Dict,
    manifest: Dict,
    scripts: Dict[str, str],
    scene_ids: List[str],
) -> str:
    lines = []
    chars = bible.get("characters", [])

    lines.append("## Characters")
    for char in chars:
        cid   = char["id"]
        name  = char["name"]
        color = char.get("color", "#ffffff")
        lines.append(f'define {cid} = Character("{name}", color="{color}")')
    lines.append("")

    lines.append("## Images")
    bgs = manifest.get("backgrounds", [])
    for bg in bgs:
        bg_id   = bg["id"]
        bg_file = bg.get("image_file", bg_id[3:] + ".png" if bg_id.startswith("bg_") else bg_id + ".png")
        lines.append(f'image {bg_id} = "images/{bg_file}"')
    for char in manifest.get("characters", []):
        cid      = char["id"]
        img_file = char.get("image_file", f"{cid}.png")
        lines.append(f'image {cid}:')
        lines.append(f'    "images/{img_file}"')
        lines.append('    zoom 0.55')
    lines.append("")

    if bgs:
        first_bg = bgs[0]
        first_bg_id   = first_bg["id"]
        first_bg_file = first_bg.get("image_file", first_bg_id[3:] + ".png" if first_bg_id.startswith("bg_") else first_bg_id + ".png")
        lines.append(f'define gui.main_menu_background = "images/{first_bg_file}"')
        lines.append("")

    lines.append("## Override built-in positions so sprites sit at screen bottom with padding")
    lines.append("transform left:")
    lines.append("    xalign 0.15 yalign 1.0")
    lines.append("transform center:")
    lines.append("    xalign 0.5 yalign 1.0")
    lines.append("transform right:")
    lines.append("    xalign 0.85 yalign 1.0")
    lines.append("transform speaking:")
    lines.append("    alpha 1.0")
    lines.append("transform not_speaking:")
    lines.append("    alpha 0.5")
    lines.append("")

    lines.append("label splashscreen:")
    lines.append("    return")
    lines.append("")

    first = scene_ids[0] if scene_ids else "scene_001"
    lines.append("label start:")
    lines.append(f"    jump {first}")
    lines.append("")

    for sid in scene_ids:
        script = scripts.get(sid, "")
        if script:
            lines.append(script)
            if not script.endswith("\n"):
                lines.append("")
            lines.append("")

    return "\n".join(lines)


def _write_options_rpy(game_dir: str, title: str):
    safe = re.sub(r"[^A-Za-z0-9_]", "", title.replace(" ", "_")) or "UntitledGame"
    content = (
        f'define config.name = "{title}"\n'
        f'define config.version = "1.0"\n'
        f'define config.window_icon = None\n'
        f'define gui.show_name = True\n'
        f'define config.save_directory = "{safe}"\n'
        f'init python:\n'
        f'    build.name = "{safe}"\n'
        f'    build.executable_name = "{safe}"\n'
        f'    build.directory_name = "{safe}-1.0"\n'
    )
    with open(os.path.join(game_dir, "options.rpy"), "w", encoding="utf-8") as f:
        f.write(content)


def _copy_templates(game_dir: str):
    templates = Path(__file__).parent / "renpy_templates"
    for filename in ["screens.rpy", "gui.rpy"]:
        src = templates / filename
        dst = os.path.join(game_dir, filename)
        if src.exists():
            shutil.copy2(str(src), dst)
    gui_src = templates / "gui"
    gui_dst = os.path.join(game_dir, "gui")
    if gui_src.is_dir():
        if os.path.exists(gui_dst):
            shutil.rmtree(gui_dst)
        shutil.copytree(str(gui_src), gui_dst)


def _write_solid_png(path: Path, width: int, height: int, rgb: tuple):
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row  = b"\x00" + bytes(rgb) * width
    idat = zlib.compress(row * height, level=1)
    png  = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
