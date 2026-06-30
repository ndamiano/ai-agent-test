"""combat — turn-based encounters. Authors the `combat` component.

A combat game: declared stats (hp/mp/atk), abilities that damage/heal/apply statuses, combatants
(stat blocks tied to cast characters), and encounters (combatants placed on two factions with
win/lose conditions that flow back out to a node). A fight is ENTERED by a `start_combat` hotspot
in a place (so combat composes with `world`) and resolved out via on_victory/on_defeat node_ends.

The five slices are too interdependent to grow one file at a time (an encounter refs combatants
ref abilities ref stats), so the whole block is authored in ONE write_component call and validated
atomically — v_combat catches every INTERNAL reference at write time, leaving only the cross-
component refs (a combatant's `character`, an on_victory node) for the crossref gate.

Example games:
  - "a knight fights through a crypt of undead"   — cast + world + scenes + combat
  - "duel the four elemental masters of the tower" — cast + world + scenes + combat
"""

from typing import Dict, List, Optional

from maestro.modules import checks
from maestro.modules.module import Error, ErrorType, Module, register_module

_ROLES = {"resource_depletable", "resource_regenerating", "modifier", "rating"}
_OPS = {"damage", "heal", "set", "add"}
_SHAPES = {"self", "single", "all", "line", "cone", "area"}
_TARGET_FACTIONS = {"self", "ally", "enemy", "any"}
_FACTIONS = {"player", "ally", "enemy", "neutral"}
_MODELS = {"turn_based", "real_time", "auto"}

# The IR slices this component owns — a crossref failure on any of these paths is combat's to fix.
_COMBAT_SLICES = frozenset({"stats", "statuses", "abilities", "combatants", "encounters"})


def _combat_effect_refs_ok(eff: Dict, stats: set, statuses: set) -> Optional[str]:
    """A combat_effect is stat-op, status, or a world-effect bridge. Validate the INTERNAL refs
    (stat/status declared here); world effects bridge to flags/vars checked at crossref."""
    if "world" in eff:
        return None
    if "status" in eff:
        return None if eff["status"] in statuses else f"status {eff['status']!r} is not declared"
    if "stat" in eff:
        if eff["stat"] not in stats:
            return f"effect stat {eff['stat']!r} is not declared"
        if eff.get("op") not in _OPS:
            return f"effect on stat {eff['stat']!r} needs an op in {sorted(_OPS)}"
        sw = (eff.get("formula") or {}).get("scales_with")
        if sw is not None and sw not in stats:
            return f"formula.scales_with {sw!r} is not a declared stat"
        return None
    return "a combat effect must set a 'stat' (with op), a 'status', or a 'world' effect"


