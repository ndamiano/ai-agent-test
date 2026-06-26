"""Ren'Py projections — which discrete modules this engine can render, and how.

A module's schema is substrate-level (shared); its *projection* to engine output is per-engine.
Ren'Py renders the two realization modules via the existing IR→script compilers (ir_vn for a
`scenes` graph, ir_pnc for a `world`). Registering them here makes the compile dispatch's
fail-fast guard pass; a `projected` module with no entry here (e.g. card_play) makes a Ren'Py
build of a game that uses it fail with a clear message instead of silently dropping content.
"""

from maestro.modules import register_projection
from renpy.ir_vn import compile_vn
from renpy.ir_pnc import compile_pnc


def register() -> None:
    register_projection("renpy", "scenes", compile_vn)
    register_projection("renpy", "world", compile_pnc)
