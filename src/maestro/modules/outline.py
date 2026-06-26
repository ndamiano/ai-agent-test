"""outline — the dramatic arc of a visual novel. Authors `outline`.

A beat-sheet stage between premise and nodes: a small frozen arc (logline + ordered beats + a
planned path to each ending) the node sub-loop then realizes, so the script has a global shape
instead of being improvised scene-by-scene. VN-only (pairs with the `dialogue` spine).
"""

from typing import Dict, List, Optional, Tuple

from maestro.modules import checks
from maestro.modules.module import Error, ErrorType, Module, register_module


def v_outline(c: Dict) -> Optional[str]:
    if not c.get("logline"):
        return "outline.logline is required (one sentence naming the dramatic spine)"
    beats = c.get("beats")
    if not isinstance(beats, list) or not beats:
        return "outline.beats must be a non-empty list of beat objects"
    for i, b in enumerate(beats):
        if not isinstance(b, dict) or not b.get("id"):
            return f"outline.beats[{i}] needs an 'id' (e.g. 'beat_01')"
    eps = c.get("ending_paths")
    if not isinstance(eps, list):
        return "outline.ending_paths must be a list (one per premise ending)"
    for i, e in enumerate(eps):
        if not isinstance(e, dict) or not e.get("ending"):
            return f"outline.ending_paths[{i}] needs an 'ending' (a premise.endings id)"
    return None


SKEL_OUTLINE = (
    '{\n'
    '  "logline": "one sentence naming the dramatic spine — who wants what, what stands in the way",\n'
    '  "beats": [\n'
    '    {"id": "beat_01", "summary": "what happens in this beat",\n'
    '     "purpose": "its dramatic job: setup | inciting | escalation | midpoint_turn | crisis | climax | resolution",\n'
    '     "tension": "the question or stake this beat presses — what the player worries about now"}\n'
    '  ],\n'
    '  "ending_paths": [\n'
    '    {"ending": "<a premise.endings id>", "earned_by": "the choices/turns along the way that make this ending land"}\n'
    '  ]\n'
    '}\n'
    '// The outline is the ARC the scenes will realize — author it FROM the premise (its\n'
    '//   central_question, cast, endings). NOT scenes yet: the shape of the whole story.\n'
    '// beats: 5+ in dramatic ORDER — a real rise (setup -> escalation -> a midpoint that\n'
    '//   reframes -> crisis/climax -> resolution); each beat does work no other beat does.\n'
    '// ending_paths: ONE per premise.endings id — plan how each different ending is EARNED,\n'
    '//   so branches diverge in meaning, not just in which scene plays.'
)


class Outline(Module):
    id = "outline"
    description = ("A beat-sheet arc planned before scenes are written, giving the script a global "
                   "shape. Pairs with the visual-novel spine for longer, well-paced stories.")
    requires = ("dialogue",)
    priority = 30
    component = "outline"
    mode_prompt = "mode_outline.txt"
    mode_tools = frozenset({"write_component", "update_scratchpad", "request_review"})
    skeleton = SKEL_OUTLINE
    schemas = {"outline": v_outline}
    skeletons = {"outline": SKEL_OUTLINE}

    def params(self) -> Dict:
        return {"min_beats": 5}

    def affected_components(self) -> Tuple[str, ...]:
        return ("outline",)

    def get_errors(self, context) -> List[Error]:
        art = context.artifact
        errs: List[Error] = []

        def add(result, code, tier):
            e = checks.as_error(result, type=tier, code=code, component="outline")
            if e:
                errs.append(e)

        B, F = ErrorType.BUILD, ErrorType.FIX
        add(checks.exists(art, "outline.logline"), "logline", B)
        add(checks.count(art, "outline.beats", min=context.param("min_beats", 5)), "min_beats", B)
        add(checks.each_has(art, "outline.beats", fields=["id", "summary", "purpose", "tension"]),
            "beat_fields", B)
        add(checks.distinct(art, "outline.beats", key="id"), "distinct_beats", F)
        add(checks.each_has(art, "outline.ending_paths", fields=["ending", "earned_by"]),
            "ending_path_fields", B)
        add(checks.refs_resolve(art, "premise.endings", "outline.ending_paths",
                                from_key="id", to_key="ending"), "endings_planned", F)
        return errs

    def context_view(self, c: Dict) -> Dict:
        return {k: v for k, v in c.items() if k != "beats"}


MODULE = Outline()
register_module(MODULE)
