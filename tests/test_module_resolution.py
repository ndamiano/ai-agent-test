"""The proposer picks modules from the catalog; code force-includes the foundation, expands each
pick's requires, rejects inconsistent sets, and derives the engine. These tests pin that contract."""

import maestro.modules as M
from tools.spec_tools import _picks, _module_reasons, _AUTO_REASON


def test_picks_normalizes_map_and_list_and_garbage():
    assert _picks({"dialogue": "told through talk", "economy": ""}) == {
        "dialogue": "told through talk", "economy": ""}
    assert _picks(["dialogue", "outline"]) == {"dialogue": "", "outline": ""}
    assert _picks(None) == {} and _picks("dialogue") == {}


def test_reasons_keep_justifications_and_flag_gaps():
    picks = {"dialogue": "the descent is told through his dialogue", "economy": ""}
    modules, _ = M.resolve_modules(list(picks))
    reasons = _module_reasons(modules, picks)
    assert reasons["dialogue"] == "the descent is told through his dialogue"
    assert reasons["economy"] == "(reason missing)"        # picked, no justification
    assert reasons["cast"] == _AUTO_REASON                  # pulled by requires, not chosen
    assert set(reasons) == set(modules)                     # one reason per resolved module


def test_catalog_hides_always_on_foundation():
    catalog = dict(M.selectable_catalog())
    assert "human" not in catalog and "assets" not in catalog
    real = {"dialogue", "navigation", "cast", "economy", "outline", "goal_flag", "card_play"}
    assert real <= set(catalog)
    assert all(catalog[mid] for mid in real)  # every real module is described


def test_foundation_is_always_forced_in():
    modules, _ = M.resolve_modules(["dialogue"])
    assert {"human", "assets"} <= set(modules)


def test_requires_expand_transitively():
    # navigation needs dialogue_npc, which needs navigation + cast; card_play pulls the whole world.
    modules, _ = M.resolve_modules(["card_play"])
    assert {"card_play", "navigation", "dialogue_npc", "cast"} <= set(modules)


def test_vn_picks_renpy_card_picks_web():
    _, vn_engine = M.resolve_modules(["dialogue", "outline"])
    _, card_engine = M.resolve_modules(["card_play"])
    assert vn_engine == "renpy"
    assert card_engine == "web"  # only the web runtime renders cards


def test_navigation_game_resolves_without_falling_back():
    modules, engine = M.resolve_modules(["navigation", "goal_flag"])
    assert "navigation" in modules and "goal_flag" in modules
    assert "dialogue" not in modules  # the VN spine is not the pnc terminal
    assert engine == "renpy"


def test_conflicting_spines_fall_back_to_vn():
    # dialogue (VN spine) and navigation are mutually exclusive — an inconsistent pick.
    modules, engine = M.resolve_modules(["dialogue", "navigation"])
    assert "dialogue" in modules and "navigation" not in modules
    assert engine == "renpy"


def test_no_terminal_falls_back_to_vn():
    modules, engine = M.resolve_modules(["economy"])
    assert "dialogue" in modules  # a buildable terminal always exists
    assert engine == "renpy"


def test_unknown_ids_are_dropped():
    modules, _ = M.resolve_modules(["dialogue", "made_up_module"])
    assert "made_up_module" not in modules
    assert "dialogue" in modules
