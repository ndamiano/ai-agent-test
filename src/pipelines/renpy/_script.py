import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, strip_fences

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_SCRIPT_SYSTEM = (
    "You are a Ren'Py script writer. Output only valid Ren'Py script. "
    "No JSON, no markdown fences, no explanation."
)


# ---------------------------------------------------------------------------
# Scene writing helpers
# ---------------------------------------------------------------------------

def _bible_summary(bible: Dict, characters_present: list | None = None) -> str:
    chars = bible.get("characters", [])
    if characters_present:
        chars = [c for c in chars if c.get("id") in characters_present]
    char_lines = "\n".join(
        f"  {c['name']} (id={c['id']}, {c.get('role', '')}): "
        f"verbal_tic={c.get('verbal_tic') or c.get('speech_pattern', '')} | "
        f"forbidden_word={c.get('forbidden_word', '')} | "
        f"nonverbal={c.get('nonverbal', '')} | "
        f"secret={c.get('secret', '')}"
        for c in chars
    )
    setting = bible.get("setting", {})
    tone_parts = [
        f"{d.get('adjective', '')} ({d.get('explanation', '')})"
        for d in bible.get("tone_directives", [])
        if d.get("adjective")
    ]
    tone = "; ".join(tone_parts)
    return (
        f"Premise: {bible.get('premise', '')}\n"
        f"Tone: {tone}\n"
        f"Setting: {setting.get('name', '')} — {setting.get('physical_description', '')}\n"
        f"Characters:\n{char_lines}"
    )


def _character_vars_block(bible: Dict, characters_present: list | None = None) -> str:
    chars = bible.get("characters", [])
    if characters_present:
        chars = [c for c in chars if c.get("id") in characters_present]
    return "\n".join(
        f"  {c['id']} — {c['name']} ({c.get('role', '')})"
        for c in chars
    )


def _build_cg_block(cg_id: str, background_id: str) -> str:
    return (
        f"\n## CG Illustration\n"
        f"This scene has a full-screen CG. Place it at the emotional peak.\n"
        f"The 'scene' command clears all sprites automatically — no need to hide them before the CG.\n"
        f"Sequence: scene {cg_id} with dissolve, then 1-3 climactic lines, "
        f"then scene {background_id} with dissolve, then re-show characters with show/at before continuing.\n"
    )


def _validate_scene_script(scene_id: str, script: str) -> Tuple[bool, str]:
    if not re.search(rf'\blabel\s+{re.escape(scene_id)}\s*:', script):
        return False, f"Missing 'label {scene_id}:'"
    if len(script.strip()) < 30:
        return False, "Script too short"
    return True, ""


def _scenes_for_character(char_id: str, scripts: Dict[str, str]) -> Dict[str, str]:
    pattern = re.compile(rf'^\s*{re.escape(char_id)}\s+"', re.MULTILINE)
    return {sid: script for sid, script in scripts.items() if pattern.search(script)}


# ---------------------------------------------------------------------------
# Script post-processing
# ---------------------------------------------------------------------------

def _postprocess_script(script: str, valid_characters: set) -> str:
    """Single pass: strip Narrator speaker, fix indentation, inject speaker highlighting."""
    lines = script.split("\n")
    result = []
    shown: dict = {}
    prev_expects_block = False
    prev_indent = 0

    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            result.append(line)
            continue

        # Strip erroneous Narrator speaker
        if re.match(r'Narrator\s+"', stripped):
            line = re.sub(r'^(\s*)Narrator\s+(")', r'\1\2', line)
            stripped = line.lstrip()

        # Collapse over-indentation
        indent = len(line) - len(stripped)
        if not prev_expects_block and indent > prev_indent and prev_indent > 0:
            line = " " * prev_indent + stripped
            indent = prev_indent

        # Track show/hide/scene; inject speaking transforms before dialogue
        show_m = re.match(r'show\s+(\w+)(?:\s+at\s+(\w+))?', stripped)
        hide_m = re.match(r'hide\s+(\w+)', stripped)
        if show_m:
            cid, pos = show_m.group(1), show_m.group(2) or "center"
            if cid in valid_characters:
                shown[cid] = pos
        elif hide_m:
            shown.pop(hide_m.group(1), None)
        elif stripped.startswith("scene "):
            shown.clear()
        else:
            dialogue_m = re.match(r'(\w+)\s+"', stripped)
            if dialogue_m and len(shown) > 1:
                speaker = dialogue_m.group(1)
                if speaker in valid_characters and speaker in shown:
                    pad = line[: len(line) - len(stripped)]
                    for cid, pos in shown.items():
                        t = "speaking" if cid == speaker else "not_speaking"
                        result.append(f"{pad}show {cid} at {pos}, {t}")

        result.append(line)
        prev_expects_block = stripped.rstrip().endswith(":")
        prev_indent = indent

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Script validation and repair
# ---------------------------------------------------------------------------

def _find_script_issues(
    script: str,
    valid_labels: Set[str],
    valid_backgrounds: Set[str],
    valid_characters: Set[str],
    valid_cgs: Set[str] = None,
) -> str:
    valid_scenes = valid_backgrounds | (valid_cgs or set()) | {"black"}
    parts = []
    broken_jumps = set(re.findall(r'\bjump\s+(\w+)', script)) - valid_labels
    broken_bgs   = set(re.findall(r'\bscene\s+(bg_\w+)', script)) - valid_scenes
    broken_cgs   = set(re.findall(r'\bscene\s+(cg_\w+)', script)) - valid_scenes
    broken_chars = set(re.findall(r'\bshow\s+(\w+)', script)) - valid_characters
    if broken_jumps:
        parts.append(f"unknown jump targets: {sorted(broken_jumps)}")
    if broken_bgs:
        parts.append(f"unknown backgrounds: {sorted(broken_bgs)}")
    if broken_cgs:
        parts.append(f"unknown CG ids: {sorted(broken_cgs)}")
    if broken_chars:
        parts.append(f"unknown characters: {sorted(broken_chars)}")
    return "; ".join(parts)