def v_combat(c: Dict) -> Optional[str]:
    model = c.get("combat_model", "turn_based")
    if model not in _MODELS:
        return f"combat.combat_model must be one of {sorted(_MODELS)} (turn_based plays today)"

    stats = c.get("stats")
    if not isinstance(stats, list) or not stats:
        return "combat.stats must be a non-empty list of {id, default, role}"
    stat_ids = set()
    has_depletable = False
    for s in stats:
        if not isinstance(s, dict) or not s.get("id"):
            return "combat.stats entries need an 'id'"
        if s.get("role") not in _ROLES:
            return f"combat.stats['{s.get('id')}'].role must be one of {sorted(_ROLES)}"
        if not isinstance(s.get("default"), (int, float)):
            return f"combat.stats['{s['id']}'].default must be a number"
        has_depletable = has_depletable or s["role"] == "resource_depletable"
        stat_ids.add(s["id"])
    if not has_depletable:
        return ("combat.stats needs at least one role 'resource_depletable' stat (e.g. hp) — a "
                "fight has to be able to END")

    status_ids = set()
    for st in c.get("statuses", []) or []:
        if not isinstance(st, dict) or not st.get("id") or not st.get("name"):
            return "combat.statuses entries need an 'id' and 'name'"
        for eff in st.get("tick", []) or []:
            err = _combat_effect_refs_ok(eff, stat_ids, status_ids | {st["id"]})
            if err:
                return f"combat.statuses['{st['id']}'].tick: {err}"
        status_ids.add(st["id"])

    abilities = c.get("abilities")
    if not isinstance(abilities, list) or not abilities:
        return "combat.abilities must be a non-empty list of {id, targeting, effects}"
    ability_ids = set()
    for a in abilities:
        if not isinstance(a, dict) or not a.get("id"):
            return "combat.abilities entries need an 'id'"
        tgt = a.get("targeting")
        if not isinstance(tgt, dict) or tgt.get("shape") not in _SHAPES \
                or tgt.get("faction") not in _TARGET_FACTIONS:
            return (f"combat.abilities['{a['id']}'].targeting needs shape in {sorted(_SHAPES)} "
                    f"and faction in {sorted(_TARGET_FACTIONS)}")
        effects = a.get("effects")
        if not isinstance(effects, list) or not effects:
            return f"combat.abilities['{a['id']}'].effects must be a non-empty list"
        for eff in effects:
            err = _combat_effect_refs_ok(eff, stat_ids, status_ids)
            if err:
                return f"combat.abilities['{a['id']}'].effects: {err}"
        for cost in a.get("cost", []) or []:
            if cost.get("stat") not in stat_ids:
                return f"combat.abilities['{a['id']}'].cost stat {cost.get('stat')!r} is not declared"
        ability_ids.add(a["id"])

    combatants = c.get("combatants")
    if not isinstance(combatants, list) or not combatants:
        return "combat.combatants must be a non-empty list of {id, stats, abilities}"
    combatant_ids = set()
    for cb in combatants:
        if not isinstance(cb, dict) or not cb.get("id"):
            return "combat.combatants entries need an 'id'"
        for sv in cb.get("stats", []) or []:
            if sv.get("stat") not in stat_ids:
                return f"combat.combatants['{cb['id']}'].stats names undeclared stat {sv.get('stat')!r}"
        for ab in cb.get("abilities", []) or []:
            if ab not in ability_ids:
                return f"combat.combatants['{cb['id']}'] uses undeclared ability {ab!r}"
        combatant_ids.add(cb["id"])

    encounters = c.get("encounters")
    if not isinstance(encounters, list) or not encounters:
        return "combat.encounters must be a non-empty list of {id, combatants, victory}"
    for e in encounters:
        if not isinstance(e, dict) or not e.get("id"):
            return "combat.encounters entries need an 'id'"
        ecbs = e.get("combatants")
        if not isinstance(ecbs, list) or not ecbs:
            return f"combat.encounters['{e['id']}'].combatants must be a non-empty list"
        seen_factions = set()
        for slot in ecbs:
            if slot.get("ref") not in combatant_ids:
                return f"combat.encounters['{e['id']}'] places undeclared combatant {slot.get('ref')!r}"
            if slot.get("faction") not in _FACTIONS:
                return (f"combat.encounters['{e['id']}'] combatant {slot.get('ref')!r} needs a "
                        f"faction in {sorted(_FACTIONS)}")
            seen_factions.add(slot["faction"])
        if "player" not in seen_factions or not (seen_factions & {"enemy", "neutral"}):
            return (f"combat.encounters['{e['id']}'] needs at least one 'player' combatant and one "
                    f"opponent ('enemy') — otherwise there is no fight")
        if not isinstance(e.get("victory"), dict):
            return f"combat.encounters['{e['id']}'] needs a 'victory' condition"
    return None


SKEL_COMBAT = (
    '{\n'
    '  "combat_model": "turn_based",\n'
    '  "stats": [\n'
    '    {"id": "hp",  "default": 30, "min": 0, "max": 30, "role": "resource_depletable"},\n'
    '    {"id": "mp",  "default": 10, "min": 0, "max": 10, "role": "resource_regenerating"},\n'
    '    {"id": "atk", "default": 5,  "role": "modifier"}\n'
    '  ],\n'
    '  "statuses": [\n'
    '    {"id": "poison", "name": "Poison", "tick": [{"stat": "hp", "op": "damage", "formula": {"base": 2}}]}\n'
    '  ],\n'
    '  "abilities": [\n'
    '    {"id": "slash", "name": "Slash",\n'
    '     "targeting": {"shape": "single", "faction": "enemy", "range": "melee"},\n'
    '     "effects": [{"stat": "hp", "op": "damage", "formula": {"base": 4, "scales_with": "atk", "scale": 1}}]},\n'
    '    {"id": "firebolt", "name": "Firebolt", "cost": [{"stat": "mp", "amount": 3}],\n'
    '     "targeting": {"shape": "single", "faction": "enemy", "range": "ranged"},\n'
    '     "effects": [{"stat": "hp", "op": "damage", "formula": {"base": 6}}, {"status": "poison", "duration": 3}]}\n'
    '  ],\n'
    '  "combatants": [\n'
    '    {"id": "cb_hero",  "character": "<a cast id>", "stats": [{"stat": "hp", "value": 30}], "abilities": ["slash", "firebolt"]},\n'
    '    {"id": "cb_enemy", "character": "<a cast id>", "stats": [{"stat": "hp", "value": 12}], "abilities": ["slash"]}\n'
    '  ],\n'
    '  "encounters": [\n'
    '    {"id": "enc_<slug>", "background": "bg_<place>",\n'
    '     "combatants": [\n'
    '       {"ref": "cb_hero",  "faction": "player", "position": {"cell": {"x": 1, "y": 2}}},\n'
    '       {"ref": "cb_enemy", "faction": "enemy",  "position": {"cell": {"x": 5, "y": 2}}}\n'
    '     ],\n'
    '     "victory": {"all_defeated": "enemy"},\n'
    '     "defeat":  {"all_defeated": "player"},\n'
    '     "on_victory": {"type": "jump", "target": "<a nodes id>"},\n'
    '     "on_defeat":  {"type": "end",  "ending": "game_over"}}\n'
    '  ]\n'
    '}\n'
    '// Author the WHOLE block in ONE write_component("combat", {...}) call.\n'
    '// stats: declare your own system; you NEED one role "resource_depletable" (hp) so a fight ends.\n'
    '//   roles: resource_depletable (HP), resource_regenerating (MP), modifier (feeds formulas), rating.\n'
    '// abilities.effects op: damage/heal/set/add on a stat; or {"status": id, "duration": n} to\n'
    '//   apply a status; or {"world": {"set_flag": "..."}} to bridge to game flags.\n'
    '// combatants tie to a cast `characters` id for name/sprite; their abilities MUST be declared above.\n'
    '// encounters need >=1 "player" and >=1 "enemy" combatant. on_victory/on_defeat are node_ends\n'
    '//   (jump to a nodes id, or end {ending}) — combat resolves back into the story.\n'
    '// ENTRY: some place interactable MUST have action {"type": "start_combat", "encounter": "enc_<slug>"}\n'
    '//   (add it with add_interactable on a world place) or the fight is never reachable.'
)

