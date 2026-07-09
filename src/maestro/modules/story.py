"""story — the dramatic plan. Authors the `story` component.

The narrative layer: the central question the endings answer differently, a beat-sheet arc that
gives the script a global shape, and a planned path to each ending. Optional — a pure mechanic or
all-puzzle game composes no story.

Example games:
  - "a guilt-ridden student murders a pawnbroker and slowly unravels" — cast + story + scenes
  - "rival chefs in a failing restaurant, three ways the night ends"  — cast + story + scenes
"""

import re
from typing import Dict, Optional

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.module import Check, Module, register_module
from maestro.modules.views import MAX_MENU_CHOICES


def render_beat(b: Dict) -> str:
    """One story beat as a prompt line (the scene author's brief for a slot)."""
    tags = [t for t in (b.get("type"),
                        f'stake: {b["tension"]}' if b.get("tension") else None) if t]
    suffix = f' ({", ".join(tags)})' if tags else ""
    return f'{b.get("id")} — {b.get("summary", "")}{suffix}'


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


_TERMINUS_TYPES = ("game_end", "handoff", "merge")


def v_terminus(t: Dict) -> Optional[str]:
    """A storyline ends exactly one of three ways. The TYPE is code-set from the storyline's kind
    and its branch's return — the model authors the payload (an ending's final scene / a merge
    target), never the type in isolation."""
    if not isinstance(t, dict) or t.get("type") not in _TERMINUS_TYPES:
        return f"terminus.type must be one of {_TERMINUS_TYPES}"
    if t["type"] == "game_end":
        end = t.get("ending")
        if not isinstance(end, dict) or not end.get("id") or not end.get("description"):
            return ("a game_end terminus needs ending:{id, description} — description is the "
                    "concrete final scene, never an abstract label like 'closure'")
    if t["type"] == "merge" and not (t.get("into") and t.get("at_beat")):
        return "a merge terminus needs {into: <storyline_id>, at_beat: <beat_id>}"
    return None


def v_branch(b: Dict) -> Optional[str]:
    """A branch point on the SOURCE storyline: at from_beat, a choice spins off `spinoff`, resuming
    at return_to_beat (null ⇒ spinoff is terminal). requires is the optional state gate."""
    if not isinstance(b, dict):
        return "a branch must be a JSON object"
    for f in ("id", "from_beat", "choice", "spinoff"):
        if not b.get(f):
            return f"branch is missing '{f}' — needs id, from_beat, choice (what the player picks), spinoff (the storyline it starts)"
    return None


def v_storyline(s: Dict, require_beats: bool = True) -> Optional[str]:
    """One linear storyline: its shell (kind, premise, branches, terminus) plus — once filled — a
    sequence of beats. `require_beats=False` at shell-creation time (add_storyline); the final
    v_story gate requires them."""
    if not isinstance(s, dict):
        return "a storyline must be a JSON object"
    if not s.get("id"):
        return "a storyline needs an 'id' (e.g. 'sl_main')"
    if s.get("kind") not in ("main", "side"):
        return f"storyline {s.get('id')!r} needs kind 'main' or 'side'"
    if not s.get("premise"):
        return f"storyline {s['id']!r} needs a 'premise' (one line: what this line is about)"
    beats = s.get("beats") or []
    if require_beats and not beats:
        return f"storyline {s['id']!r}: beats must be a non-empty list"
    if not isinstance(beats, list):
        return f"storyline {s['id']!r}: beats must be a list"
    seen = set()
    for i, b in enumerate(beats):
        err = v_beat_one(b)
        if err:
            return f"storyline {s['id']!r} beats[{i}]: {err}"
        if b["id"] in seen:
            return f"storyline {s['id']!r}: duplicate beat id {b['id']!r} (ids unique within a storyline)"
        seen.add(b["id"])
    by_beat: Dict = {}
    for i, br in enumerate(s.get("branches") or []):
        err = v_branch(br)
        if err:
            return f"storyline {s['id']!r} branches[{i}]: {err}"
        by_beat.setdefault(br["from_beat"], []).append(br)
    # The scene at a branch beat realizes as a menu of [continue] + its branches, and a menu
    # holds MAX_MENU_CHOICES — so the branch count is bounded HERE, where the model can still
    # move a fork, not at scene-write time where the derived menu would be unsatisfiable.
    for bid, brs in by_beat.items():
        if len(brs) > MAX_MENU_CHOICES - 1:
            return (f"storyline {s['id']!r}: {len(brs)} branches fork from {bid!r} — at most "
                    f"{MAX_MENU_CHOICES - 1} per beat (the scene's menu holds {MAX_MENU_CHOICES} "
                    f"choices and 'continue' takes one). Move a fork to a different beat, or cut "
                    f"one — depth over width.")
        texts = [str(b.get("choice", "")).strip().lower() for b in brs]
        if len(texts) != len(set(texts)):
            return (f"storyline {s['id']!r}: two branches at {bid!r} share the same choice text "
                    f"{brs[0].get('choice')!r} — each choice is what the player PICKS, a distinct "
                    f"in-world action (e.g. 'Jax stays behind' vs 'Mara stays behind'), never the "
                    f"question itself repeated.")
    err = v_terminus(s.get("terminus") or {})
    if err:
        return f"storyline {s['id']!r} terminus: {err}"
    return None


