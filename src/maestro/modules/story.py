"""story — the dramatic plan. Authors the `story` component.

The narrative layer: the central question the endings answer differently, a beat-sheet arc that
gives the script a global shape, and a planned path to each ending. Optional — a pure mechanic or
all-puzzle game composes no story.

Example games:
  - "a guilt-ridden student murders a pawnbroker and slowly unravels" — cast + story + scenes
  - "rival chefs in a failing restaurant, three ways the night ends"  — cast + story + scenes
"""

from typing import Dict, Optional

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.module import Check, Module, register_module


def story_block(artifact: Dict) -> list:
    """The dramatic plan as prompt context: question + endings (+ beat one-liners). What
    realization steps aim at."""
    story = artifact.get("story") or {}
    if not story.get("central_question"):
        return []
    out = ["", f"STORY — central question: {story['central_question']}"]
    for e in story.get("endings") or []:
        if isinstance(e, dict) and e.get("id"):
            out.append(f"  ending {e['id']}: {e.get('description', '')}")
    beats = [b for b in story.get("beats") or [] if isinstance(b, dict) and b.get("id")]
    if beats:
        out.append("  beats: " + " → ".join(b["id"] for b in beats))
    return out


def render_beat(b: Dict) -> str:
    """One story beat as a prompt line (the scene author's brief for a slot)."""
    tags = [t for t in (b.get("type"),
                        f'stake: {b["tension"]}' if b.get("tension") else None) if t]
    suffix = f' ({", ".join(tags)})' if tags else ""
    return f'{b.get("id")} — {b.get("summary", "")}{suffix}'


def beats_detail_block(artifact: Dict) -> list:
    """The arc SO FAR with full summaries — the author's context for the next beat/ending (the
    id-only story_block is for OTHER modules; here the author needs to see what each beat does)."""
    beats = [b for b in (artifact.get("story") or {}).get("beats", [])
             if isinstance(b, dict) and b.get("id")]
    if not beats:
        return []
    return ["", "BEATS SO FAR (the arc so far — continue the rise; your next beat does work NONE "
            "of these do):", *(f"  {render_beat(b)}" for b in beats)]


def endings_detail_block(artifact: Dict) -> list:
    endings = [e for e in (artifact.get("story") or {}).get("endings", [])
               if isinstance(e, dict) and e.get("id")]
    if not endings:
        return []
    return ["", "ENDINGS SO FAR (each answers the question a DIFFERENT way — yours is distinct):",
            *(f"  {e['id']}: {e.get('description', '')}" for e in endings)]


_BEAT_FIELDS = ("summary", "type", "purpose", "tension")


def v_beat_one(b: Dict) -> Optional[str]:
    """Structural gate for ONE beat — shared by add_beat and v_story."""
    if not isinstance(b, dict):
        return "a beat must be a JSON object"
    if not b.get("id"):
        return "a beat needs an 'id' (e.g. 'beat_01')"
    for f in _BEAT_FIELDS:
        if not b.get(f):
            return (f"beat is missing '{f}' — a beat needs summary (the scene author's whole brief), "
                    f"type (register), purpose (dramatic job), and tension (the stakes dial; 'none' "
                    f"is a real answer)")
    return None


def v_story(c: Dict) -> Optional[str]:
    if not c.get("central_question"):
        return "story.central_question is required (the dramatic question the endings answer)"
    endings = c.get("endings")
    if not isinstance(endings, list) or not endings:
        return "story.endings must be a non-empty list of {id, description}"
    for i, e in enumerate(endings):
        if not isinstance(e, dict) or not e.get("id"):
            return f"story.endings[{i}] needs an 'id' (e.g. 'ending_solitude')"
    beats = c.get("beats")
    if not isinstance(beats, list) or not beats:
        return "story.beats must be a non-empty list of beat objects"
    for i, b in enumerate(beats):
        err = v_beat_one(b)
        if err:
            return f"story.beats[{i}]: {err}"
    eps = c.get("ending_paths")
    if not isinstance(eps, list):
        return "story.ending_paths must be a list (one per ending)"
    for i, e in enumerate(eps):
        if not isinstance(e, dict) or not e.get("ending"):
            return f"story.ending_paths[{i}] needs an 'ending' (a story.endings id)"
    return None


