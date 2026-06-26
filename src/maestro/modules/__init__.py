"""maestro.modules — the mechanic-module package.

Importing the package imports every module file, so each one's `register_module(...)` runs and the
MODULE_REGISTRY is populated. The human module is always present; `compose` force-includes it.
"""

# Core + foundations first (mechanic modules import from these).
from maestro.modules.module import (  # noqa: F401
    CorrectionPrompt,
    Error,
    ErrorType,
    Module,
    Preset,
    PRESETS,
    MODULE_REGISTRY,
    compose,
    projection_for,
    register_module,
    register_preset,
    register_projection,
    unprojectable,
)
from maestro.modules import views, checks, context  # noqa: F401

# Mechanic modules (registration side effects) — human is always present.
from maestro.modules import (  # noqa: F401
    human,
    assets,
    card_play,
    cast,
    dialogue,
    economy,
    goal,
    navigation,
    outline,
)

# Presets — the named module sets the classifier emits (`genre` keeps the preset name for the UI).
register_preset("vn", Preset(
    substrate="discrete",
    modules=("cast", "assets", "outline", "dialogue", "economy"),
    engine="renpy"))
register_preset("point_and_click", Preset(
    substrate="discrete",
    modules=("cast", "assets", "dialogue_npc", "navigation", "economy", "goal_flag"),
    engine="renpy"))
# card_ante is open-ended (play for ante indefinitely) — it composes NO goal module; "endless" is
# simply the absence of a win condition.
register_preset("card_ante", Preset(
    substrate="discrete",
    modules=("cast", "assets", "dialogue_npc", "navigation", "economy", "card_play"),
    engine="web"))