def v_spinoff_terminus(story: Dict, s: Dict) -> Optional[str]:
    """A spun-off line's terminus must agree with the branch that starts it: a set return_to_beat
    promised a handoff; a null one made the line terminal (game_end or merge). Enforced at
    add_storyline time (the branch is already on disk) and re-swept by v_story."""
    br = next((b for other in story.get("storylines") or [] if isinstance(other, dict)
               for b in other.get("branches") or []
               if isinstance(b, dict) and b.get("spinoff") == s.get("id")), None)
    if br is None:
        return None
    t = (s.get("terminus") or {}).get("type")
    if br.get("return_to_beat") and t != "handoff":
        return (f"storyline {s['id']!r}: its branch {br.get('id')!r} sets return_to_beat="
                f"{br['return_to_beat']!r}, so this line MUST end {{\"type\": \"handoff\"}} — "
                f"control returns to the source line; it cannot end the game or merge.")
    if not br.get("return_to_beat") and t == "handoff":
        return (f"storyline {s['id']!r}: its branch {br.get('id')!r} left return_to_beat null, so "
                f"this line is TERMINAL — end it with its own game_end ending or a merge into "
                f"another line, never handoff (there is nowhere to hand back to).")
    return None


def v_story(c: Dict) -> Optional[str]:
    spine = c.get("spine")
    if not isinstance(spine, dict) or not spine.get("theme") or not spine.get("tone"):
        return "story.spine must be {theme, tone, trope?} — theme and tone are required (the spine, replacing a central question)"
    storylines = c.get("storylines")
    if not isinstance(storylines, list) or not storylines:
        return "story.storylines must be a non-empty list of storyline objects"
    ids = set()
    mains = 0
    game_ends = 0
    for i, s in enumerate(storylines):
        err = v_storyline(s) or v_spinoff_terminus(c, s)
        if err:
            return f"story.storylines[{i}]: {err}"
        if s["id"] in ids:
            return f"story.storylines: duplicate storyline id {s['id']!r}"
        ids.add(s["id"])
        if s.get("kind") == "main":
            mains += 1
        if (s.get("terminus") or {}).get("type") == "game_end":
            game_ends += 1
    if mains != 1:
        return f"story needs exactly one kind='main' storyline (found {mains})"
    if game_ends < 1:
        return "story needs at least one storyline with a game_end terminus (the game must be completable)"
    start = c.get("start_storyline")
    if start not in ids:
        return f"story.start_storyline must name a declared storyline (got {start!r})"
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

SKEL_STORYLINE_ONE = (
    '// storyline_id (the tool arg) is the id, e.g. "sl_main" / "sl_cover_vale". `content` is\n'
    '// the SHELL of this ONE linear storyline (its beats are filled in later, one at a time):\n'
    '{\n'
    '  "kind": "main | side",   // exactly one storyline is "main"\n'
    '  "premise": "one line: what THIS linear line is about",\n'
    '  "target_beats": 8,        // how long this line runs (>= the floor); a side quest is short\n'
    '  "branches": [             // OPTIONAL fork points where this line spins off another line\n'
    '                             // (at most 2 per beat; each choice distinct in-world text)\n'
    '    {"id": "br_x", "from_beat": "beat_03", "choice": "what the player picks",\n'
    '     "spinoff": "sl_other", "return_to_beat": "beat_04",  // null => the spinoff is terminal\n'
    '     "requires": null}      // OPTIONAL state gate, e.g. {"flag": "trusts_vale"}\n'
    '  ],\n'
    '  "terminus": {"type": "game_end", "ending": {"id": "ending_caught",\n'
    '     "description": "the concrete final scene, in-world words; NEVER an abstract label"}}\n'
    '     // or {"type":"handoff"} (a side line that hands back) or {"type":"merge","into":"sl_x","at_beat":"beat_y"}\n'
    '}\n'
    '// The TYPE of terminus is yours to pick for the STORY, but a side line that a branch resumes\n'
    '//   must be "handoff"; a main line ends the game with "game_end".'
)


def spine_block(artifact: Dict) -> list:
    spine = (artifact.get("story") or {}).get("spine") or {}
    if not spine.get("theme"):
        return []
    trope = f' | trope: {spine["trope"]}' if spine.get("trope") else ""
    return ["", f"SPINE — theme: {spine.get('theme','')} | tone: {spine.get('tone','')}{trope}"]


