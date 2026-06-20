"""Engine dispatch — map a spec's `engine` tag to its compile entry point.

The maestro core stays engine-neutral: it builds the genre-decomposed IR components and
calls `compile_for(spec.engine)(run_dir, distribute=...)`. Every backend honours the same
contract (compile_renpy / compile_web): a `(working_dir, distribute=bool) -> Dict` returning
`{ok, reason, lint_error_count, project_dir, ...}`. Adding an engine = a new entry here plus
its `compile_*` in its own package; no core branching.
"""

from typing import Callable, Dict


def compile_for(engine: str) -> Callable[..., Dict]:
    if engine == "web":
        from web.compiler import compile_web
        return compile_web
    from renpy.compiler import compile_renpy
    return compile_renpy
