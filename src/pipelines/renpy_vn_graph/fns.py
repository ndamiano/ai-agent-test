import json
import os
import shutil
from pathlib import Path
from typing import Dict, List

from pipelines.runner import render_template
from llm_clients.inference import PipelineAgent, strip_fences
from pipelines.renpy_vn_graph import graph as _graph
from pipelines.renpy._script import (
    _bible_summary,
    _character_vars_block,
    _validate_scene_script,
    _postprocess_script,
    _validate_and_repair,
    _stitch_script,
    _write_options_rpy,
    _SCRIPT_SYSTEM,
    run_final_lint,
)
from pipelines.renpy.renpy_builder import _copy_templates, _distribute

_PROMPTS_DIR = Path(__file__).parent / "prompts"

_JSON_SYSTEM = (
    "You are a precise creative writing assistant. Output only valid JSON. "
    "No markdown, no explanation, no code fences."
)

_CHARACTER_COLORS = [
    "#c8ffc8", "#c8c8ff", "#ffc8c8", "#ffe0a3", "#d8b4ff", "#a7f3d0",
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


def _call_json(prompt: str, label: str, max_tokens: int, attempts: int = 3) -> dict:
    for _ in range(attempts):
        agent = PipelineAgent(_JSON_SYSTEM, max_tokens=max_tokens)
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
                "summary":          f"A story beat leading toward the ending.",
                "dramatic_purpose": "Advances the plot.",
                "location_id":      child_beats[0].get("location_id", "location") if child_beats else "location",
                "emotional_tone":   "tense",
                "characters_present": char_ids[:1],
                "choice_labels":    [f"Option {i+1}" for i in range(len(children))] if node["type"] == "branch" else [],
            }
        beat_map[nid] = result

    return {"beat_map": beat_map}


# ---------------------------------------------------------------------------
# Stage 5: Node scripts (LLM — one call per node)
# ---------------------------------------------------------------------------

def _build_node_script_prompts(premise: Dict, dag: Dict, beat_map: Dict):
    """Yield (node_id, system_prompt, user_prompt) for every node. Used by dev_utils."""
    nodes = dag.get("nodes", {})
    topo  = dag.get("topological_order", [])
    for nid in topo:
        node = nodes.get(nid)
        if not node:
            continue
        beat          = beat_map.get(nid, {})
        chars_present = beat.get("characters_present", [])
        location_id   = beat.get("location_id", "")
        background_id = _bg_id(location_id) if location_id else "bg_location"
        node_type     = node["type"]
        children      = node["child_ids"]
        choice_labels = beat.get("choice_labels", [])
        bible_sum     = _bible_summary(premise, chars_present)
        char_vars     = _character_vars_block(premise, chars_present)

        if node_type == "branch":
            choices = [
                {"label": choice_labels[i] if i < len(choice_labels) else f"Choice {i+1}", "jump": cid}
                for i, cid in enumerate(children)
            ]
            next_info = "This is a choice point. Use a menu: block.\n" + "\n".join(
                f"  menu option {i+1}: \"{c['label']}\" → jump {c['jump']}"
                for i, c in enumerate(choices)
            )
            end_instruction = (
                "  menu:\n" +
                "\n".join(f'      "{c["label"]}":\n          jump {c["jump"]}' for c in choices)
            )
        elif node_type == "ending":
            next_info       = 'This is an ending. End with "The End." then return.'
            end_instruction = '  "The End."\n  return'
        else:
            next_id         = children[0] if children else None
            next_beat       = beat_map.get(next_id, {}) if next_id else {}
            next_info       = f"Next node: {next_id} — {next_beat.get('summary', '')}" if next_id else "This is the final node."
            end_instruction = f"  jump {next_id}" if next_id else '  "The End."\n  return'

        user_prompt = render_template(_PROMPTS_DIR / "node_script.txt", {
            "bible_summary":   bible_sum,
            "beat":            beat,
            "node_id":         nid,
            "node_type":       node_type,
            "background_id":   background_id,
            "character_vars":  char_vars,
            "next_info":       next_info,
            "end_instruction": end_instruction,
        })
        yield (nid, _SCRIPT_SYSTEM, user_prompt)


def write_node_scripts(inputs: Dict, working_dir: Path, max_tokens: int = 8000) -> Dict:
    premise  = inputs.get("premise", {})
    dag      = inputs.get("graph", {})
    beat_map = inputs.get("beat_map", {}).get("beat_map", {})
    nodes    = dag.get("nodes", {})
    topo     = dag.get("topological_order", [])

    scripts: Dict[str, str] = {}

    for nid, system, prompt in _build_node_script_prompts(premise, dag, beat_map):
        node      = nodes.get(nid, {})
        node_type = node.get("type", "")
        beat      = beat_map.get(nid, {})
        location_id   = beat.get("location_id", "")
        background_id = _bg_id(location_id) if location_id else "bg_location"
        children      = node.get("child_ids", [])

        print(f"    [node_scripts]  {nid} ({node_type})")
        agent = PipelineAgent(_SCRIPT_SYSTEM, max_tokens=max_tokens)
        raw   = strip_fences(agent.send(prompt)).strip()

        valid, error = _validate_scene_script(nid, raw)
        if not valid:
            for attempt in range(1, 3):
                print(f"    [node_scripts]  {nid} retry {attempt}: {error}")
                raw = strip_fences(agent.send(
                    f"Error: {error}\n"
                    f"Rewrite the script. It must start with 'label {nid}:'. "
                    "Output only Ren'Py script."
                )).strip()
                valid, error = _validate_scene_script(nid, raw)
                if valid:
                    break

        if not valid:
            print(f"    [node_scripts]  {nid} using fallback")
            if node_type == "ending":
                fallback_end = '    "The End."\n    return'
            elif children:
                fallback_end = f"    jump {children[0]}"
            else:
                fallback_end = '    "The End."\n    return'
            raw = (
                f"label {nid}:\n"
                f"    scene {background_id} with dissolve\n"
                f'    "{beat.get("summary", "...")}"\n'
                f"{fallback_end}\n"
            )

        scripts[nid] = raw

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
    from pipelines.renpy.fns import generate_images as _gen
    # renpy.fns expects inputs["bible"] — adapt
    return _gen({**inputs, "bible": inputs.get("premise", {})}, working_dir)


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
    valid_characters  = {c["id"] for c in manifest.get("characters", [])}
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
