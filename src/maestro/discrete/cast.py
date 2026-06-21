"""cast — the character roster. Owns `premise`.

Contributes the SHARED premise floor (every discrete game has named characters). The story-spine
module (dialogue) layers the richer requirements — voice/history, 3+ cast, endings — on top, so a
point-and-click game whose premise is just NPCs isn't forced to carry a branching-cast contract.
"""

from maestro.modules import Module
from maestro.discrete.validators import v_premise, SKEL_PREMISE

MODULE = Module(
    id="cast",
    components=("premise",),
    schemas={"premise": v_premise},
    skeletons={"premise": SKEL_PREMISE},
    baseline={"premise": [
        {"type": "exists", "path": "premise.central_question"},
        {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]},
    ]},
)
