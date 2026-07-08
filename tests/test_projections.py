"""The `(engine, module_id)` projection registry and its fail-fast contract.

A module's checks are engine-neutral; its RENDER is per-engine, registered as a `(engine, module_id)`
projection. Two invariants ride on this registry: (1) engine SELECTION — `engine_for` picks the first
engine that can project the whole module set, which is why a `combat` game routes to Godot and
everything else to Ren'Py; (2) fail-fast — a `projected` module with no projection for the chosen
engine must be reported by `unprojectable` so the compile refuses rather than silently dropping
content. These tests pin both against the REAL backend registrations.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from maestro.engines import ensure_projections_registered
from maestro.modules import engine_for, projection_for, unprojectable


@pytest.fixture(autouse=True)
def _registered():
    # WHY: every test asserts against real backend registrations; ensure_projections_registered is
    # idempotent, so calling it per test is safe and mirrors how engine_for warms the registry.
    ensure_projections_registered()


# ── the registered pairs resolve ──────────────────────────────────────────────

@pytest.mark.parametrize("engine,module_id", [
    ("renpy", "scenes"),   # VN graph -> ir_vn
    ("renpy", "world"),    # point-and-click -> ir_pnc
    ("godot", "scenes"),   # runtime interprets play_node
    ("godot", "world"),    # runtime interprets run_place
    ("godot", "combat"),   # combat is Godot-only, plays via combat.gd
])
def test_registered_projection_resolves(engine, module_id):
    # WHY: after register(), each expected pair must have a projection entry — a missing one would
    # make an otherwise-buildable game fail the compile's fail-fast guard.
    assert projection_for(engine, module_id) is not None


def test_combat_has_no_renpy_projection():
    # WHY: combat's whole reason to route to Godot is that Ren'Py cannot render it — the registry must
    # have NO (renpy, combat) entry, which is what drives engine_for to pick Godot for a combat game.
    assert projection_for("renpy", "combat") is None


# ── engine_for: the set decides the engine ────────────────────────────────────

@pytest.mark.parametrize("modules,expected", [
    (["scenes", "cast"], "renpy"),          # VN bundle -> Ren'Py (the preferred engine)
    (["world"], "renpy"),                    # point-and-click still renders in Ren'Py
    (["combat"], "godot"),                   # combat has no renpy projection -> Godot
    (["scenes", "world", "combat"], "godot"),  # any combat in the set forces Godot
])
def test_engine_for_picks_the_first_engine_that_projects_the_whole_set(modules, expected):
    # WHY: engine_for returns the first ENGINE_TAGS engine with no unprojectable module — Ren'Py is
    # preferred, so only a module Ren'Py can't render (combat) tips the whole game to Godot.
    assert engine_for(modules) == expected


def test_engine_for_ignores_unknown_module_ids():
    # WHY: engine_for only considers registered `projected` modules; an unknown id can't make a set
    # unprojectable, so a set of pure content modules stays on the preferred engine.
    assert engine_for(["cast", "story", "inventory"]) == "renpy"


# ── unprojectable: the fail-fast driver ───────────────────────────────────────

def test_unprojectable_flags_combat_on_renpy():
    # WHY: this is the exact signal the compile dispatch (renpy/ir_compiler.py) reads to refuse a
    # build — combat in a Ren'Py set must come back as unprojectable so the compile fails fast with a
    # clear reason instead of silently dropping the encounters.
    assert unprojectable("renpy", ["scenes", "combat"]) == ["combat"]


def test_unprojectable_clean_for_godot():
    # WHY: Godot registers combat, so the same set is fully projectable there — no fail-fast.
    assert unprojectable("godot", ["scenes", "combat"]) == []


def test_unprojectable_ignores_nonprojected_modules():
    # WHY: only `projected` modules need an engine renderer; content modules (cast/story) are never
    # unprojectable on any engine, so they never trip the fail-fast guard.
    assert unprojectable("renpy", ["cast", "story"]) == []
    assert unprojectable("godot", ["cast", "story"]) == []
