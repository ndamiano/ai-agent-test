"""Godot projections — which discrete modules the GDScript runtime can render.

Like web, the Godot engine is one static interpreter (runtime/*.gd) that walks game.json — there
is no per-game codegen, so a module's "projection" here is just a marker that the runtime knows
how to interpret that module's IR slice. scenes (play_node) and world (run_place) are handled by
the runtime; registering them lets the compile dispatch's fail-fast guard pass for a Godot build.

card_play stays web-only (no projection here). The combat authoring module is phase 2; the
runtime already interprets encounter IR, but no module emits it on disk yet.
"""

from maestro.modules import register_projection

# The runtime is one interpreter; the value is a marker, not a per-module function.
_RUNTIME = "godot-runtime"


def register() -> None:
    register_projection("godot", "scenes", _RUNTIME)
    register_projection("godot", "world", _RUNTIME)