def storylines(artifact: Dict) -> list:
    return [s for s in (artifact.get("story") or {}).get("storylines", [])
            if isinstance(s, dict) and s.get("id")]


def _storyline_line(s: Dict) -> str:
    term = (s.get("terminus") or {}).get("type", "?")
    nb, tb = len(s.get("beats") or []), s.get("target_beats") or "?"
    return f"  {s['id']} ({s.get('kind','?')}, {nb}/{tb} beats, ends: {term}): {s.get('premise','')}"


def storylines_block(artifact: Dict) -> list:
    """The storyline graph so far — what the next storyline/beat continues + stays distinct from."""
    sls = storylines(artifact)
    if not sls:
        return []
    out = ["", "STORYLINES SO FAR (each a linear line; yours does work none of these do):"]
    for s in sls:
        out.append(_storyline_line(s))
        for b in s.get("beats") or []:
            out.append(f"      {render_beat(b)}")
        for br in s.get("branches") or []:
            out.append(f"      ↳ branch at {br.get('from_beat')}: '{br.get('choice')}' → {br.get('spinoff')}")
    return out


def story_block(artifact: Dict) -> list:
    """Id-only spine + storyline index for OTHER modules' prompts."""
    story = artifact.get("story") or {}
    spine = story.get("spine") or {}
    if not spine.get("theme"):
        return []
    out = ["", f"STORY — theme: {spine.get('theme','')} | tone: {spine.get('tone','')}"]
    for s in storylines(artifact):
        out.append(_storyline_line(s))
    return out


def story_view(artifact: Dict) -> Dict:
    """The slot-guard's view: storyline ids + the flat beat-id list (beats are numbered globally so
    the guard's no-overwrite works across lines; ids are still unique within a storyline)."""
    sls = storylines(artifact)
    return {"storyline_ids": [s["id"] for s in sls],
            "beat_ids": [b["id"] for s in sls for b in s.get("beats") or []
                         if isinstance(b, dict) and b.get("id")]}


def _one(_view) -> int:
    """Cap beats/storylines at ONE author per step — a storyline's arc is a SEQUENCE (each beat
    sees the full prior arc), and a spun-off storyline is authored with its branch point in view."""
    return 1


_REPAIR_TOOLS = frozenset({"read_component", "write_component", "request_review"})
_SPINE_TOOLS = frozenset({"set_spine", "read_component", "request_review"})
_STORYLINE_TOOLS = frozenset({"add_storyline", "read_component", "request_review"})
_BEAT_TOOLS = frozenset({"add_beat", "finish_storyline", "read_component", "request_review"})
def _fill_beat_id(view, _assigned, args):
    """Code-fill the beat id: next free global beat_NN. Node ids are derived from beat ids, so
    beat ids must be globally unique — a model-picked id is the surface collisions live on
    (observed live: a side line's natural 'beat_01' spins forever against the flat guard set)."""
    taken = view.get("beat_ids") or []
    nums = [int(m.group(1)) for b in taken for m in [re.match(r"beat_(\d+)$", b)] if m]
    args = dict(args or {})
    args["beat_id"] = f"beat_{(max(nums) + 1 if nums else 1):02d}"
    return args


_STORYLINE_GUARD = {"count_tool": "add_storyline", "id_key": "storyline_id",
                    "id_list_key": "storyline_ids", "noun": "storyline", "cap": _one}
_BEAT_GUARD = {"count_tool": "add_beat", "id_key": "beat_id",
               "id_list_key": "beat_ids", "noun": "beat", "cap": _one,
               "prepare": _fill_beat_id}


def _declared_ids(ctx) -> set:
    return {s["id"] for s in storylines(ctx.artifact)}


def _d_main_storyline(chk, m, ctx):
    """Bootstrap: with no storylines yet, author the ONE main line first (its shell — premise,
    terminus, branches, length). Beats + spun-off lines follow."""
    if storylines(ctx.artifact):
        return []
    from maestro.modules.module import Error
    return [Error(type=chk.tier, code=chk.code, component="story", path="sl_main", ref="sl_main",
                  message="author the MAIN storyline first with add_storyline (kind='main') — its "
                          "premise, how it ends (a game_end terminus), any branch points, and its length.")]


def _d_demanded_storylines(chk, m, ctx):
    """Demand-driven (mirror inventory): every storyline a branch spins off must be authored. One
    add_storyline job per referenced-but-undeclared spinoff, keyed on the real id."""
    from maestro.modules.module import Error
    declared = _declared_ids(ctx)
    if len(declared) >= ctx.param("max_storylines", 8):
        return []
    spun = {br.get("spinoff") for s in storylines(ctx.artifact)
            for br in (s.get("branches") or []) if br.get("spinoff")}
    return [Error(type=chk.tier, code=chk.code, component="story", path=sid, ref=sid,
                  message=(f"storyline '{sid}' is spun off by a branch but not authored — add it with "
                           f"add_storyline (a side line hands back with a 'handoff' terminus)."))
            for sid in sorted(spun - declared)]


