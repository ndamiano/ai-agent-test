"""cast — the character roster. Owns `premise`.

Contributes the SHARED premise floor (every discrete game has named characters). The story-spine
module (dialogue) layers the richer requirements — voice/history, 3+ cast, endings — on top, so a
point-and-click game whose premise is just NPCs isn't forced to carry a branching-cast contract.
"""

from maestro.modules import Module
from maestro.discrete.validators import v_premise, SKEL_PREMISE

# What a downstream author actually steers by, per character: id/name + the voice anchors
# (voice + example_lines + temperament + drive). `history`/`competencies` are deep-background the
# scene writer rarely needs once story_state carries the established facts, and `color` is a UI hex
# — dropping them keeps the per-step premise focused without losing the voice the writing depends on.
_PREMISE_KEEP = ("id", "name", "voice", "temperament", "drive", "example_lines")


def _premise_view(c):
    return {
        "central_question": c.get("central_question"),
        "characters": [{k: ch[k] for k in _PREMISE_KEEP if ch.get(k) is not None}
                       for ch in c.get("characters", []) if isinstance(ch, dict)],
        "endings": c.get("endings"),
    }


MODULE = Module(
    id="cast",
    components=("premise",),
    schemas={"premise": v_premise},
    skeletons={"premise": SKEL_PREMISE},
    baseline={"premise": [
        {"type": "exists", "path": "premise.central_question"},
        {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]},
    ]},
    mode_tools=frozenset({"write_component", "update_scratchpad", "request_review"}),
    mode_prompt="mode_premise.txt",
    context_view=_premise_view,
)
