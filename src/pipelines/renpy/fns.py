import os
import random
import re
import shutil
from pathlib import Path
from typing import Dict, List

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, JSON_SYSTEM, json_with_correction
from pipelines.renpy import graph as _graph
from pipelines.renpy._script import (
    _postprocess_script,
    _validate_and_repair,
    _stitch_script,
    _write_options_rpy,
    run_final_lint,
)
from pipelines.renpy.renpy_builder import _copy_templates, _distribute
from utils.image import write_solid_png

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_CHARACTER_COLORS = [
    "#c8ffc8", "#c8c8ff", "#ffc8c8", "#ffe0a3", "#d8b4ff", "#a7f3d0",
]


def _call_json(prompt: str, label: str, max_tokens: int, attempts: int = 3) -> dict:
    agent = PipelineAgent(JSON_SYSTEM, max_tokens=max_tokens)
    return json_with_correction(agent, prompt, label, attempts=attempts)


def _tone_str(premise: Dict) -> str:
    return "; ".join(
        f"{d.get('adjective', '')} ({d.get('explanation', '')})"
        for d in premise.get("tone_directives", [])
        if d.get("adjective")
    )


def _bg_id(location_id: str) -> str:
    return location_id if location_id.startswith("bg_") else f"bg_{location_id}"


def _get_sdk_path() -> str:
    try:
        from config.settings_manager import settings_manager
        sdk = settings_manager.get_settings().get("renpy_sdk_path") or ""
        if sdk:
            return sdk
    except Exception:
        pass
    return os.environ.get("RENPY_SDK", "")


# ---------------------------------------------------------------------------
# Stage 1: Graph (no LLM)
# ---------------------------------------------------------------------------

