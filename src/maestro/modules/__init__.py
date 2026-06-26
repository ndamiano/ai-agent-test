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
    MODULE_REGISTRY,
    compose,
    engine_for,
    expand_modules,
    module_conflict,
    projection_for,
    register_module,
    register_projection,
    resolve_modules,
    selectable_catalog,
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
