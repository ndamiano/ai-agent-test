"""Web projections — which discrete modules the browser runtime can render.

The web engine is a static runtime (runtime/engine.js) that interprets game.json live — there is
no per-game codegen, so a module's "projection" here is just a marker that the runtime knows how
to interpret that module's IR slice. scenes (playNode), world (runPnc), and card_play
(runMatch) are all handled by the runtime; registering them lets the compile dispatch's fail-fast
guard pass for a web build.
"""

from maestro.modules import register_projection

# The runtime is one interpreter; the value is a marker, not a per-module function.
_RUNTIME = "web-runtime"


def register() -> None:
    register_projection("web", "scenes", _RUNTIME)
    register_projection("web", "world", _RUNTIME)
    register_projection("web", "card_play", _RUNTIME)   # runMatch in engine.js