SKEL_BEAT_ONE = (
    '// beat_id (the tool arg) is the id, e.g. "beat_03". `content` is this ONE beat:\n'
    '{\n'
    '  "summary": "1-2 sentences of concrete business — what physically happens, the specific\n'
    '    object/place/action in play, who does or says the key thing; the scene author\'s WHOLE\n'
    '    brief, never a headline. ONE committed event, never alternatives (\\"X, or maybe Y\\").",\n'
    '  "type": "its register: bonding | comedy | friction | plot | character",\n'
    '  "purpose": "its dramatic job: setup | inciting | escalation | midpoint_turn | crisis | climax | resolution",\n'
    '  "tension": "the stakes dial — what the player worries about NOW; \\"none\\" is a real answer and most early beats carry it"\n'
    '}\n'
    '// Place this beat correctly in the arc SO FAR — a rise toward the crisis, BACK-LOADED.\n'
    '//   Each beat does work no other beat does. The crisis/fork is the LAST beat.'
)

SKEL_ENDING_ONE = (
    '// add_ending(ending_id, description, earned_by):\n'
    '//   ending_id  = "ending_<slug>"\n'
    '//   description= the concrete final scene — who does/says what, in-world words; NEVER an\n'
    '//                abstract label like "integration"/"closure" (the scene author reads it verbatim).\n'
    '//   earned_by  = the SPECIFIC beat + choice that earns it, e.g. "the beat_04 choice to stay".'
)


def story_view(artifact: Dict) -> Dict:
    """The slot-guard's view for BOTH count targets — the beat/ending ids already authored. No
    open_slots: the ids are the model's to invent."""
    story = artifact.get("story") or {}
    return {"beat_ids": [b["id"] for b in story.get("beats", [])
                         if isinstance(b, dict) and b.get("id")],
            "ending_ids": [e["id"] for e in story.get("endings", [])
                           if isinstance(e, dict) and e.get("id")]}


def _one(_view) -> int:
    """Cap beats/endings at ONE author per step: unlike a cast, the arc is a SEQUENCE, so each beat
    must see the full prior arc (parallel siblings would each author blind to the others)."""
    return 1


# The post-authoring safety repairs (missing field, dup id, orphaned ending) round-trip through
# write_component: there is no per-beat / per-ending / per-path edit tool, and add_beat/add_ending
# refuse an existing id, so the fix reads the story then rewrites it with ONE change. The specific
# prompts below name the exact path and forbid a clobber-rewrite.
_REPAIR_TOOLS = frozenset({"read_component", "write_component", "request_review"})
_CQ_TOOLS = frozenset({"set_central_question", "read_component", "request_review"})
_BEAT_TOOLS = frozenset({"add_beat", "read_component", "request_review"})
_ENDING_TOOLS = frozenset({"add_ending", "read_component", "request_review"})
_BEAT_GUARD = {"count_tool": "add_beat", "id_key": "beat_id", "id_list_key": "beat_ids",
               "noun": "beat", "cap": _one}
_ENDING_GUARD = {"count_tool": "add_ending", "id_key": "ending_id", "id_list_key": "ending_ids",
                 "noun": "ending", "cap": _one}


def _d_min_beats(chk, m, ctx):
    need = ctx.param("min_beats", 5) - checks.length(ctx.artifact, "story.beats")
    return checks.slot_errors(need, type=chk.tier, code=chk.code,
                              component="story", noun="beat") if need > 0 else []


def _d_min_endings(chk, m, ctx):
    need = ctx.param("min_endings", 3) - checks.length(ctx.artifact, "story.endings")
    return checks.slot_errors(need, type=chk.tier, code=chk.code,
                              component="story", noun="ending") if need > 0 else []