_MODE_TOOLS = frozenset({"write_component", "read_component", "validate", "update_scratchpad",
                         "request_review"})
# A combat-slice crossref (a combatant's external character, an ability's flag gate) is fixed by
# rewriting the combat doc; an unreachable encounter is fixed by wiring a start_combat hotspot.
_CROSSREF_TOOLS = frozenset({"write_component", "read_component", "read_node", "update_scratchpad",
                             "request_review"})
_REACH_TOOLS = frozenset({"read_component", "read_place", "add_interactable", "edit_place",
                          "write_place", "update_scratchpad", "request_review"})


def _start_combat_targets(art: Dict) -> set:
    """Every encounter id a place hotspot enters via a start_combat action."""
    targets = set()
    places = (art.get("places") or {}).get("places") or {}
    for place in places.values():
        for h in (place.get("interactables") or []) if isinstance(place, dict) else []:
            act = h.get("action") if isinstance(h, dict) else None
            if isinstance(act, dict) and act.get("type") == "start_combat" and act.get("encounter"):
                targets.add(act["encounter"])
    return targets


class Combat(Module):
    id = "combat"
    description = ("Turn-based combat: stats, abilities, and encounters the player fights through. "
                   "Godot engine only.")
    requires = ("world", "scenes")
    priority = 40
    component = "combat"
    mode_prompt = "combat_write.txt"
    mode_tools = _MODE_TOOLS
    skeleton = SKEL_COMBAT
    skeletons = {"combat": SKEL_COMBAT}
    schemas = {"combat": v_combat}
    prompts = {"author": "combat_write.txt", "fix": "combat_write.txt"}
    target_jobs = {"build_combat": "author", "crossref": "fix", "encounters_reachable": "fix"}
    target_tools = {"build_combat": _MODE_TOOLS, "crossref": _CROSSREF_TOOLS,
                    "encounters_reachable": _REACH_TOOLS}
    target_max_tokens = {"build_combat": 16000}   # whole-block one-shot needs headroom; fixes keep the 8k guard
    projected = True

    def params(self) -> Dict:
        return {"min_encounters": 1}

    def get_errors(self, context) -> List[Error]:
        art = context.artifact
        errs: List[Error] = []

        c = checks.as_error(
            checks.count(art, "combat.encounters", min=context.param("min_encounters", 1)),
            type=ErrorType.BUILD, code="build_combat", component="combat")
        if c:
            return [c]

        # Each encounter must be reachable — entered by some place's start_combat hotspot.
        entered = _start_combat_targets(art)
        for e in (art.get("combat") or {}).get("encounters", []):
            eid = e.get("id")
            if eid and eid not in entered:
                errs.append(Error(
                    type=ErrorType.FIX, code="encounters_reachable", component="combat", path=eid,
                    message=(f"encounter '{eid}' is never started — give a place interactable an "
                             f"action {{type: 'start_combat', encounter: '{eid}'}} "
                             f"(add_interactable on a world place).")))

        # The combat slices' external references (a combatant's character, an ability's flag gate):
        # surfaced as combat's own crossref errors so the fix rewrites the combat doc, not a place.
        from maestro.ir_crossref import slice_token
        for rec in checks.crossref_failures(art):
            if slice_token(rec.get("path", "")) in _COMBAT_SLICES:
                errs.append(Error(type=ErrorType.FIX, code="crossref", component="combat",
                                  message=rec["message"], path=rec.get("path"), ref=rec.get("ref")))
        return errs


MODULE = Combat()
register_module(MODULE)