def _d_storyline_beats(chk, m, ctx):
    """Per-storyline beat floor + finished-tool: for each storyline, fan a shortfall to target_beats
    (>= floor) into one add_beat job each — unless the author has declared it done at/above the floor."""
    from maestro.modules.module import Error
    floor = ctx.param("min_beats_floor", 3)
    out = []
    for s in storylines(ctx.artifact):
        have = len(s.get("beats") or [])
        if s.get("done") and have >= floor:
            continue
        target = max(floor, s.get("target_beats") or floor)
        for k in range(have, target):
            out.append(Error(type=chk.tier, code=chk.code, component="story",
                             path=f"{s['id']}#{k + 1:03d}", ref=s["id"],
                             message=(f"storyline '{s['id']}' needs beat {k + 1} of {target} — add it "
                                      f"with add_beat('{s['id']}', ...); or finish_storyline('{s['id']}') "
                                      f"if the line is complete (>= {floor} beats).")))
    return out


class Story(Module):
    id = "story"
    layer = "engine"
    description = ("A dramatic plan: a theme+tone spine and a graph of linear storylines (a main "
                   "line that ends the game, plus optional side lines it branches into). REQUIRED "
                   "whenever the story you wrote has a plot, named outcomes, or characters whose "
                   "conversations matter. Skip only for a pure puzzle-box with no story to tell.")
    priority = 30
    component = "story"
    mode_prompt = "story_storyline_add.txt"
    mode_tools = frozenset({"write_component", "read_component", "request_review"})
    skeleton = SKEL_STORYLINE_ONE
    schemas = {"story": v_story}
    skeletons = {"story": SKEL_STORYLINE_ONE}
    projector = staticmethod(story_view)

    # Authored piece by piece: the spine first (blocking — theme+tone the rest hangs from), then the
    # MAIN storyline shell, then each storyline's beats ONE at a time (each seeing its arc so far),
    # then any branch-demanded side storylines (demand-driven, mirror inventory). The field/distinct
    # checks are cheap post-authoring safety.
    checks = [
        Check("spine", lambda chk, m, ctx: m.wrap(chk, checks.exists(ctx.artifact, "story.spine")),
              blocking=True, tools=_SPINE_TOOLS, prompt="story_spine.txt", skeleton=""),
        Check("main_storyline", _d_main_storyline, tools=_STORYLINE_TOOLS, guard=_STORYLINE_GUARD,
              prompt="story_storyline_add.txt", skeleton=SKEL_STORYLINE_ONE),
        Check("storyline_beats", _d_storyline_beats, tools=_BEAT_TOOLS, guard=_BEAT_GUARD,
              prompt="story_beats_add.txt", skeleton=SKEL_BEAT_ONE),
        Check("demanded_storylines", _d_demanded_storylines, tools=_STORYLINE_TOOLS,
              guard=_STORYLINE_GUARD, when_clean=True,
              prompt="story_storyline_add.txt", skeleton=SKEL_STORYLINE_ONE),
        Check("beat_fields", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "story.storylines", fields=["id", "kind", "premise"])),
            context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="story_field_patch.txt", skeleton=""),
        Check("distinct_storylines", lambda chk, m, ctx: m.wrap(chk, checks.distinct(
            ctx.artifact, "story.storylines", key="id")), job="fix", context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="story_rename_duplicate.txt", skeleton=""),
    ]

    def params(self) -> Dict:
        # Story-forward floors raised on the neighbours. Per-storyline beat FLOOR (a line may run
        # longer via target_beats); max_storylines caps runaway spinning. No min_endings — endings
        # are terminal storylines, sized by the story.
        return {"min_characters": 2, "min_beats_floor": 3, "max_storylines": 8,
                "each_node_min_lines": 6,
                "character_fields": ["voice", "temperament", "drive", "history",
                                     "competencies", "example_lines"]}

    def render_context(self, ctx: Dict) -> str:
        # Planned FROM the premise + cast + the spine + the storyline graph so far. For a spun-off
        # storyline, the target carries the branch point that demanded it (via cr.target_block).
        from maestro.modules import cast
        art = ctx.get("artifact") or {}
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        lines += cast.character_cards(art)
        lines += spine_block(art)
        lines += storylines_block(art)
        lines += cr.tail_block(ctx)
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> list:
        return spine_block(artifact) + storylines_block(artifact)


MODULE = Story()
register_module(MODULE)