def _to_int(val, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_float(val, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def generate_graph(inputs: Dict, working_dir: Path) -> Dict:
    brief = inputs.get("brief", {})
    return _graph.generate_dag(
        num_endings=_to_int(brief.get("num_endings", 4), 4),
        depth=_to_int(brief.get("depth", 6), 6),
        min_good_endings=_to_int(brief.get("min_good_endings", 2), 2),
        merge_probability=_to_float(brief.get("merge_probability", 0.3), 0.3),
        seed=brief.get("seed"),
    )


# ---------------------------------------------------------------------------
# Stage 2: Premise (LLM)
# ---------------------------------------------------------------------------

def generate_premise(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    brief = inputs.get("brief", {})
    prompt = render_template(_PROMPTS_DIR / "premise.txt", {
        "genre":           brief.get("genre", ""),
        "tone":            brief.get("tone", ""),
        "setting":         brief.get("setting", ""),
        "notes":           brief.get("notes", ""),
        "character_count": brief.get("character_count", 3),
    })
    print("    [premise]  generating story premise")
    result = _call_json(prompt, "premise", max_tokens)

    chars = result.get("characters", [])
    for i, char in enumerate(chars):
        if not char.get("id") and char.get("name"):
            char["id"] = char["name"].lower().replace(" ", "_").replace("-", "_")
        char.setdefault("color", _CHARACTER_COLORS[i % len(_CHARACTER_COLORS)])

    return result


# ---------------------------------------------------------------------------
# Stage 3: Endings (LLM)
# ---------------------------------------------------------------------------

def generate_endings(inputs: Dict, working_dir: Path, max_tokens: int = 8000) -> Dict:
    premise    = inputs.get("premise", {})
    dag        = inputs.get("graph", {})
    ending_ids = dag.get("ending_ids", [])
    nodes      = dag.get("nodes", {})

    ending_specs = [
        {"id": eid, "end_type": nodes[eid]["end_type"]}
        for eid in ending_ids
        if eid in nodes
    ]

    prompt = render_template(_PROMPTS_DIR / "endings.txt", {
        "premise":       premise.get("premise", ""),
        "characters":    premise.get("characters", []),
        "ending_count":  len(ending_specs),
        "endings":       ending_specs,
    })
    print(f"    [endings]  generating {len(ending_specs)} ending(s)")
    result = _call_json(prompt, "endings", max_tokens)
    return {"endings": result.get("endings", [])}


# ---------------------------------------------------------------------------
# Stage 4: Backward fill (LLM — one call per non-ending node)
# ---------------------------------------------------------------------------

def backward_fill(inputs: Dict, working_dir: Path, max_tokens: int = 4000) -> Dict:
    premise     = inputs.get("premise", {})
    dag         = inputs.get("graph", {})
    raw_endings = inputs.get("endings", {})
    ending_list = raw_endings.get("endings", [])

    nodes = dag.get("nodes", {})
    topo  = dag.get("topological_order", [])

    beat_map: Dict[str, dict] = {}
    for e in ending_list:
        beat_map[e["id"]] = e

    premise_summary = (
        f"{premise.get('premise', '')}\n"
        f"Setting: {premise.get('setting', {}).get('name', '')}"
    )
    char_ids = [c["id"] for c in premise.get("characters", [])]

    for nid in reversed(topo):
        node = nodes.get(nid)
        if not node or node["type"] == "ending":
            continue

        children    = node["child_ids"]
        child_beats = [{"id": cid, **beat_map.get(cid, {})} for cid in children]
        reachable   = [beat_map.get(eid, {"id": eid}) for eid in node["reachable_endings"]]

        prompt = render_template(_PROMPTS_DIR / "backward_fill.txt", {
            "premise_summary":    premise_summary,
            "node_id":            nid,
            "node_type":          node["type"],
            "is_branch":          "true" if node["type"] == "branch" else "false",
            "child_beats":        child_beats,
            "child_ids":          children,
            "child_count":        len(children),
            "reachable_endings":  reachable,
            "available_characters": char_ids,
        })

        print(f"    [backward_fill]  {nid} ({node['type']})")
        try:
            result = _call_json(prompt, f"backward_fill {nid}", max_tokens)
        except RuntimeError:
            result = {
                "summary":          "A story beat leading toward the ending.",
                "dramatic_purpose": "Advances the plot.",
                "location_id":      child_beats[0].get("location_id", "location") if child_beats else "location",
                "emotional_tone":   "tense",
                "characters_present": char_ids[:1],
                "choice_labels":    [f"Option {i+1}" for i in range(len(children))] if node["type"] == "branch" else [],
            }
        beat_map[nid] = result

    return {"beat_map": beat_map}


# ---------------------------------------------------------------------------
# Stage 5: Node scripts (LLM — one call per dialogue slot)
# ---------------------------------------------------------------------------

_SLOT_NARRATION_PROB = 0.20
_SLOT_TARGET_MIN    = 8
_SLOT_TARGET_MAX    = 14
_POSITIONS          = ["left", "right", "center"]


def _find_protagonist_id(premise: Dict) -> str:
    for c in premise.get("characters", []):
        if c.get("role") == "protagonist":
            return c["id"]
    chars = premise.get("characters", [])
    return chars[0]["id"] if chars else ""


def _generate_slots(chars_present: List[str], protagonist_id: str) -> List[dict]:
    target = random.randint(_SLOT_TARGET_MIN, _SLOT_TARGET_MAX)
    weighted = []
    for cid in chars_present:
        weighted.append(cid)
        if cid == protagonist_id:
            weighted.append(cid)

    slots = []
    last_speaker = None
    for _ in range(target):
        if not weighted or random.random() < _SLOT_NARRATION_PROB:
            slots.append({"type": "narration"})
            last_speaker = None
        else:
            # Prevent consecutive lines from the same speaker
            candidates = [c for c in weighted if c != last_speaker] or weighted
            speaker = random.choice(candidates)
            slots.append({"type": "line", "speaker": speaker})
            last_speaker = speaker
    return slots


def _char_system_cache(premise: Dict) -> Dict[str, str]:
    tone    = _tone_str(premise)
    setting = premise.get("setting", {})
    chars   = premise.get("characters", [])

    cache = {}
    for char in chars:
        others = "\n".join(
            f"  {c['name']} ({c.get('role', '')})"
            for c in chars if c["id"] != char["id"]
        )
        cache[char["id"]] = render_template(_PROMPTS_DIR / "character_line_system.txt", {
            "char_name":           char.get("name", char["id"]),
            "voice_mechanics":     char.get("voice_mechanics", ""),
            "char_immediate_goal": char.get("immediate_goal", ""),
            "char_subtext":        char.get("subtext", ""),
            "premise":             premise.get("premise", ""),
            "tone":                tone,
            "setting_name":        setting.get("name", ""),
            "others":              others or "(none)",
        })
    return cache


def _narration_system(premise: Dict) -> str:
    setting = premise.get("setting", {})
    return render_template(_PROMPTS_DIR / "narration_system.txt", {
        "premise":            premise.get("premise", ""),
        "tone":               _tone_str(premise),
        "setting_name":       setting.get("name", ""),
        "setting_atmosphere": setting.get("atmosphere", ""),
    })


def _history_str(history: List[dict], last_n: int = 10) -> str:
    if not history:
        return "(scene just started)"
    recent = history[-last_n:]
    lines = []
    for h in recent:
        if h["type"] == "narration":
            lines.append(f"[narration]: {h['text']}")
        else:
            lines.append(f"{h['speaker']}: {h['text']}")
    return "\n".join(lines)


def _char_name_for(premise: Dict, char_id: str) -> str:
    for c in premise.get("characters", []):
        if c["id"] == char_id:
            return c.get("name", char_id)
    return char_id


_MAX_LINE_CHARS = 240
_SENT_RE = re.compile(r'(?<=[.!?])\s+')


def _split_dialogue(text: str) -> List[str]:
    """Split at sentence boundaries so no single box exceeds _MAX_LINE_CHARS."""
    if len(text) <= _MAX_LINE_CHARS:
        return [text]
    sentences = _SENT_RE.split(text)
    boxes: List[str] = []
    current = ""
    for sent in sentences:
        candidate = (current + " " + sent).strip() if current else sent
        if current and len(candidate) > _MAX_LINE_CHARS:
            boxes.append(current)
            current = sent
        else:
            current = candidate
    if current:
        boxes.append(current)
    return boxes or [text]


_SEG_RE = re.compile(r'(\*[^*]+\*)')


def _strip_speaker_prefix(raw: str) -> str:
    """Drop a leading 'Name:' the model sometimes prepends despite instructions."""
    if ":" in raw[:40]:
        parts = raw.split(":", 1)
        if len(parts) == 2 and len(parts[0].split()) <= 3:
            return parts[1].strip()
    return raw


def _parse_char_output(raw: str, cid: str) -> tuple:
    raw = _strip_speaker_prefix(raw.strip())

    # Strip any tilde emphasis markers (not supported)
    raw = re.sub(r'~([^~]+)~', r'\1', raw)

    segments = _SEG_RE.split(raw)
    content_lines: List[str] = []
    history_parts: List[str] = []
    speech_buf = ""

    def _flush_speech():
        nonlocal speech_buf
        text = speech_buf.strip().strip('"').strip("'").strip().replace('"', "'")
        if text:
            for box in _split_dialogue(text):
                content_lines.append(f'    {cid} "{box}"')
            history_parts.append(text)
        speech_buf = ""

    for seg in segments:
        if not seg:
            continue
        if seg.startswith('*') and seg.endswith('*') and len(seg) > 2:
            _flush_speech()
            action = seg[1:-1].strip().replace('"', "'")
            if action:
                history_parts.append(f"*{action}*")
                for box in _split_dialogue(action):
                    content_lines.append(f'    act "{box}"')
        else:
            speech_buf += seg

    _flush_speech()
    return content_lines, " ".join(history_parts)


def _call_line(system: str, user_prompt: str) -> str:
    """Narration: returns plain text (no segment parsing needed)."""
    agent = PipelineAgent(system, max_tokens=200)
    raw = agent.send(user_prompt).strip().strip('"').strip("'")
    return _strip_speaker_prefix(raw).strip('"').strip("'")


def _call_char_line(system: str, user_prompt: str) -> str:
    """Character dialogue: returns raw text for segment parsing."""
    agent = PipelineAgent(system, max_tokens=300)
    return _strip_speaker_prefix(agent.send(user_prompt).strip())


def _generate_node_by_slots(
    node_id: str,
    node_type: str,
    beat: dict,
    chars_present: List[str],
    children: List[str],
    choice_labels: List[str],
    protagonist_id: str,
    char_systems: Dict[str, str],
    narration_sys: str,
    premise: Dict,
    story_so_far: str = "",
) -> str:
    location_id    = beat.get("location_id", "location")
    background_id  = _bg_id(location_id)
    beat_summary   = beat.get("summary", "")
    emotional_tone = beat.get("emotional_tone", "")

    slots   = _generate_slots(chars_present, protagonist_id)
    history: List[dict] = []
    content_lines: List[str] = []

    for slot in slots:
        hist_str = _history_str(history)

        if slot["type"] == "narration":
            user_p = render_template(_PROMPTS_DIR / "narration.txt", {
                "beat_summary":   beat_summary,
                "emotional_tone": emotional_tone,
                "history":        hist_str,
                "story_so_far":   story_so_far,
            })
            text = _call_line(narration_sys, user_p)
            if text:
                history.append({"type": "narration", "text": text})
                content_lines.append(f'    "{text}"')

        else:
            cid       = slot["speaker"]
            char_name = _char_name_for(premise, cid)
            system    = char_systems.get(cid)
            if not system:
                continue
            user_p = render_template(_PROMPTS_DIR / "character_line.txt", {
                "char_name":      char_name,
                "beat_summary":   beat_summary,
                "emotional_tone": emotional_tone,
                "scene_objective": beat.get("dramatic_purpose", ""),
                "history":        hist_str,
                "story_so_far":   story_so_far,
            })
            raw = _call_char_line(system, user_p)
            if raw:
                lines, hist_text = _parse_char_output(raw, cid)
                if lines:
                    history.append({"type": "line", "speaker": cid, "text": hist_text})
                    content_lines.extend(lines)

    parts = [f"label {node_id}:"]
    parts.append(f"    scene {background_id} with dissolve")

    n = len(chars_present)
    for i, cid in enumerate(chars_present[:3]):
        pos = "center" if n == 1 else _POSITIONS[i]
        parts.append(f"    show {cid} at {pos}")

    parts.extend(content_lines)

    if node_type == "ending":
        parts.append('    "The End."')
        parts.append("    return")
    elif node_type == "branch" and children:
        parts.append("    menu:")
        for i, cid in enumerate(children):
            label = choice_labels[i] if i < len(choice_labels) else f"Choice {i+1}"
            parts.append(f'        "{label}":')
            parts.append(f"            jump {cid}")
    elif children:
        parts.append(f"    jump {children[0]}")
    else:
        parts.append('    "The End."')
        parts.append("    return")

    return "\n".join(parts) + "\n"


def write_node_scripts(inputs: Dict, working_dir: Path, max_tokens: int = 8000) -> Dict:
    premise        = inputs.get("premise", {})
    dag            = inputs.get("graph", {})
    beat_map       = inputs.get("beat_map", {}).get("beat_map", {})
    nodes          = dag.get("nodes", {})
    topo           = dag.get("topological_order", [])
    protagonist_id = _find_protagonist_id(premise)
    char_systems   = _char_system_cache(premise)
    narration_sys  = _narration_system(premise)

    scripts: Dict[str, str] = {}
    story_beats: List[str] = []

    for nid in topo:
        node = nodes.get(nid)
        if not node:
            continue

        node_type     = node["type"]
        beat          = beat_map.get(nid, {})
        chars_present = beat.get("characters_present", [])
        children      = node.get("child_ids", [])
        choice_labels = beat.get("choice_labels", [])
        story_so_far  = "\n".join(f"- {s}" for s in story_beats[-8:]) or "(story just beginning)"

        print(f"    [node_scripts]  {nid} ({node_type})")
        raw = _generate_node_by_slots(
            node_id=nid,
            node_type=node_type,
            beat=beat,
            chars_present=chars_present,
            children=children,
            choice_labels=choice_labels,
            protagonist_id=protagonist_id,
            char_systems=char_systems,
            narration_sys=narration_sys,
            premise=premise,
            story_so_far=story_so_far,
        )
        scripts[nid] = raw

        if beat.get("summary"):
            story_beats.append(beat["summary"])

    return {"scripts": scripts, "node_ids": topo}


# ---------------------------------------------------------------------------
# Stage 6: Asset manifest (LLM)
# ---------------------------------------------------------------------------

def generate_asset_manifest(inputs: Dict, working_dir: Path, max_tokens: int = 8000) -> Dict:
    premise  = inputs.get("premise", {})
    beat_map = inputs.get("beat_map", {}).get("beat_map", {})

    # Collect unique locations and summarise which beats happen there
    location_beats: Dict[str, List[str]] = {}
    for nid, beat in beat_map.items():
        loc = beat.get("location_id", "")
        if loc:
            location_beats.setdefault(loc, []).append(beat.get("summary", ""))

    location_summary = "\n".join(
        f"- {loc}: " + "; ".join(summaries[:3])
        for loc, summaries in location_beats.items()
    ) or "(no locations found)"

    tone = ", ".join(
        d.get("adjective", "") for d in premise.get("tone_directives", [])
    )

    prompt = render_template(_PROMPTS_DIR / "asset_manifest.txt", {
        "premise":          premise.get("premise", ""),
        "setting":          premise.get("setting", {}),
        "tone":             tone,
        "characters":       premise.get("characters", []),
        "location_summary": location_summary,
    })

    print("    [asset_manifest]  generating art direction and image descriptions")
    try:
        result = _call_json(prompt, "asset_manifest", max_tokens, attempts=3)
    except RuntimeError:
        print("    [asset_manifest]  LLM failed, building from beat_map")
        result = {}

    # Normalise backgrounds — ensure bg_ prefix and image_file
    bg_by_id: Dict[str, dict] = {}
    for bg in result.get("backgrounds", []):
        bid = _bg_id(bg.get("id", "bg_unknown"))
        bg["id"]         = bid
        bg["image_file"] = (bid[3:] if bid.startswith("bg_") else bid) + ".png"
        bg_by_id[bid]    = bg

    # Fill in any locations the LLM missed
    for loc in location_beats:
        bid = _bg_id(loc)
        if bid not in bg_by_id:
            bg_by_id[bid] = {
                "id":          bid,
                "name":        loc.replace("_", " ").title(),
                "image_file":  (bid[3:] if bid.startswith("bg_") else bid) + ".png",
                "description": f"{loc.replace('_', ' ').title()}. {premise.get('setting', {}).get('physical_description', '')}".strip(),
            }

    # Characters — always derived from premise, not LLM
    char_manifest = [
        {
            "id":         c["id"],
            "name":       c["name"],
            "appearance": c.get("appearance", ""),
            "color":      c.get("color", "#c8ffc8"),
            "image_file": f"{c['id']}.png",
        }
        for c in premise.get("characters", [])
    ]

    # CGs
    cgs = []
    for cg in result.get("cgs", []):
        cg_id = cg.get("id", "")
        if not cg_id.startswith("cg_"):
            cg_id = f"cg_{cg_id}"
        cgs.append({
            "id":          cg_id,
            "image_file":  f"{cg_id}.png",
            "description": cg.get("description", ""),
        })

    # Title card
    title_card = result.get("title_card", {})
    title_card["image_file"] = "title_card.png"
    if not title_card.get("description"):
        char_names = ", ".join(c["name"] for c in premise.get("characters", [])[:3])
        setting    = premise.get("setting", {})
        title_card["description"] = (
            f"Wide cinematic visual novel title card. "
            f"{setting.get('physical_description', '')} "
            f"Characters: {char_names}. Mood: {tone}. Atmospheric, detailed."
        )

    return {
        "art_direction": result.get("art_direction", {}),
        "backgrounds":   list(bg_by_id.values()),
        "characters":    char_manifest,
        "cgs":           cgs,
        "title_card":    title_card,
    }


# ---------------------------------------------------------------------------
# Stage 7: Images
# ---------------------------------------------------------------------------

def generate_images(inputs: Dict, working_dir: Path) -> Dict:
    from tools.comfyui_tools import (
        build_character_job, build_background_job,
        build_cg_job, build_title_card_job,
        generate_images_batch,
    )
    from tools.execution_context import track_written_file

    premise  = inputs.get("premise", {})
    manifest = inputs.get("asset_manifest", {})

    images_dir = working_dir / "game_output" / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    premise_chars = {c["id"]: c for c in premise.get("characters", [])}
    job_meta: List[Dict] = []
    jobs:     List[Dict] = []

    for bg in manifest.get("backgrounds", []):
        bg_file = bg["image_file"]
        job_meta.append({"file": bg_file, "dest": images_dir / bg_file, "kind": "bg"})
        jobs.append(build_background_job(bg.get("description", bg.get("name", bg["id"]))))

    for char in manifest.get("characters", []):
        img_file = char.get("image_file", f"{char['id']}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "char"})
        jobs.append(build_character_job(premise_chars.get(char["id"], char)))

    for cg in manifest.get("cgs", []):
        img_file = cg.get("image_file", f"{cg['id']}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "cg"})
        jobs.append(build_cg_job(cg.get("description", cg["id"])))

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
# Stage 8: Build
# ---------------------------------------------------------------------------

def build(inputs: Dict, working_dir: Path) -> Dict:
    from tools.execution_context import track_written_file

    brief        = inputs.get("brief", {})
    premise      = inputs.get("premise", {})
    manifest     = inputs.get("asset_manifest", {})
    node_scripts = inputs.get("node_scripts", {})
    scripts      = node_scripts.get("scripts", {})
    node_ids     = node_scripts.get("node_ids", list(scripts.keys()))

    title      = brief.get("title", "Untitled")
    output_dir = str(working_dir / "game_output")

    valid_backgrounds = {bg["id"] for bg in manifest.get("backgrounds", [])}
    valid_characters  = {c["id"] for c in manifest.get("characters", [])} | {"act"}
    valid_cgs         = {cg["id"] for cg in manifest.get("cgs", [])}
    valid_labels      = set(node_ids) | {"start", "splashscreen", "main_menu"}

    _validate_and_repair(scripts, node_ids, valid_labels, valid_backgrounds, valid_characters, valid_cgs)

    for nid in node_ids:
        if scripts.get(nid):
            scripts[nid] = _postprocess_script(scripts[nid], valid_characters)

    full_script = _stitch_script(premise, manifest, scripts, node_ids)

    game_dir = os.path.join(output_dir, "game")
    os.makedirs(game_dir, exist_ok=True)

    title_card_file = manifest.get("title_card", {}).get("image_file", "")
    first_bg        = manifest.get("backgrounds", [{}])[0]
    first_bg_id     = first_bg.get("id", "")
    first_bg_file   = first_bg.get("image_file", (first_bg_id[3:] + ".png") if first_bg_id.startswith("bg_") else "")
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
