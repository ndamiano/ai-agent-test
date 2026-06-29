"""Engine dispatch — map a spec's `engine` tag to its compile entry point.

The maestro core stays engine-neutral: it builds the genre-decomposed IR components and
calls `compile_for(spec.engine)(run_dir, distribute=...)`. Every backend honours the same
contract (compile_renpy / compile_web): a `(working_dir, distribute=bool) -> Dict` returning
`{ok, reason, lint_error_count, project_dir, ...}`. Adding an engine = a new entry here plus
its `compile_*` in its own package; no core branching.
"""

from typing import Callable, Dict, Tuple

# The engine tags the core knows about, in preference order (the engine a spec gets is the first
# one that can project all its modules — see maestro.modules.engine_for).
ENGINE_TAGS: Tuple[str, ...] = ("renpy", "web", "godot")

_projections_registered = False


def ensure_projections_registered() -> None:
    """Populate the (engine, module) projection registry from every backend. Idempotent. Compile
    paths register their own engine, but engine selection at propose time needs all of them, so
    this is the one place the core eagerly pulls them in."""
    global _projections_registered
    if _projections_registered:
        return
    from renpy.projections import register as register_renpy
    from web.projections import register as register_web
    from godot.projections import register as register_godot
    register_renpy()
    register_web()
    register_godot()
    _projections_registered = True


def compile_for(engine: str) -> Callable[..., Dict]:
    if engine == "web":
        from web.compiler import compile_web
        return compile_web
    if engine == "godot":
        from godot.compiler import compile_godot
        return compile_godot
    from renpy.compiler import compile_renpy
    return compile_renpy
