"""story — the dramatic plan. Authors the `story` component.

The narrative layer: the central question the endings answer differently, a beat-sheet arc that
gives the script a global shape, and a planned path to each ending. Optional — a pure mechanic or
all-puzzle game composes no story.

Example games:
  - "a guilt-ridden student murders a pawnbroker and slowly unravels" — cast + story + scenes
  - "rival chefs in a failing restaurant, three ways the night ends"  — cast + story + scenes
"""

from typing import Dict, Optional

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
        if not isinstance(b, dict) or not b.get("id"):
            return f"story.beats[{i}] needs an 'id' (e.g. 'beat_01')"
    eps = c.get("ending_paths")
    if not isinstance(eps, list):
        return "story.ending_paths must be a list (one per ending)"
    for i, e in enumerate(eps):
        if not isinstance(e, dict) or not e.get("ending"):
            return f"story.ending_paths[{i}] needs an 'ending' (a story.endings id)"
    return None


SKEL_STORY = (
    '{\n'
    '  "central_question": "the dramatic question the endings answer differently",\n'
    '  "endings": [ {"id": "ending_<slug>", "description": "the concrete final scene — who does/says\n'
    '    what, in-world words; never an abstract label like \\"integration\\" or \\"closure\\" (the scene\n'
    '    author reads this verbatim and abstract words here become dialogue)"} ],\n'
    '  "beats": [\n'
    '    {"id": "beat_01", "summary": "what happens in this beat",\n'
    '     "type": "its register: bonding | comedy | friction | plot | character",\n'
    '     "purpose": "its dramatic job: setup | inciting | escalation | midpoint_turn | crisis | climax | resolution",\n'
    '     "tension": "the stakes dial — what the player worries about NOW; \\"none\\" is a real answer and most early beats carry it"}\n'
    '  ],\n'
    '  "ending_paths": [\n'
    '    {"ending": "<a story.endings id>", "earned_by": "the SPECIFIC beat + choice that earns it\n'
    '      (e.g. \\"the beat_04 choice to stay\\") — never a vague cause"}\n'
    '  ]\n'
    '}\n'
    '// The story is the ARC the scenes will realize — author it FROM the concept + cast.\n'
    '//   NOT scenes yet: the shape of the whole story.\n'
    '// beats: 5+ in dramatic ORDER — a rise toward the crisis, but BACK-LOADED: the early\n'
    '//   beats are allowed to just be these people together (bonding/comedy, tension "none");\n'
    '//   each beat does work no other beat does. summary = ONE committed event, never a menu\n'
    '//   of alternatives ("X, or maybe Y") — the scene author reads it as a literal brief.\n'
    '// endings: distinct outcomes the central_question resolves to.\n'
    '// ending_paths: ONE per endings id — plan how each different ending is EARNED, so branches\n'
    '//   diverge in meaning, not just in which scene plays.'
)


class Story(Module):
    id = "story"
    description = ("A dramatic plan: the central question, a beat-sheet arc, and distinct endings. "
                   "Include for story-forward games; it makes the cast richer and the script branch.")
    priority = 30
    component = "story"
    mode_prompt = "story_write.txt"
    mode_tools = frozenset({"write_component", "update_scratchpad", "request_review"})
    skeleton = SKEL_STORY
    schemas = {"story": v_story}
    skeletons = {"story": SKEL_STORY}

    checks = [
        Check("central_question", lambda chk, m, ctx: m.wrap(chk, checks.exists(
            ctx.artifact, "story.central_question"))),
        Check("min_endings", lambda chk, m, ctx: m.wrap(chk, checks.count(
            ctx.artifact, "story.endings", min=ctx.param("min_endings", 3)))),
        Check("distinct_endings", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "story.endings", key="id")), job="fix"),
        Check("min_beats", lambda chk, m, ctx: m.wrap(chk, checks.count(
            ctx.artifact, "story.beats", min=ctx.param("min_beats", 5)))),
        Check("beat_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "story.beats", fields=["id", "summary", "type", "purpose", "tension"]))),
        Check("distinct_beats", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "story.beats", key="id")), job="fix"),
        Check("ending_path_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "story.ending_paths", fields=["ending", "earned_by"]))),
        Check("endings_planned", lambda chk, m, ctx: m.wrap(chk, checks.refs_resolve(
            ctx.artifact, "story.endings", "story.ending_paths",
            from_key="id", to_key="ending")), job="fix"),
    ]

    def params(self) -> Dict:
        # Story-forward floors raised on the neighbours: a richer/larger cast, meatier branching
        # scenes, and a minimum spread of endings/beats. cast/scenes read these via param union.
        return {"min_characters": 2, "min_endings": 3, "min_beats": 5,
                "min_branches": 1, "each_node_min_lines": 6,
                "character_fields": ["voice", "temperament", "drive", "history",
                                     "competencies", "example_lines"]}

    def render_context(self, ctx: Dict) -> str:
        # The story is planned FROM the cast: full character cards (drives are what collide into
        # a plot), nothing else but the request frame.
        from maestro import context_render as cr
        from maestro.modules import cast
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += cast.character_cards(ctx.get("artifact") or {})
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)


MODULE = Story()
register_module(MODULE)
