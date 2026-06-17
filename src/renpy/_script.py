import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

from renpy.templating import render_template
from llm_clients.inference import PipelineAgent, strip_fences

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_SCRIPT_SYSTEM = (
    "You are a Ren'Py script writer. Output only valid Ren'Py script. "
    "No JSON, no markdown fences, no explanation."
)


_STAGE_SLOTS = ("left", "right", "center")


def _stage_positions(shown: dict) -> dict:
    """Assign each on-screen character a distinct slot so they don't pile up at
    center (ports the old pipeline's deterministic staging). A character's explicit
    `at <pos>` wins; the rest fill the remaining left/right/center slots in the order
    they were shown. A lone character sits center."""
    ids = list(shown)
    if len(ids) == 1:
        return {ids[0]: shown[ids[0]] or "center"}
    taken = {p for p in shown.values() if p}
    free = [p for p in _STAGE_SLOTS if p not in taken]
    return {cid: (shown[cid] or (free.pop(0) if free else "center")) for cid in ids}


def _postprocess_script(script: str, valid_characters: set) -> str:
    lines = script.split("\n")
    result = []
    shown: dict = {}   # cid -> explicit position, or None (auto-assigned a slot)
    prev_expects_block = False
    prev_indent = 0

    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            result.append(line)
            continue

        if re.match(r'[Nn]arrator\s+"', stripped):
            line = re.sub(r'^(\s*)[Nn]arrator\s+(")', r'\1\2', line)
            stripped = line.lstrip()

        indent = len(line) - len(stripped)
        if not prev_expects_block and indent > prev_indent and prev_indent > 0:
            line = " " * prev_indent + stripped
            indent = prev_indent

        show_m = re.match(r'show\s+(\w+)(?:\s+at\s+(\w+))?', stripped)
        hide_m = re.match(r'hide\s+(\w+)', stripped)
        if show_m:
            cid, explicit = show_m.group(1), show_m.group(2)
            if cid in valid_characters:
                shown[cid] = explicit
                if not explicit:
                    # Give a bare `show X` a slot now so it isn't centered on top of
                    # whoever's already on stage; dialogue lines re-stage as the cast grows.
                    line = re.sub(r'^(\s*show\s+\w+)', r'\1 at ' + _stage_positions(shown)[cid],
                                  line, count=1)
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
                    positions = _stage_positions(shown)
                    for cid in shown:
                        t = "speaking" if cid == speaker else "not_speaking"
                        result.append(f"{pad}show {cid} at {positions[cid]}, {t}")

        if "%" in line and '%%' not in line:
            line = re.sub(r'("(?:[^"\\]|\\.)*")', lambda m: m.group(0).replace("%", "%%"), line)

        result.append(line)
        prev_expects_block = stripped.rstrip().endswith(":")
        prev_indent = indent

    return "\n".join(result)


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
    _RENPY_KEYWORDS = {"scene", "show", "hide", "jump", "return", "menu", "call",
                       "pause", "play", "stop", "queue", "voice", "nvl", "window",
                       "image", "define", "transform", "init", "python", "label", "with"}
    dialogue_speakers = set(re.findall(r'^[ \t]*(\w+)\s+"', script, re.MULTILINE))
    broken_speakers = dialogue_speakers - valid_characters - _RENPY_KEYWORDS
    if broken_jumps:
        parts.append(f"unknown jump targets: {sorted(broken_jumps)}")
    if broken_bgs:
        parts.append(f"unknown backgrounds: {sorted(broken_bgs)}")
    if broken_cgs:
        parts.append(f"unknown CG ids: {sorted(broken_cgs)}")
    if broken_chars:
        parts.append(f"unknown characters in show: {sorted(broken_chars)}")
    if broken_speakers:
        parts.append(f"unknown dialogue speakers (not defined as Character): {sorted(broken_speakers)}")
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


def _stitch_script(
    bible: Dict,
    manifest: Dict,
    scripts: Dict[str, str],
    scene_ids: List[str],
) -> str:
    lines = []
    chars = bible.get("characters", [])

    lines.append("## Characters")
    lines.append('define act = Character(None, what_italic=True, what_color="#a0a0a0")')
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
    with open(os.path.join(game_dir, "options.rpy"), "w", encoding="utf-8") as f:
        f.write(content)


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


def node_line_ranges(full_script: str, node_ids: List[str]) -> List[Tuple[int, int, str]]:
    """Map each node's `label <id>:` to its [start, end] line span in the stitched
    script, so a lint error on a script.rpy line can be attributed to the node that
    owns it — the agent fixes that one scene instead of churning across all of them."""
    starts = []
    for i, ln in enumerate(full_script.split("\n"), 1):
        m = re.match(r'\s*label\s+(\w+)\s*:', ln)
        if m and m.group(1) in node_ids:
            starts.append((i, m.group(1)))
    starts.sort()
    ranges = []
    for idx, (start, sid) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else 10 ** 9
        ranges.append((start, end, sid))
    return ranges


def _node_for_line(lineno: int, ranges: List[Tuple[int, int, str]]):
    for start, end, sid in ranges:
        if start <= lineno <= end:
            return sid
    return None


def run_final_lint(output_dir: str, sdk_path: str, node_ranges=None) -> dict:
    lint_output = _run_renpy_lint(output_dir, sdk_path)
    errors = _parse_lint_errors(lint_output) if lint_output else []
    formatted = []
    for lineno, ctx in errors[:10]:
        sid = _node_for_line(lineno, node_ranges) if node_ranges else None
        formatted.append(f"[{sid}] {ctx}" if sid else ctx)
    return {
        "error_count": len(errors),
        "errors": formatted,
    }
