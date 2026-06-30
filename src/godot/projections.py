"""Godot projections — which discrete modules the GDScript runtime can render.

Like web, the Godot engine is one static interpreter (runtime/*.gd) that walks game.json — there
is no per-game codegen, so a module's "projection" here is just a marker that the runtime knows
how to interpret that module's IR slice. scenes (play_node) and world (run_place) are handled by
the runtime; registering them lets the compile dispatch's fail-fast guard pass for a Godot build.

card_play stays web-only (no projection here). combat is Godot-only — it has no renpy/web
projection, so a game that picks it routes here automatically (Module.engine_for); the runtime's
combat.gd plays the encounter IR the combat module emits on disk.
"""

from maestro.modules import register_projection

# The runtime is one interpreter; the value is a marker, not a per-module function.
_RUNTIME = "godot-runtime"


def register() -> None:
    register_projection("godot", "scenes", _RUNTIME)
    register_projection("godot", "world", _RUNTIME)
    register_projection("godot", "combat", _RUNTIME)