def _repair_broken_scene(
    scene_id: str,
    script: str,
    issue: str,
    valid_labels: Set[str],
    valid_backgrounds: Set[str],
    valid_characters: Set[str],
    valid_cgs: Set[str] = None,
) -> str:
    valid_scenes = sorted(valid_backgrounds | (valid_cgs or set()))
    prompt = render_template(_PROMPTS_DIR / "bridge.txt", {
        "scene_id":              scene_id,
        "issue":                 issue,
        "script":                script,
        "available_labels":      sorted(valid_labels),
        "available_backgrounds": valid_scenes,
        "available_characters":  sorted(valid_characters),
    })
    agent = PipelineAgent(_SCRIPT_SYSTEM, max_tokens=25000)
    raw   = strip_fences(agent.send(prompt)).strip()
    return raw if raw else script


# ---------------------------------------------------------------------------
# Script assembly
# ---------------------------------------------------------------------------

def _stitch_script(
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
    for cg in manifest.get("cgs", []):
        cg_id    = cg["id"]
        img_file = cg.get("image_file", f"{cg_id}.png")
        lines.append(f'image {cg_id} = "images/{img_file}"')
    for char in manifest.get("characters", []):
        cid      = char["id"]
        img_file = char.get("image_file", f"{cid}.png")
        lines.append(f'image {cid}:')
        lines.append(f'    "images/{img_file}"')
        lines.append('    zoom 0.55')
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


def _write_options_rpy(game_dir: str, title: str, main_menu_bg_file: str = "") -> None:
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
    if main_menu_bg_file:
        content += f'define gui.main_menu_background = "images/{main_menu_bg_file}"\n'
    with open(os.path.join(game_dir, "options.rpy"), "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------

def _validate_and_repair(
    scripts: Dict[str, str],
    scene_ids: List[str],
    valid_labels: Set[str],
    valid_backgrounds: Set[str],
    valid_characters: Set[str],
    valid_cgs: Set[str],
) -> None:
    for sid in scene_ids:
        script = scripts.get(sid, "")
        if not script:
            continue
        issues = _find_script_issues(script, valid_labels, valid_backgrounds, valid_characters, valid_cgs)
        if issues:
            print(f"    [build]  repairing {sid}: {issues}")
            scripts[sid] = _repair_broken_scene(
                sid, script, issues, valid_labels, valid_backgrounds, valid_characters, valid_cgs
            )


def _lint_and_repair(
    output_dir: str,
    full_script: str,
    scripts: Dict[str, str],
    scene_ids: List[str],
    bible: Dict,
    manifest: Dict,
    valid_labels: Set[str],
    valid_backgrounds: Set[str],
    valid_characters: Set[str],
    valid_cgs: Set[str],
    sdk_path: str,
) -> str | None:
    """Run lint; repair scenes with errors. Returns updated stitched script, or None if no repairs."""
    lint_output = _run_renpy_lint(output_dir, sdk_path)
    if not lint_output:
        return None
    lint_errors = _parse_lint_errors(lint_output)
    if not lint_errors:
        return None

    print(f"    [build]  lint: {len(lint_errors)} issue(s) — attempting repair")
    errors_by_scene: Dict[str, List[str]] = {}
    for lineno, context in lint_errors:
        sid = _scene_id_at_line(full_script, lineno)
        if sid and sid in scripts:
            errors_by_scene.setdefault(sid, []).append(context)
    if not errors_by_scene:
        return None

    for sid, errs in errors_by_scene.items():
        issue = "; ".join(errs[:5])
        print(f"    [build]  lint repair: {sid}")
        scripts[sid] = _repair_broken_scene(
            sid, scripts[sid], issue,
            valid_labels, valid_backgrounds, valid_characters, valid_cgs,
        )
    return _stitch_script(bible, manifest, scripts, scene_ids)


# ---------------------------------------------------------------------------
# Lint helpers
# ---------------------------------------------------------------------------

def _run_renpy_lint(output_dir: str, sdk_path: str) -> str:
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
    results = []
    lines   = lint_output.splitlines()
    for i, line in enumerate(lines):
        m = re.search(r'\.rpy:(\d+)|[Ll]ine\s+(\d+)', line)
        if m:
            lineno  = int(m.group(1) or m.group(2))
            context = " ".join(lines[i:i + 3]).strip()
            results.append((lineno, context))
    return results


def _scene_id_at_line(stitched_script: str, target_lineno: int) -> str | None:
    current = None
    for lineno, line in enumerate(stitched_script.splitlines(), 1):
        m = re.match(r'^label\s+(\w+)\s*:', line)
        if m:
            current = m.group(1)
        if lineno == target_lineno:
            return current
    return current


def run_final_lint(output_dir: str, sdk_path: str) -> dict:
    """Run lint on the built project and return error summary for scoring."""
    lint_output = _run_renpy_lint(output_dir, sdk_path)
    errors = _parse_lint_errors(lint_output) if lint_output else []
    return {
        "error_count": len(errors),
        "errors": [ctx for _, ctx in errors[:10]],
    }