class Story(Module):
    id = "story"
    description = ("A dramatic plan: the central question, a beat-sheet arc, and distinct endings. "
                   "REQUIRED whenever the story you wrote has a plot arc, named endings, or "
                   "characters whose conversations matter — without it the script has no "
                   "narrative floor and dialogue may never be authored. Skip only for a pure "
                   "puzzle-box with no story to tell.")
    priority = 30
    component = "story"
    mode_prompt = "story_write.txt"
    mode_tools = frozenset({"write_component", "read_component", "request_review"})
    skeleton = SKEL_BEAT_ONE
    schemas = {"story": v_story}
    skeletons = {"story": SKEL_BEAT_ONE}
    projector = staticmethod(story_view)

    # The story is authored piece by piece: the central_question first (blocking — the spine the
    # rest hangs from), then the beat-sheet ONE beat at a time (each seeing the full arc so far),
    # then the endings ONE at a time (each authored WITH how it's earned, so ending_paths is
    # satisfied by construction). The field/distinct/path checks are cheap safety — a per-item add
    # validates structure + the guard blocks dup ids, so they rarely fire; when they do, the fix
    # rewrites the whole story.
    checks = [
        Check("central_question", lambda chk, m, ctx: m.wrap(chk, checks.exists(
            ctx.artifact, "story.central_question")),
            blocking=True, tools=_CQ_TOOLS, prompt="story_question.txt", skeleton=""),
        Check("min_beats", _d_min_beats, tools=_BEAT_TOOLS, guard=_BEAT_GUARD,
              prompt="story_beats_add.txt", skeleton=SKEL_BEAT_ONE),
        Check("beat_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "story.beats", fields=["id", "summary", "type", "purpose", "tension"])),
            context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="story_field_patch.txt", skeleton=""),
        Check("distinct_beats", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "story.beats", key="id")), job="fix", context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="story_rename_duplicate.txt", skeleton=""),
        Check("min_endings", _d_min_endings, tools=_ENDING_TOOLS, guard=_ENDING_GUARD,
              prompt="story_endings_add.txt", skeleton=SKEL_ENDING_ONE),
        Check("distinct_endings", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "story.endings", key="id")), job="fix", context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="story_rename_duplicate.txt", skeleton=""),
        Check("ending_path_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "story.ending_paths", fields=["ending", "earned_by"])),
            context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="story_field_patch.txt", skeleton=""),
        Check("endings_planned", lambda chk, m, ctx: m.wrap(chk, checks.refs_resolve(
            ctx.artifact, "story.endings", "story.ending_paths",
            from_key="id", to_key="ending")), job="fix", context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="story_endings_plan_fix.txt", skeleton=""),
    ]

    def params(self) -> Dict:
        # Story-forward floors raised on the neighbours: a richer/larger cast, meatier branching
        # scenes, and a minimum spread of endings/beats. cast/scenes read these via param union.
        return {"min_characters": 2, "min_endings": 3, "min_beats": 5,
                "min_branches": 1, "each_node_min_lines": 6,
                "character_fields": ["voice", "temperament", "drive", "history",
                                     "competencies", "example_lines"]}

    def render_context(self, ctx: Dict) -> str:
        # The story is planned FROM the premise + cast (drives collide into a plot) and the story SO
        # FAR: the central question, the arc's beats with full summaries, the endings already
        # written — so the next item continues the arc and stays distinct.
        from maestro.modules import cast
        art = ctx.get("artifact") or {}
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        lines += cast.character_cards(art)
        cq = (art.get("story") or {}).get("central_question")
        if cq:
            lines += ["", f"CENTRAL QUESTION: {cq}"]
        lines += beats_detail_block(art)
        lines += endings_detail_block(art)
        lines += cr.tail_block(ctx)
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> list:
        # Structural repair on the story component — the beats, endings, and planned paths as ids +
        # summaries so a dedup/field fix can see exactly what to merge or complete.
        art = artifact
        paths = [p for p in (art.get("story") or {}).get("ending_paths", [])
                 if isinstance(p, dict)]
        out = beats_detail_block(art) + endings_detail_block(art)
        if paths:
            out += ["", "ENDING PATHS (ending → earned_by):",
                    *(f"  {p.get('ending')} ← {p.get('earned_by')}" for p in paths)]
        return out


MODULE = Story()
register_module(MODULE)
