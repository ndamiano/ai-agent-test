"""combat per-error fix prompts: each crossref KIND / structural slice / unreachable encounter gets
its own specific correction (prompt + the exact combat WRITE tool + the resolving catalogue), not the
old shared SKEL_FIX menu. The detector carries `kind` so the crossref fix can dispatch per-kind."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.modules.combat import MODULE as COMBAT, _structural_slice
from maestro.modules.context import Context


def _base_combat(**over):
    c = {
        "combat_model": "turn_based",
        "stats": [{"id": "hp", "default": 30, "min": 0, "max": 30, "role": "resource_depletable"}],
        "abilities": [{"id": "slash", "name": "Slash",
                       "targeting": {"shape": "single", "faction": "enemy", "range": "melee"},
                       "effects": [{"stat": "hp", "op": "damage", "formula": {"base": 4}}]}],
        "combatants": [{"id": "cb_hero", "character": "hero", "stats": [{"stat": "hp", "value": 30}],
                        "abilities": ["slash"]},
                       {"id": "cb_foe", "character": "foe", "stats": [{"stat": "hp", "value": 10}],
                        "abilities": ["slash"], "xp_yield": 5}],
        "encounters": [{"id": "enc1", "background": "bg_arena",
                        "combatants": [{"ref": "cb_hero", "faction": "player"},
                                       {"ref": "cb_foe", "faction": "enemy"}],
                        "victory": {"all_defeated": "enemy"},
                        "on_victory": {"type": "jump", "target": "aftermath"}}],
        "progression": {"player": "cb_hero", "xp_var": "xp",
                        "growth": [{"stat": "hp", "per_level": 3}]},
    }
    c.update(over)
    return c


def _art(combat, *, cast_ids=("hero", "foe"), node_ids=("aftermath",), reachable=True):
    hotspot = ({"id": "h1", "label": "fight", "position": {"cell": {"x": 1, "y": 1}},
                "action": {"type": "start_combat", "encounter": "enc1"}}
               if reachable else
               {"id": "h1", "label": "x", "position": {"cell": {"x": 1, "y": 1}},
                "action": {"type": "examine", "text": "nothing"}})
    return {
        "characters": {"characters": [{"id": c, "name": c.title()} for c in cast_ids]},
        "asset_manifest": {"backgrounds": [{"id": "bg_arena", "image_file": "a.png"}]},
        "nodes": {"node_ids": list(node_ids),
                  "nodes": {n: {"id": n, "lines": []} for n in node_ids}},
        "items": {"items": [{"id": "potion", "name": "Potion"}]},
        "places": {"start_place": "room", "place_ids": ["room"],
                   "variables": [{"id": "xp", "default": 0, "level_var": "level", "per_level": 20},
                                 {"id": "level", "default": 1}],
                   "places": {"room": {"kind": "interior", "background": "bg_arena",
                                       "interactables": [hotspot]}}},
        "combat": combat,
    }


def _ctx(art):
    class S:
        run_dir = "/tmp/none"
        def load_artifact(self): return art
        def read_story_state(self): return {}
    return Context(spec={"params": {}, "modules": ["combat", "world", "scenes"]},
                   state=S(), artifact=art)


def _err(art, code):
    return next(e for e in COMBAT.get_errors(_ctx(art)) if e.code == code)


# ── crossref: kind is carried + dispatches to a per-kind prompt ─────────────────
def test_crossref_detector_carries_kind():
    # cb_foe.character 'foe' undeclared in cast -> a `character`-kind crossref on the combatants slice.
    art = _art(_base_combat(), cast_ids=("hero",))
    e = _err(art, "crossref")
    assert e.kind == "character"
    assert e.path == "combatants[cb_foe].character"


def test_crossref_character_uses_write_combatant_and_cast_roster():
    art = _art(_base_combat(), cast_ids=("hero", "mara", "bramble"))  # foe still missing
    e = _err(art, "crossref")
    assert e.kind == "character"
    cp = COMBAT.get_correction_prompt(_ctx(art), e)
    # the exact combat write tool for the slice — never edit_node
    assert "write_combatant" in cp.allowed_tools
    assert "edit_node" not in cp.allowed_tools
    # the character-specific prompt, not a generic menu
    assert "cast" in cp.system.lower() and "write_combatant" in cp.user
    # the resolving catalogue is the cast roster (the ids the ref must repoint into)
    assert "mara" in cp.user and "bramble" in cp.user


def test_crossref_node_uses_write_encounter_and_nodes_catalogue():
    # on_victory jumps to 'aftermath' but no such node exists -> node-kind crossref on encounters slice.
    art = _art(_base_combat(), node_ids=("intro", "epilogue"))
    e = _err(art, "crossref")
    assert e.kind == "node"
    cp = COMBAT.get_correction_prompt(_ctx(art), e)
    assert "write_encounter" in cp.allowed_tools and "edit_node" not in cp.allowed_tools
    assert "epilogue" in cp.user  # the nodes catalogue to repoint into


def test_crossref_item_gate_uses_slice_write_tool():
    # an ability requires an item the game never declares -> item-kind crossref, fixed via write_ability.
    ab = {"id": "drink", "name": "Drink",
          "targeting": {"shape": "self", "faction": "self", "range": "melee"},
          "requires": {"item": "elixir"},
          "effects": [{"stat": "hp", "op": "heal", "formula": {"base": 5}}]}
    combat = _base_combat()
    combat["abilities"] = combat["abilities"] + [ab]
    combat["combatants"][0]["abilities"] = ["slash", "drink"]
    art = _art(combat)
    e = _err(art, "crossref")
    assert e.kind == "item" and e.path.startswith("abilities[drink]")
    cp = COMBAT.get_correction_prompt(_ctx(art), e)
    assert "write_ability" in cp.allowed_tools
    assert "potion" in cp.user  # existing item catalogue to repoint into


# ── structural: routed to the failing slice's skeleton + write tool ─────────────
def test_structural_slice_parse():
    assert _structural_slice("combat.abilities: bad") == "abilities"
    assert _structural_slice("combat.combatants: x") == "combatants"
    assert _structural_slice("combat.stats['hp'].role must be") == "stats"
    assert _structural_slice("combat.combat_model must be") == "combat_model"
    assert _structural_slice("combat must be a JSON object") == ""


def test_structural_fix_targets_the_failing_slice_tool():
    # an ability effect missing its `op` -> v_combat "combat.abilities: ..." -> write_ability skeleton.
    combat = _base_combat()
    combat["abilities"][0]["effects"] = [{"stat": "hp", "formula": {"base": 4}}]
    art = _art(combat)
    e = _err(art, "combat_structural")
    cp = COMBAT.get_correction_prompt(_ctx(art), e)
    assert "write_ability" in cp.allowed_tools
    assert "write_combatant" not in cp.allowed_tools  # ONE slice, not the menu
    assert "re-author" in cp.system.lower()


# ── unreachable encounter: fixed on a PLACE, not by touching combat ─────────────
def test_reach_fix_edits_a_place_not_the_encounter():
    art = _art(_base_combat(), reachable=False)
    e = _err(art, "encounters_reachable")
    assert e.path == "enc1"
    cp = COMBAT.get_correction_prompt(_ctx(art), e)
    assert "add_interactable" in cp.allowed_tools
    # combat write tools are NOT offered — the encounter is done, only entry is missing
    assert "write_encounter" not in cp.allowed_tools
    assert "start_combat" in cp.system and "room" in cp.user  # the place index catalogue
