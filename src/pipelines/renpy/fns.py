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


def _to_int(val, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Stage 1: Premise (LLM)
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

    # The model intermittently omits protagonist_id; backfill so the artifact
    # always carries a resolving reference for downstream stages.
    result["protagonist_id"] = _find_protagonist_id(result)

    _generate_voice_sheets(result)
    return result


def _generate_voice_sheets(premise: Dict, max_tokens: int = 2000) -> None:
    """One plain-text call per character — full sheets in one JSON call truncate on small models."""
    chars = premise.get("characters", [])
    tone  = _tone_str(premise)
    for char in chars:
        others = "\n".join(
            f"- {c.get('name', c['id'])} ({c.get('identity', '')}): {c.get('voice', '')}"
            for c in chars if c["id"] != char["id"]
        )
        prompt = render_template(_PROMPTS_DIR / "voice_sheet.txt", {
            "char_name":  char.get("name", char["id"]),
            "identity":   char.get("identity", ""),
            "situation":  char.get("situation", ""),
            "charge":     char.get("charge", ""),
            "voice":      char.get("voice", ""),
            "premise":    premise.get("premise", ""),
            "tone":       tone,
            "others":     others or "(none)",
        })
        print(f"    [premise]  voice sheet: {char.get('name', char['id'])}")
        agent = PipelineAgent("You write character voice sheets for fiction writers.", max_tokens=max_tokens)
        sheet = agent.send(prompt).strip()
        if sheet:
            char["voice_mechanics"] = sheet


# ---------------------------------------------------------------------------
# Stage 2: Story outline (LLM — endings, then spine, then scenes per segment)
# ---------------------------------------------------------------------------

_END_TYPE_CYCLE = ["bad", "neutral", "good"]


def _ending_slots(num_endings: int, min_good: int) -> List[dict]:
    types = ["good"] * min(min_good, num_endings)
    i = 0
    while len(types) < num_endings:
        types.append(_END_TYPE_CYCLE[i % len(_END_TYPE_CYCLE)])
        i += 1
    return [{"id": f"ending_{i+1:03d}", "end_type": t} for i, t in enumerate(types)]


def _compact_characters(premise: Dict) -> List[dict]:
    return [
        {
            "id":       c["id"],
            "name":     c.get("name", c["id"]),
            "identity": c.get("identity", ""),
            "charge":   c.get("charge", ""),
        }
        for c in premise.get("characters", [])
    ]


def _valid_cast(scene: dict, char_ids: set, protagonist_id: str) -> List[str]:
    cast = [c for c in scene.get("characters_present", []) if c in char_ids]
    return cast[:3] or [protagonist_id]


def _format_scenes(scenes: List[dict], header: str) -> str:
    if not scenes:
        return ""
    lines = [header]
    lines += [f"- [{s.get('scene_type', '?')}] {s.get('summary', '')}" for s in scenes]
    return "\n".join(lines)


def _generate_segment_scenes(
    premise: Dict,
    segment_job: str,
    segment_destination: str,
    prior_scenes: str,
    scene_count: int,
    label: str,
    char_ids: set,
    protagonist_id: str,
    max_tokens: int,
) -> List[dict]:
    prompt = render_template(_PROMPTS_DIR / "story_scenes.txt", {
        "premise":             premise.get("premise", ""),
        "central_question":    premise.get("central_question", ""),
        "characters":          _compact_characters(premise),
        "segment_job":         segment_job,
        "segment_destination": segment_destination,
        "prior_scenes":        prior_scenes or "(none — this is the opening of the story)",
        "scene_count":         scene_count,
    })
    result = _call_json(prompt, f"story scenes ({label})", max_tokens)
    scenes = [s for s in result.get("scenes", []) if isinstance(s, dict) and s.get("summary")]
    if not scenes:
        raise RuntimeError(f"story scenes ({label}): no usable scenes returned")
    scenes = scenes[:scene_count]
    for s in scenes:
        s["characters_present"] = _valid_cast(s, char_ids, protagonist_id)
    return scenes


def _repair_spine(spine: dict, ending_ids: List[str]) -> dict:
    """Force a valid 2-option partition of all endings."""
    options = [o for o in spine.get("commitment_choice", {}).get("options", []) if isinstance(o, dict)]
    options = options[:2]
    while len(options) < 2:
        options.append({"label": f"Path {len(options)+1}", "strategy": "", "ending_ids": []})

    seen: set = set()
    for o in options:
        kept = []
        for e in o.get("ending_ids", []):
            if e in ending_ids and e not in seen:
                kept.append(e)
                seen.add(e)
        o["ending_ids"] = kept
    for eid in ending_ids:
        if eid not in seen:
            min(options, key=lambda o: len(o["ending_ids"]))["ending_ids"].append(eid)
    for o in options:
        if not o["ending_ids"]:
            donor = max(options, key=lambda x: len(x["ending_ids"]))
            o["ending_ids"].append(donor["ending_ids"].pop())

    spine.setdefault("commitment_choice", {})["options"] = options
    return spine


def generate_story(inputs: Dict, working_dir: Path, max_tokens: int = 16000) -> Dict:
    brief   = inputs.get("brief", {})
    premise = inputs.get("premise", {})

    num_endings = max(2, _to_int(brief.get("num_endings", 4), 4))
    min_good    = _to_int(brief.get("min_good_endings", 2), 2)
    depth       = max(4, _to_int(brief.get("depth", 6), 6))
    trunk_len   = max(2, depth // 2)
    arm_len     = max(1, depth - trunk_len - 1)

    char_ids       = {c["id"] for c in premise.get("characters", [])}
    protagonist_id = _find_protagonist_id(premise)
    question       = premise.get("central_question", "")

    # 1. Endings — each a distinct answer to the central question
    slots = _ending_slots(num_endings, min_good)
    prompt = render_template(_PROMPTS_DIR / "story_endings.txt", {
        "premise":          premise.get("premise", ""),
        "central_question": question,
        "characters":       _compact_characters(premise),
        "ending_count":     num_endings,
        "min_good":         min_good,
        "endings":          slots,
    })
    print(f"    [story]  endings ({num_endings} answers to the central question)")
    raw_endings = _call_json(prompt, "story endings", max_tokens).get("endings", [])
    endings = []
    for i, slot in enumerate(slots):
        e = raw_endings[i] if i < len(raw_endings) and isinstance(raw_endings[i], dict) else {}
        e["id"] = slot["id"]
        e.setdefault("end_type", slot["end_type"])
        e.setdefault("summary", f"The story ends ({slot['end_type']}).")
        e["characters_present"] = _valid_cast(e, char_ids, protagonist_id)
        endings.append(e)

    # The model may reassign end types, but only within the brief's constraints
    model_types = [e.get("end_type") for e in endings]
    valid_types = all(t in ("good", "neutral", "bad") for t in model_types)
    if not valid_types or model_types.count("good") < min(min_good, num_endings):
        for e, slot in zip(endings, slots):
            e["end_type"] = slot["end_type"]

    # 2. Spine — the commitment choice and which endings each arm owns
    prompt = render_template(_PROMPTS_DIR / "story_spine.txt", {
        "premise":          premise.get("premise", ""),
        "central_question": question,
        "endings":          endings,
    })
    print("    [story]  spine (commitment choice + arm assignment)")
    spine = _repair_spine(_call_json(prompt, "story spine", max_tokens), [e["id"] for e in endings])
    commitment = spine["commitment_choice"]
    endings_by_id = {e["id"]: e for e in endings}

    # 3. Scenes — trunk first, then each arm with everything prior in view
    option_lines = "\n".join(
        f"- \"{o.get('label', '')}\": {o.get('strategy', '')}" for o in commitment["options"]
    )
    trunk = _generate_segment_scenes(
        premise,
        segment_job=(
            "Open the story. Establish the protagonist's world and the people in it, "
            "then escalate until the commitment choice is unavoidable. The final scene is the choice scene: "
            f"{commitment.get('situation', '')}"
        ),
        segment_destination=(
            "The final scene ends with the player choosing between:\n"
            f"{option_lines}\n"
            "Set this choice up so both options feel costly. Do not resolve it."
        ),
        prior_scenes="",
        scene_count=trunk_len,
        label="trunk",
        char_ids=char_ids,
        protagonist_id=protagonist_id,
        max_tokens=max_tokens,
    )
    print(f"    [story]  trunk ({len(trunk)} scenes)")

    arms = []
    written = list(trunk)
    for idx, option in enumerate(commitment["options"]):
        arm_endings = [endings_by_id[eid] for eid in option["ending_ids"]]
        crisis_labels = [c for c in (option.get("crisis_labels") or []) if isinstance(c, str)]
        if len(arm_endings) > 1 and len(crisis_labels) != len(arm_endings):
            crisis_labels = [e.get("title", f"Choice {i+1}") for i, e in enumerate(arm_endings)]

        if len(arm_endings) > 1:
            dest_lines = "\n".join(
                f"- \"{lbl}\" → {e.get('summary', '')} (answer: {e.get('answer', '')})"
                for lbl, e in zip(crisis_labels, arm_endings)
            )
            destination = (
                "The final scene is the crisis choice. It ends with the player choosing between:\n"
                f"{dest_lines}\nBuild to this choice; do not resolve it."
            )
        else:
            e = arm_endings[0]
            destination = (
                f"This arm flows into one ending: {e.get('summary', '')} "
                f"(answer: {e.get('answer', '')}). The final scene sets it up without playing it."
            )

        scenes = _generate_segment_scenes(
            premise,
            segment_job=(
                f"This is the path where the protagonist chose \"{option.get('label', '')}\": "
                f"{option.get('strategy', '')} Play out this strategy meeting reality. "
                "The choice scene has already been played — this segment opens immediately AFTER "
                "the protagonist chose. Never restage or revisit that decision; show its consequences."
            ),
            segment_destination=destination,
            prior_scenes=_format_scenes(written, "Scenes already written:"),
            scene_count=arm_len,
            label=f"arm {idx+1}",
            char_ids=char_ids,
            protagonist_id=protagonist_id,
            max_tokens=max_tokens,
        )
        print(f"    [story]  arm {idx+1} \"{option.get('label', '')}\" ({len(scenes)} scenes)")
        written.extend(scenes)
        arms.append({
            "label":         option.get("label", ""),
            "strategy":      option.get("strategy", ""),
            "ending_ids":    option["ending_ids"],
            "crisis_labels": crisis_labels,
            "scenes":        scenes,
        })

    story = {
        "central_question":  question,
        "endings":           endings,
        "commitment_choice": commitment,
        "trunk":             trunk,
        "arms":              arms,
    }
    _graph.assemble_story(story)  # fail fast inside the stage so retries can fix it
    return story


# ---------------------------------------------------------------------------
# Stages 3+4: Graph + beat map (no LLM — deterministic assembly of the outline)
# ---------------------------------------------------------------------------

def graph_from_story(inputs: Dict, working_dir: Path) -> Dict:
    return _graph.assemble_story(inputs.get("story", {}))["graph"]


def beat_map_from_story(inputs: Dict, working_dir: Path) -> Dict:
    return {"beat_map": _graph.assemble_story(inputs.get("story", {}))["beat_map"]}


# ---------------------------------------------------------------------------
# Stage 5: Node scripts (LLM — one call per dialogue slot)
# ---------------------------------------------------------------------------

_SLOT_NARRATION_PROB = 0.20
_SLOT_TARGET_MIN    = 8
_SLOT_TARGET_MAX    = 14
_SKETCH_MIN_LINES   = 4
_POSITIONS          = ["left", "right", "center"]


def _find_protagonist_id(premise: Dict) -> str:
    chars = premise.get("characters", [])
    pid = premise.get("protagonist_id", "")
    if pid and any(c["id"] == pid for c in chars):
        return pid
    return chars[0]["id"] if chars else ""


def _generate_slots(chars_present: List[str], protagonist_id: str) -> List[dict]:
    """Random slot assignment — fallback when the scene sketch fails."""
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


def _guaranteed_ancestors(nodes: Dict, topo: List[str]) -> Dict[str, List[str]]:
    """Per node: ids on EVERY root→node path, in topo order. With branching,
    only these events are guaranteed to have happened when the player arrives."""
    dom: Dict[str, set] = {}
    for nid in topo:
        node = nodes.get(nid, {})
        parents = [p for p in node.get("parent_ids", []) if p in dom]
        if not parents:
            dom[nid] = set()
        else:
            dom[nid] = set.intersection(*[dom[p] | {p} for p in parents])
    order = {nid: i for i, nid in enumerate(topo)}
    return {nid: sorted(s, key=order.get) for nid, s in dom.items()}


def _exit_note(node_type: str, beat: dict, children: List[str],
               choice_labels: List[str], beat_map: Dict[str, dict]) -> str:
    if node_type == "ending":
        return (
            "This is the story's final scene. The last lines must land the ending — "
            f"{beat.get('summary', '')} — with finality, not a cliffhanger."
        )
    if node_type == "branch" and choice_labels:
        labels = " / ".join(choice_labels)
        return (
            f"The scene ends with the player choosing between: {labels}. "
            "The final lines must make this choice feel urgent and unresolved — do not pick for them."
        )
    if children:
        nxt = beat_map.get(children[0], {}).get("summary", "")
        if nxt:
            return f"The scene flows directly into the next: {nxt}"
    return ""


def _validate_sketch(sketch: dict, chars_present: List[str]) -> List[dict]:
    """Keep only lines with a valid speaker; map narrator to narration slots."""
    raw_lines = sketch.get("lines", []) if isinstance(sketch, dict) else []
    slots = []
    for ln in raw_lines:
        if not isinstance(ln, dict):
            continue
        speaker = ln.get("speaker", "")
        slot = {
            "intent":   ln.get("intent", ""),
            "emotion":  ln.get("emotion", ""),
            "register": ln.get("register", "plain"),
        }
        if speaker in ("narrator", "narration"):
            slots.append({"type": "narration", **slot})
        elif speaker in chars_present:
            slots.append({"type": "line", "speaker": speaker, **slot})
    return slots[:_SLOT_TARGET_MAX + 2]


def _generate_scene_sketch(
    beat: dict,
    chars_present: List[str],
    premise: Dict,
    story_so_far: str,
    exit_note: str,
    scene_position: str = "",
    max_tokens: int = 3000,
) -> dict:
    char_by_id = {c["id"]: c for c in premise.get("characters", [])}
    characters = [
        {
            "id":       cid,
            "name":     char_by_id.get(cid, {}).get("name", cid),
            "identity": char_by_id.get(cid, {}).get("identity", ""),
            "charge":   char_by_id.get(cid, {}).get("charge", ""),
        }
        for cid in chars_present
    ]
    prompt = render_template(_PROMPTS_DIR / "scene_sketch.txt", {
        "story_so_far":     story_so_far,
        "beat_summary":     beat.get("summary", ""),
        "dramatic_purpose": beat.get("dramatic_purpose", ""),
        "scene_type":       beat.get("scene_type", ""),
        "scene_position":   scene_position or "(unspecified)",
        "emotional_tone":   beat.get("emotional_tone", beat.get("tone", "")),
        "exit_note":        exit_note or "(open — the scene simply ends)",
        "characters":       characters,
        "line_count_min":   _SLOT_TARGET_MIN,
        "line_count_max":   _SLOT_TARGET_MAX,
    })
    return _call_json(prompt, "scene_sketch", max_tokens)


def _char_system_cache(premise: Dict) -> Dict[str, str]:
    tone    = _tone_str(premise)
    setting = premise.get("setting", {})
    chars   = premise.get("characters", [])

    cache = {}
    for char in chars:
        others = "\n".join(
            f"  {c['name']} — {c.get('identity', '')}"
            for c in chars if c["id"] != char["id"]
        )
        cache[char["id"]] = render_template(_PROMPTS_DIR / "character_line_system.txt", {
            "char_name":       char.get("name", char["id"]),
            "char_identity":   char.get("identity", ""),
            "char_situation":  char.get("situation", ""),
            "char_charge":     char.get("charge", ""),
            "voice_mechanics": char.get("voice_mechanics") or char.get("voice", ""),
            "premise":         premise.get("premise", ""),
            "tone":            tone,
            "setting_name":    setting.get("name", ""),
            "others":          others or "(none)",
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
            lines.append(f"{h.get('speaker_name', h['speaker'])}: {h['text']}")
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


_ATTRIB_RE = re.compile(
    r'^(?:[A-Z][a-z]*\s+)?(?:he|she|they|\w+)\s+(?:says?|said|asks?|asked|replies|replied|'
    r'snaps?|snapped|whispers?|whispered|mutters?|muttered|adds?|added)\b[\s,.]*$',
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r'"([^"]+)"')


def _split_speech_and_directions(raw: str) -> str:
    """Models sometimes write screenplay-style: "speech" She does a thing.
    Re-tag the unquoted prose as *action* segments so the parser handles it.
    Only applies when the line opens with a quote — a quote mid-sentence is
    dialogue content, not formatting."""
    if not raw.lstrip().startswith('"'):
        return raw
    spans = _QUOTED_RE.findall(raw)
    if not spans:
        return raw
    parts: List[str] = []
    for i, seg in enumerate(re.split(r'"[^"]+"', raw)):
        seg = seg.strip()
        if seg and '*' not in seg:
            first_person = re.match(r"^I\b|^I'", seg)
            if first_person:
                parts.append(seg)
            elif not _ATTRIB_RE.match(seg) and len(seg.split()) > 3:
                parts.append(f"*{seg}*")
        if i < len(spans):
            parts.append(spans[i])
    return " ".join(parts)


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoji blocks
    "\u2600-\u27BF"          # misc symbols, dingbats
    "\u2B00-\u2BFF"          # arrows, stars
    "\uFE00-\uFE0F"          # variation selectors
    "\u200D"                 # zero-width joiner
    "]+"
)


def _clean_line_text(raw: str) -> str:
    """Normalize model output into text safe inside a Ren'Py say string."""
    raw = raw.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    raw = _EMOJI_RE.sub("", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _parse_char_output(raw: str, cid: str) -> tuple:
    raw = _strip_speaker_prefix(raw.strip())
    raw = _clean_line_text(raw)

    # Strip any tilde emphasis markers (not supported)
    raw = re.sub(r'~([^~]+)~', r'\1', raw)

    raw = _split_speech_and_directions(raw)

    segments = _SEG_RE.split(raw)
    content_lines: List[str] = []
    history_parts: List[str] = []
    speech_buf = ""

    def _flush_speech():
        nonlocal speech_buf
        text = speech_buf.strip().strip('"').strip("'").strip()
        # Unbalanced quote = formatting artifact, not quoted speech — drop it
        if text.count('"') % 2:
            text = text.replace('"', "")
        else:
            text = text.replace('"', "'")
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
    raw = _clean_line_text(_strip_speaker_prefix(raw)).strip('"').strip("'")
    return raw.replace('"', "'")


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
    exit_note: str = "",
    scene_position: str = "",
) -> str:
    location_id    = beat.get("location_id", "location")
    background_id  = _bg_id(location_id)
    beat_summary   = beat.get("summary", "")
    emotional_tone = beat.get("emotional_tone", beat.get("tone", ""))

    sketch: dict = {}
    slots: List[dict] = []
    try:
        sketch = _generate_scene_sketch(beat, chars_present, premise, story_so_far, exit_note, scene_position)
        slots  = _validate_sketch(sketch, chars_present)
    except RuntimeError:
        pass
    if len(slots) < _SKETCH_MIN_LINES:
        print(f"    [node_scripts]  {node_id}: sketch unusable, falling back to random slots")
        slots = _generate_slots(chars_present, protagonist_id)

    objectives = sketch.get("character_objectives", {}) if isinstance(sketch, dict) else {}
    if not isinstance(objectives, dict):
        objectives = {}

    history: List[dict] = []
    content_lines: List[str] = []

    for idx, slot in enumerate(slots):
        hist_str  = _history_str(history)
        is_final  = idx >= len(slots) - 2
        slot_exit = exit_note if is_final else ""
        intent    = slot.get("intent") or "Advance the scene — react to the last line."
        emotion   = slot.get("emotion") or emotional_tone
        register_note = (
            "This is a charged line — emotion breaks through, and the character's signature register may surface."
            if slot.get("register") == "charged"
            else "This is a plain line — everyday spoken words. No imagery, no metaphor."
        )

        if slot["type"] == "narration":
            user_p = render_template(_PROMPTS_DIR / "narration.txt", {
                "beat_summary":   beat_summary,
                "emotional_tone": emotional_tone,
                "scene_position": scene_position,
                "line_intent":    intent,
                "exit_note":      slot_exit,
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
                "scene_position": scene_position,
                "scene_objective": objectives.get(cid) or beat.get("dramatic_purpose", ""),
                "line_intent":    intent,
                "line_emotion":   emotion,
                "register_note":  register_note,
                "exit_note":      slot_exit,
                "history":        hist_str,
                "story_so_far":   story_so_far,
            })
            raw = _call_char_line(system, user_p)
            if raw:
                lines, hist_text = _parse_char_output(raw, cid)
                if lines:
                    history.append({
                        "type": "line", "speaker": cid,
                        "speaker_name": char_name, "text": hist_text,
                    })
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


def generate_single_node(
    premise: Dict,
    dag: Dict,
    beat_map: Dict[str, dict],
    nid: str,
    char_systems: Dict[str, str] = None,
    narration_sys: str = None,
    ancestors: Dict[str, List[str]] = None,
) -> str:
    """Generate the script for one node. Used by write_node_scripts and the
    single-scene dev CLI (python -m pipelines.renpy.scene)."""
    nodes = dag.get("nodes", {})
    topo  = dag.get("topological_order", [])
    node  = nodes[nid]

    protagonist_id = _find_protagonist_id(premise)
    if char_systems is None:
        char_systems = _char_system_cache(premise)
    if narration_sys is None:
        narration_sys = _narration_system(premise)
    if ancestors is None:
        ancestors = _guaranteed_ancestors(nodes, topo)

    node_type     = node["type"]
    beat          = beat_map.get(nid, {})
    chars_present = beat.get("characters_present", [])
    children      = node.get("child_ids", [])
    choice_labels = beat.get("choice_labels", [])

    # Only beats on every path to this node — sibling-branch events never happened here
    anc = ancestors.get(nid, [])
    past = [
        beat_map[aid]["summary"]
        for aid in anc
        if beat_map.get(aid, {}).get("summary")
    ]
    story_so_far = "\n".join(f"- {s}" for s in past[-8:]) or "(story just beginning)"
    exit_note    = _exit_note(node_type, beat, children, choice_labels, beat_map)

    when = beat.get("when", "")
    pos  = "the story's opening scene" if not anc else f"scene {len(anc) + 1} of this playthrough"
    scene_position = f"{pos} — {when}" if when else pos

    return _generate_node_by_slots(
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
        exit_note=exit_note,
        scene_position=scene_position,
    )


def write_node_scripts(inputs: Dict, working_dir: Path, max_tokens: int = 8000) -> Dict:
    premise       = inputs.get("premise", {})
    dag           = inputs.get("graph", {})
    beat_map      = inputs.get("beat_map", {}).get("beat_map", {})
    nodes         = dag.get("nodes", {})
    topo          = dag.get("topological_order", [])
    char_systems  = _char_system_cache(premise)
    narration_sys = _narration_system(premise)
    ancestors     = _guaranteed_ancestors(nodes, topo)

    scripts: Dict[str, str] = {}
    for nid in topo:
        if nid not in nodes:
            continue
        print(f"    [node_scripts]  {nid} ({nodes[nid]['type']})")
        scripts[nid] = generate_single_node(
            premise, dag, beat_map, nid,
            char_systems=char_systems,
            narration_sys=narration_sys,
            ancestors=ancestors,
        )

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
    sdk_path = _get_sdk_path()
    _copy_templates(game_dir, sdk_path)

    # Overwrite placeholder main menu background with generated title card
    title_card_src = os.path.join(game_dir, "images", "title_card.png")
    gui_main_menu  = os.path.join(game_dir, "gui", "main_menu.png")
    if os.path.exists(title_card_src) and os.path.exists(os.path.dirname(gui_main_menu)):
        shutil.copy2(title_card_src, gui_main_menu)
        print("    [build]  title card → gui/main_menu.png")

    print(f"    [build]  project written to: {output_dir}")
    result = {"project_dir": os.path.abspath(output_dir)}

    if sdk_path:
        lint_summary = run_final_lint(output_dir, sdk_path)
        result["lint"] = lint_summary
        print(f"    [build]  final lint: {lint_summary['error_count']} error(s)")
        result.update(_distribute(output_dir, sdk_path))
    else:
        result["lint"] = {"error_count": None}

    return {"status": "built", "output_dir": output_dir, **result}
