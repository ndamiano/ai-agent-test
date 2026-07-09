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


def test_catalog_shows_aspects_not_engine_modules():
    # The catalog is nouns-primary: the proposer sees ASPECTS (dialogue/exploration/...), never the
    # engine modules they resolve to (scenes/world/combat) nor the always-on foundation.
    catalog = dict(M.selectable_catalog())
    aspects = {"dialogue", "narrative", "exploration", "turn_combat", "items", "roaming_encounters"}
    assert aspects <= set(catalog)
    assert all(catalog[a] for a in aspects)                       # every aspect is described
    engine_and_foundation = {"scenes", "world", "cast", "inventory", "story", "combat",
                             "wild_encounters", "human", "assets", "state"}
    assert engine_and_foundation.isdisjoint(catalog)             # engines/foundation are hidden


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


# ── aspects: the nouns-primary pick layer resolves to engine modules ──────────────────────────
import pytest  # noqa: E402
from maestro.modules.module import Module, register_module, MODULE_REGISTRY  # noqa: E402


def _engine_ids(ids):
    """The resolved set with the LLM-facing aspect ids stripped — the actual engine machinery."""
    modules, _ = M.resolve_modules(ids)
    return {mid for mid in modules if MODULE_REGISTRY[mid].layer != "aspect"}


def test_aspect_resolves_to_its_engine_modules():
    # picking the `dialogue` aspect pulls in its engine module (scenes) + that module's deps (cast).
    modules, engine = M.resolve_modules(["dialogue"])
    assert {"dialogue", "scenes", "cast"} <= set(modules)
    assert engine == "renpy"


def test_dialogue_aspect_engine_parity_with_picking_scenes():
    # behavior parity for the VN path: composing `dialogue` builds the SAME engine machinery as
    # naming the raw `scenes` engine module does today (the aspect id itself is inert — no checks).
    assert _engine_ids(["dialogue"]) == _engine_ids(["scenes"])
    assert M.MODULE_REGISTRY["dialogue"].checks == []             # degenerate wrapper, authors nothing


def test_combat_aspect_routes_to_godot():
    # the `turn_combat` aspect resolves through combat -> world/scenes/cast and forces the godot engine.
    modules, engine = M.resolve_modules(["turn_combat"])
    assert {"turn_combat", "combat", "world", "scenes", "cast"} <= set(modules)
    assert engine == "godot"


def test_every_catalog_aspect_carries_an_engine_requirement():
    for mid, _ in M.selectable_catalog():
        m = MODULE_REGISTRY[mid]
        assert m.layer == "aspect"
        engine_reqs = [r for r in m.requires if MODULE_REGISTRY[r].layer == "engine"]
        assert engine_reqs, f"aspect {mid!r} must require an engine module"


def test_aspect_without_engine_requires_is_rejected_at_register():
    # the guardrail: an aspect with no owning engine module is illegal — caught at import/register
    # time (fail fast), not silently at build.
    class Orphan(Module):
        id = "_orphan_aspect_test"
        layer = "aspect"
        requires = ()                                            # no engine module
        description = "illegal aspect"

    try:
        with pytest.raises(ValueError, match="must require"):
            register_module(Orphan())
    finally:
        MODULE_REGISTRY.pop("_orphan_aspect_test", None)


def test_aspect_requiring_only_another_aspect_is_rejected():
    class OnlyAspect(Module):
        id = "_only_aspect_test"
        layer = "aspect"
        requires = ("dialogue",)                                 # an aspect, not an engine module
        description = "illegal aspect"

    try:
        with pytest.raises(ValueError, match="must require"):
            register_module(OnlyAspect())
    finally:
        MODULE_REGISTRY.pop("_only_aspect_test", None)


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


# ── world-embedded conversations return (not chain to endings) ────────────────────────────────
from maestro.modules.scenes import _world_end  # noqa: E402


def test_world_conversation_returns_not_ends():
    ids = {"ending_a", "ending_b"}
    # a mid-map conversation hands control back to the world, never chains onward or ends the game
    assert _world_end({"type": "jump", "target": "scene_02"}, ids) == {"type": "return"}
    assert _world_end({"type": "end"}, ids) == {"type": "return"}
    assert _world_end(None, ids) == {"type": "return"}


def test_world_climax_menu_to_endings_survives():
    ids = {"ending_a", "ending_b"}
    end = {"type": "menu", "choices": [{"text": "A", "target": "ending_a"},
                                       {"text": "B", "target": "ending_b"}]}
    assert _world_end(end, ids) == end                       # the final fork stays reachable
    # a menu that does NOT target endings is not a climax fork — collapse to return
    assert _world_end({"type": "menu", "choices": [{"text": "x", "target": "scene_9"}]},
                      ids) == {"type": "return"}


def test_wild_encounters_module_has_prompt():
    # regression: the module was missing mode_prompt, so its fix crashed on load_prompt("")
    from maestro.modules.module import load_prompt
    wm = M.MODULE_REGISTRY["wild_encounters"]
    assert wm.mode_prompt and wm.component == "places"
    assert load_prompt(wm.mode_prompt)                       # resolves without IsADirectoryError
