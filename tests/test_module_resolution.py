"""The proposer picks modules from the catalog; code force-includes the foundation, expands each
pick's requires, falls back when no realization is present, and derives the engine. These tests pin
that contract."""

import maestro.modules as M
from tools.spec_tools import _picks, _module_reasons, _AUTO_REASON


def test_picks_normalizes_map_and_list_and_garbage():
    assert _picks({"scenes": "told through talk", "inventory": ""}) == {
        "scenes": "told through talk", "inventory": ""}
    assert _picks(["scenes", "story"]) == {"scenes": "", "story": ""}
    assert _picks(None) == {} and _picks("scenes") == {}


def test_reasons_keep_justifications_and_flag_gaps():
    picks = {"scenes": "the descent is told through his dialogue", "inventory": ""}
    modules, _ = M.resolve_modules(list(picks))
    reasons = _module_reasons(modules, picks)
    assert reasons["scenes"] == "the descent is told through his dialogue"
    assert reasons["inventory"] == "(reason missing)"        # picked, no justification
    assert reasons["cast"] == _AUTO_REASON                  # pulled by requires, not chosen
    assert set(reasons) == set(modules)                     # one reason per resolved module


def test_catalog_hides_always_on_foundation():
    catalog = dict(M.selectable_catalog())
    assert "human" not in catalog and "assets" not in catalog
    real = {"scenes", "world", "cast", "inventory", "story", "combat"}
    assert real <= set(catalog)
    assert all(catalog[mid] for mid in real)  # every real module is described


def test_foundation_is_always_forced_in():
    modules, _ = M.resolve_modules(["scenes"])
    assert {"human", "assets", "state"} <= set(modules)   # state is an always-on invariant


def test_requires_expand_transitively():
    # combat needs world+scenes; scenes needs cast.
    modules, _ = M.resolve_modules(["combat"])
    assert {"combat", "world", "scenes", "cast"} <= set(modules)


def test_vn_picks_renpy_combat_picks_godot():
    _, vn_engine = M.resolve_modules(["scenes", "story"])
    _, combat_engine = M.resolve_modules(["combat"])
    assert vn_engine == "renpy"
    assert combat_engine == "godot"  # only the godot runtime plays combat


def test_world_game_resolves_without_falling_back():
    modules, engine = M.resolve_modules(["world"])
    assert "world" in modules and "scenes" not in modules  # a world alone is a realization
    assert engine == "renpy"


def test_world_and_scenes_compose():
    # No exclusion: a world WITH talkable NPCs (scenes) is a valid composition, not a fallback.
    modules, engine = M.resolve_modules(["world", "scenes"])
    assert {"world", "scenes", "cast"} <= set(modules)
    assert engine == "renpy"


def test_no_realization_falls_back_to_vn():
    modules, engine = M.resolve_modules(["inventory"])
    assert "scenes" in modules  # a buildable realization always exists
    assert engine == "renpy"


def test_unknown_ids_are_dropped():
    modules, _ = M.resolve_modules(["scenes", "made_up_module"])
    assert "made_up_module" not in modules
    assert "scenes" in modules


# ── presentation (hd2d 3D routing) ────────────────────────────────────────────────────────────
from tools.spec_tools import _resolve_presentation  # noqa: E402


def test_hd2d_with_world_forces_godot():
    modules, _ = M.resolve_modules(["world"])
    engine, presentation = _resolve_presentation("hd2d", modules, "renpy")
    assert engine == "godot" and presentation == "hd2d"


def test_hd2d_without_world_downgrades_to_2d():
    # A pure-conversation game has nothing to render in 3D; hd2d falls back rather than ship a
    # dead flag, and the engine is left as the modules derived it.
    modules, base = M.resolve_modules(["scenes"])
    engine, presentation = _resolve_presentation("hd2d", modules, base)
    assert presentation == "2d" and engine == base


def test_default_presentation_is_2d():
    modules, _ = M.resolve_modules(["world"])
    assert _resolve_presentation(None, modules, "renpy") == ("renpy", "2d")
    assert _resolve_presentation("2d", modules, "godot") == ("godot", "2d")
