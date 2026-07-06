"""wild_encounters — roaming danger on the walkable map.

A thin module by design: one real (detect → fix). Random encounters are a game-DESIGN decision,
not a size knob — some combat games are all authored set pieces (a duel ladder, a tactics
gauntlet) and must not be forced to carry grinding zones. Picking this module is the proposer
saying "the world itself is dangerous here", with the justification visible at the freeze gate.

The mechanic: a walkable zone carries an `encounter_table` {rate, entries:[{combatant, weight,
tier}]}; each step on open ground rolls `rate`, a hit fights one weighted draw (stats scaled by
tier) as the progression player. Losing a wild fight respawns at the zone entrance at half
strength — authored encounters keep their on_defeat stakes. One authored table multiplies a few
enemy archetypes into a zone's worth of fights; length from systems, not authored count.

Example games:
  - "infected things stalk the lower shafts"       — cast + world + scenes + combat + wild_encounters
  - "duel the four elemental masters of the tower"  — combat WITHOUT this module (set pieces only)
"""

from typing import Dict, Optional

from maestro.modules.module import Check, Error, Module, register_module

_WALKABLE = ("world_map", "town", "interior")


def encounter_table_error(table, combat: Dict) -> Optional[str]:
    if not isinstance(table, dict):
        return "encounter_table must be {rate, entries: [{combatant, weight?, tier?}]}"
    rate = table.get("rate")
    if not isinstance(rate, (int, float)) or not (0 < rate <= 1):
        return ("encounter_table.rate must be a number in (0, 1] — the per-step wild-fight "
                "chance on open ground (0.12 reads well)")
    entries = table.get("entries")
    if not isinstance(entries, list) or not entries:
        return "encounter_table.entries must be a non-empty list of {combatant, weight?, tier?}"
    cbs = {x["id"] for x in (combat.get("combatants") or [])
           if isinstance(x, dict) and isinstance(x.get("id"), str)}
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or e.get("combatant") not in cbs:
            return (f"encounter_table.entries[{i}].combatant must be an authored combatant id "
                    f"({sorted(cbs) or 'NONE yet — write_combatant first'})")
        if "weight" in e and (not isinstance(e["weight"], int) or e["weight"] < 1):
            return f"encounter_table.entries[{i}].weight must be a positive integer"
        if "tier" in e and (not isinstance(e["tier"], (int, float)) or e["tier"] <= 0):
            return (f"encounter_table.entries[{i}].tier must be a positive number — the stat "
                    f"multiplier for this zone's variant (1.0 = base, 1.5 = elite)")
    return None


SKEL_TABLE = (
    '{\n'
    '  "rate": 0.12,\n'
    '  "entries": [\n'
    '    {"combatant": "<an authored enemy id>", "weight": 3, "tier": 1.0},\n'
    '    {"combatant": "<a rarer, meaner one>",  "weight": 1, "tier": 1.5}\n'
    '  ]\n'
    '}\n'
    '// call set_encounter_table(place_id="<a dangerous walkable zone>", table={...}).\n'
    '// Wild fights: each step on open ground rolls `rate`; a hit draws ONE combatant by weight,\n'
    '// stats scaled by tier. Losing a wild fight respawns at the zone entrance (no game over).\n'
    '// Give the wilds/dungeon zones a table; keep the safe town zone without one.'
)

_TABLE_TOOLS = frozenset({"set_encounter_table", "read_place", "read_component",
                          "update_scratchpad", "request_review"})


def _d_wild_tables(chk, m, ctx):
    art = ctx.artifact
    combat = art.get("combat") or {}
    places = (art.get("places") or {}).get("places") or {}
    walkable = {pid: p for pid, p in places.items()
                if isinstance(p, dict) and p.get("kind") in _WALKABLE}
    if not walkable or not combat.get("combatants"):
        return []
    good = [pid for pid in walkable
            if encounter_table_error(walkable[pid].get("encounter_table"), combat) is None]
    need = ctx.param("min_wild_zones", 1)
    if len(good) >= need:
        return []
    candidates = sorted(pid for pid in walkable if pid not in good)
    return [Error(type=chk.tier, code=chk.code, component="places",
                  message=(f"{len(good)} zone(s) carry a wild encounter_table, need {need} — "
                           f"wild fights are the game's length. Pick the dangerous zone(s) from "
                           f"{candidates} and call set_encounter_table(place_id=..., "
                           f"table={{rate, entries}})."))]


class WildEncounters(Module):
    id = "wild_encounters"
    description = ("Roaming danger: dangerous zones roll random fights while the player walks "
                   "them (weighted, tier-scaled enemies feeding the XP loop). Pick when the "
                   "WORLD itself threatens — wilderness, dungeons, infestations; skip when "
                   "every fight is an authored set piece (duels, boss ladders).")
    requires = ("combat",)
    priority = 45   # after combat's slices exist and world has zones to mark dangerous
    component = "places"
    mode_prompt = "wild_encounters_write.txt"

    checks = [Check("wild_tables", _d_wild_tables, tools=_TABLE_TOOLS, skeleton=SKEL_TABLE)]

    def render_context(self, ctx: Dict) -> str:
        # The table author needs the enemy roster (entries reference authored combatant ids) and
        # the walkable zones (which one is dangerous) — nothing else.
        from maestro import context_render as cr
        from maestro.modules import combat, world
        art = ctx.get("artifact") or {}
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += combat.combat_index_block(art)
        lines += world.places_index_block(art)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)

    def params(self) -> Dict:
        return {"min_wild_zones": 1}

    def affected_components(self):
        return ("places",)


MODULE = WildEncounters()
register_module(MODULE)
