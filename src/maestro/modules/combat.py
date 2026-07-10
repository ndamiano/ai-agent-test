"""combat — turn-based encounters. Authors the `combat` component, ONE slice at a time.

A combat game: declared stats (hp/mp/atk), abilities that damage/heal/apply statuses, combatants
(stat blocks tied to cast characters), and encounters (combatants placed on two factions with
win/lose conditions that flow back out to a node). A fight is ENTERED by a `start_combat` hotspot
in a place (so combat composes with `world`) and resolved out via on_victory/on_defeat node_ends.

The slices have a clean DEPENDENCY ORDER (an encounter refs combatants ref abilities ref stats), so
they are authored bottom-up, one item per tool call — `set_combat_meta` lays the stat system, then
`write_ability` / `write_combatant` / `write_encounter` grow the lists one at a time. Each tool
validates its slice against the already-declared upstream ids at write time (the per-slice validators
below), so a single bad field re-authors ONE item instead of the whole block. `v_combat` composes the
same validators as a whole-doc backstop (the schema entry + a structural done-condition).

Example games:
  - "a knight fights through a crypt of undead"   — cast + world + scenes + combat
  - "duel the four elemental masters of the tower" — cast + world + scenes + combat
"""

import re
from typing import Dict, List, Optional

from maestro import context_render as cr
from maestro.modules import checks
from maestro.modules.module import Check, Error, Module, register_module

_ROLES = {"resource_depletable", "resource_regenerating", "modifier", "rating"}
_OPS = {"damage", "heal", "set", "add"}
_SHAPES = {"self", "single", "all", "line", "cone", "area"}
_TARGET_FACTIONS = {"self", "ally", "enemy", "any"}
_FACTIONS = {"player", "ally", "enemy", "neutral"}
_MODELS = {"turn_based", "real_time", "auto"}
_NODE_END_TYPES = {"jump", "menu", "return", "end"}


def _node_end_error(ne, label: str) -> Optional[str]:
    """A node_end terminates a flow (combat resolution, a dialogue node). It is an OBJECT — the
    common trap is writing a bare node-id string for on_victory/on_defeat."""
    if not isinstance(ne, dict):
        return (f"{label} must be a node_end OBJECT — e.g. {{\"type\": \"jump\", \"target\": "
                f"\"<a nodes id>\"}} or {{\"type\": \"end\", \"ending\": \"<label>\"}} — not the bare "
                f"value {ne!r}")
    if ne.get("type") not in _NODE_END_TYPES:
        return f"{label}.type must be one of {sorted(_NODE_END_TYPES)}"
    if ne["type"] == "jump" and not isinstance(ne.get("target"), str):
        return f"{label} is a jump and needs \"target\": <a nodes id string>"
    return None

# The IR slices this component owns — a crossref failure on any of these paths is combat's to fix.
_COMBAT_SLICES = frozenset({"stats", "statuses", "abilities", "combatants", "encounters",
                            "progression"})


# ── per-slice validators (every id field type-guarded: a mis-typed value yields an actionable
#    message, never a TypeError on `<dict> in <set>`) ──────────────────────────────────────────
def _combat_effect_refs_ok(eff: Dict, stats: set, statuses: set) -> Optional[str]:
    """A combat_effect is stat-op, status, or a world-effect bridge. Validate the INTERNAL refs
    (stat/status declared here); world effects bridge to flags/vars checked at crossref."""
    if not isinstance(eff, dict):
        return "a combat effect must be an object: {stat,op} | {status} | {world}"
    if "world" in eff:
        return None
    if "status" in eff:
        s = eff["status"]
        if not isinstance(s, str):
            return ('a status effect needs "status" to be a declared status id STRING, e.g. '
                    '{"status": "poison", "duration": 3} — not an object')
        return None if s in statuses else f"status {s!r} is not declared"
    if "stat" in eff:
        st = eff["stat"]
        if not isinstance(st, str):
            return ('a stat effect needs "stat" to be a declared stat id STRING, e.g. '
                    '{"stat": "hp", "op": "damage", "formula": {"base": 4}} — not an object')
        if st not in stats:
            return (f"effect stat {st!r} is not declared — use one of the declared stats "
                    f"{sorted(stats)}")
        if eff.get("op") not in _OPS:
            return (f"effect on stat {st!r} needs \"op\": one of {sorted(_OPS)} and the magnitude "
                    f"under \"formula\": {{\"base\": <n>}} — e.g. "
                    f"{{\"stat\": {st!r}, \"op\": \"damage\", \"formula\": {{\"base\": 6}}}}")
        formula = eff.get("formula")
        if formula is not None and not isinstance(formula, dict):
            return (f"effect \"formula\" must be an object {{\"base\": <n>, \"scales_with\"?: "
                    f"<stat id>, \"scale\"?: <n>}}, not {type(formula).__name__} {formula!r}")
        # the compile schema is additionalProperties:false — an off-key formula (observed live:
        # "scale_factor") must die at write time, not park the build at the compile gate
        bad = set(formula or {}) - {"base", "scales_with", "scale"}
        if bad:
            return (f"formula has unknown key(s) {sorted(bad)} — allowed keys are exactly "
                    f"\"base\", \"scales_with\", \"scale\" (the multiplier is named \"scale\")")
        for k in ("base", "scale"):
            v = (formula or {}).get(k)
            if v is not None and not isinstance(v, (int, float)):
                return f"formula.{k} must be a number, not {type(v).__name__} {v!r}"
        sw = (formula or {}).get("scales_with")
        if sw is not None and (not isinstance(sw, str) or sw not in stats):
            return f"formula.scales_with must be a declared stat id string; {sw!r} is not"
        return None
    return "a combat effect must set a 'stat' (with op), a 'status', or a 'world' effect"


def _stats_ids(stats) -> tuple:
    """(set of declared stat ids, error-or-None). One role must be resource_depletable so a fight ends."""
    if not isinstance(stats, list) or not stats:
        return set(), "combat.stats must be a non-empty list of {id, default, role}"
    ids, depletable = set(), False
    for s in stats:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            return set(), "combat.stats entries need a string 'id'"
        if not isinstance(s.get("role"), str) or s["role"] not in _ROLES:
            return set(), f"combat.stats['{s.get('id')}'].role must be one of {sorted(_ROLES)}"
        if not isinstance(s.get("default"), (int, float)) or isinstance(s.get("default"), bool):
            return set(), f"combat.stats['{s['id']}'].default must be a number"
        depletable = depletable or s["role"] == "resource_depletable"
        ids.add(s["id"])
    if not depletable:
        return ids, ("combat.stats needs at least one role 'resource_depletable' stat (e.g. hp) — a "
                     "fight has to be able to END")
    return ids, None


def _statuses_ids(statuses, stat_ids: set) -> tuple:
    ids = set()
    if statuses is None:
        return ids, None
    if not isinstance(statuses, list):
        return ids, "combat.statuses must be a list of {id, name, tick?}"
    for st in statuses:
        if not isinstance(st, dict) or not isinstance(st.get("id"), str) or not st.get("name"):
            return ids, "combat.statuses entries need a string 'id' and a 'name'"
        for eff in st.get("tick", []) or []:
            err = _combat_effect_refs_ok(eff, stat_ids, ids | {st["id"]})
            if err:
                return ids, f"combat.statuses['{st['id']}'].tick: {err}"
        ids.add(st["id"])
    return ids, None


def _ability_error(a: Dict, stat_ids: set, status_ids: set) -> Optional[str]:
    if not isinstance(a, dict) or not isinstance(a.get("id"), str):
        return "an ability needs a string 'id'"
    tgt = a.get("targeting")
    if not isinstance(tgt, dict) or not isinstance(tgt.get("shape"), str) \
            or tgt.get("shape") not in _SHAPES \
            or not isinstance(tgt.get("faction"), str) or tgt.get("faction") not in _TARGET_FACTIONS:
        return (f"ability['{a['id']}'].targeting needs shape (a string) in {sorted(_SHAPES)} and "
                f"faction in {sorted(_TARGET_FACTIONS)}")
    effects = a.get("effects")
    if not isinstance(effects, list) or not effects:
        return f"ability['{a['id']}'].effects must be a non-empty list"
    for eff in effects:
        err = _combat_effect_refs_ok(eff, stat_ids, status_ids)
        if err:
            return f"ability['{a['id']}'].effects: {err}"
    for cost in a.get("cost", []) or []:
        cs = cost.get("stat") if isinstance(cost, dict) else None
        if not isinstance(cs, str) or cs not in stat_ids:
            return (f"ability['{a['id']}'].cost needs {{\"stat\": <declared stat id>, \"amount\": "
                    f"<n>}}; {cs!r} is not one of the declared stats {sorted(stat_ids)}")
    req = a.get("requires")
    if req is not None:
        if not isinstance(req, dict):
            return f"ability['{a['id']}'].requires must be a condition object"
        for key in ("item", "flag", "var"):
            if key in req and not isinstance(req[key], str):
                return (f"ability['{a['id']}'].requires.{key} must be a string id (got "
                        f"{type(req[key]).__name__}) — a stat can NOT be used as a condition; "
                        f"gate on an item or flag, or drop `requires`")
    return None


def _combatant_error(cb: Dict, stat_ids: set, ability_ids: set) -> Optional[str]:
    if not isinstance(cb, dict) or not isinstance(cb.get("id"), str):
        return "a combatant needs a string 'id'"
    for sv in cb.get("stats", []) or []:
        sid = sv.get("stat") if isinstance(sv, dict) else None
        if not isinstance(sid, str) or sid not in stat_ids:
            return (f"combatant['{cb['id']}'].stats needs {{\"stat\": <declared stat id>, \"value\": "
                    f"<n>}}; {sid!r} is not one of the declared stats {sorted(stat_ids)}")
    for ab in cb.get("abilities", []) or []:
        if not isinstance(ab, str) or ab not in ability_ids:
            return (f"combatant['{cb['id']}'] uses undeclared ability {ab!r} — use one of the "
                    f"authored abilities {sorted(ability_ids) or 'NONE yet; write_ability first'}")
    if "xp_yield" in cb and (not isinstance(cb["xp_yield"], int) or cb["xp_yield"] < 0):
        return f"combatant['{cb['id']}'].xp_yield must be a non-negative integer"
    return None


def _encounter_error(e: Dict, combatant_ids: set) -> Optional[str]:
    # Collect ALL independent problems in one pass and report them together. A single encounter
    # commonly has two at once (a phantom combatant ref AND a bare-string on_victory); surfacing them
    # one at a time makes a small model ping-pong — fix one, re-introduce the other, forever.
    if not isinstance(e, dict) or not isinstance(e.get("id"), str):
        return "an encounter needs a string 'id'"
    eid = e["id"]
    issues: List[str] = []
    ecbs = e.get("combatants")
    if not isinstance(ecbs, list) or not ecbs:
        issues.append(f"encounter['{eid}'].combatants must be a non-empty list")
        ecbs = []
    seen_factions = set()
    for slot in ecbs:
        ref = slot.get("ref") if isinstance(slot, dict) else None
        if not isinstance(ref, str) or ref not in combatant_ids:
            avail = sorted(combatant_ids) or "NONE yet"
            issues.append(
                f"encounter['{eid}'] places undeclared combatant {ref!r} — either reference an "
                f"already-authored combatant ({avail}) EXACTLY, or author this enemy NOW with "
                f"write_combatant(combatant_id={ref!r}, ...) and then reference it. Do not leave a "
                f"ref that no combatant defines.")
            continue
        if slot.get("faction") not in _FACTIONS:
            issues.append(f"encounter['{eid}'] combatant {ref!r} needs a faction (a string) in "
                          f"{sorted(_FACTIONS)}")
        else:
            seen_factions.add(slot["faction"])
        pos = slot.get("position")
        if pos is not None and (not isinstance(pos, dict) or set(pos) - {"cell", "feature"}
                                or not (pos.get("cell") or pos.get("feature"))):
            issues.append(
                f"encounter['{eid}'] combatant {ref!r} has a bad position — it must be "
                f'{{"cell": {{"x": <int>, "y": <int>}}}} or {{"feature": "<id>"}}, NEVER a flat '
                f'{{"x", "y"}}. Wrap the coordinates in "cell", or drop position entirely '
                f"(it is optional — a turn-based fight needs no map cell).")
    if "player" not in seen_factions or not (seen_factions & {"enemy", "neutral"}):
        issues.append(f"encounter['{eid}'] needs at least one 'player' combatant and one opponent "
                      f"('enemy') — otherwise there is no fight")
    if not isinstance(e.get("victory"), dict):
        issues.append(f"encounter['{eid}'] needs a 'victory' condition, e.g. {{\"all_defeated\": \"enemy\"}}")
    for key in ("on_victory", "on_defeat"):
        if e.get(key) is not None:
            err = _node_end_error(e[key], f"encounter['{eid}'].{key}")
            if err:
                issues.append(err)
    return "; ".join(issues) if issues else None


def v_combat(c: Dict) -> Optional[str]:
    """Whole-doc validator — the `schemas` entry + structural backstop. Composes the per-slice
    validators in dependency order."""
    if not isinstance(c, dict):
        return "combat must be a JSON object"
    model = c.get("combat_model", "turn_based")
    if not isinstance(model, str) or model not in _MODELS:
        return f"combat.combat_model must be one of {sorted(_MODELS)} (turn_based plays today)"
    stat_ids, err = _stats_ids(c.get("stats"))
    if err:
        return err
    status_ids, err = _statuses_ids(c.get("statuses"), stat_ids)
    if err:
        return err
    abilities = c.get("abilities")
    if not isinstance(abilities, list) or not abilities:
        return "combat.abilities must be a non-empty list of {id, targeting, effects}"
    ability_ids = set()
    for a in abilities:
        e = _ability_error(a, stat_ids, status_ids)
        if e:
            return "combat.abilities: " + e
        ability_ids.add(a["id"])
    combatants = c.get("combatants")
    if not isinstance(combatants, list) or not combatants:
        return "combat.combatants must be a non-empty list of {id, stats, abilities}"
    combatant_ids = set()
    for cb in combatants:
        e = _combatant_error(cb, stat_ids, ability_ids)
        if e:
            return "combat.combatants: " + e
        combatant_ids.add(cb["id"])
    encounters = c.get("encounters")
    if not isinstance(encounters, list) or not encounters:
        return "combat.encounters must be a non-empty list of {id, combatants, victory}"
    for e in encounters:
        er = _encounter_error(e, combatant_ids)
        if er:
            return "combat.encounters: " + er
    if c.get("progression") is not None:
        er = progression_error(c["progression"], c)
        if er:
            return "combat.progression: " + er
    return None


# ── tool-facing validators (used by the slice write tools; pull declared ids from the live doc) ─
def _ids(combat: Dict, key: str) -> set:
    return {x["id"] for x in (combat.get(key) or [])
            if isinstance(x, dict) and isinstance(x.get("id"), str)}


def combat_meta_error(combat_model, stats, statuses) -> Optional[str]:
    if not isinstance(combat_model, str) or combat_model not in _MODELS:
        return f"combat_model must be one of {sorted(_MODELS)} (turn_based plays today)"
    stat_ids, err = _stats_ids(stats)
    if err:
        return err
    _, err = _statuses_ids(statuses, stat_ids)
    return err


def ability_write_error(content: Dict, combat: Dict) -> Optional[str]:
    return _ability_error(content, _ids(combat, "stats"), _ids(combat, "statuses"))


def combatant_write_error(content: Dict, combat: Dict) -> Optional[str]:
    return _combatant_error(content, _ids(combat, "stats"), _ids(combat, "abilities"))


def encounter_write_error(content: Dict, combat: Dict) -> Optional[str]:
    return _encounter_error(content, _ids(combat, "combatants"))


def progression_error(prog, combat: Dict) -> Optional[str]:
    if not isinstance(prog, dict):
        return "progression must be an object {player, xp_var, growth?}"
    cbs = _ids(combat, "combatants")
    if prog.get("player") not in cbs:
        return (f"progression.player must be an authored combatant id "
                f"({sorted(cbs) or 'NONE yet'}) — the protagonist's combatant, the one the "
                f"player fights as in every encounter")
    if not isinstance(prog.get("xp_var"), str) or not prog["xp_var"]:
        return ("progression.xp_var must name the leveled variable that accumulates XP "
                "(set_progression declares it for you from xp_var/level_var/per_level)")
    stat_ids = _ids(combat, "stats")
    for i, g in enumerate(prog.get("growth") or []):
        if not isinstance(g, dict) or g.get("stat") not in stat_ids \
                or not isinstance(g.get("per_level"), (int, float)):
            return (f"progression.growth[{i}] must be {{\"stat\": <declared stat id>, "
                    f"\"per_level\": <number>}} — stats are {sorted(stat_ids)}")
    return None




def combat_index_block(artifact: Dict) -> list:
    """Declared combat ids per slice as prompt context — what a start_combat / encounter
    reference resolves to."""
    c = artifact.get("combat") or {}
    slices = {k: sorted(x["id"] for x in (c.get(k) or [])
                        if isinstance(x, dict) and isinstance(x.get("id"), str))
              for k in ("stats", "statuses", "abilities", "combatants", "encounters")}
    if not any(slices.values()):
        return []
    return ["", "COMBAT (declared ids):",
            *(f"  {k}: {v}" for k, v in slices.items() if v)]


def combat_view(artifact: Dict) -> Dict:
    """The live id lists the author loop's slot-guard + progress note read."""
    c = artifact.get("combat") or {}
    return {
        "stat_ids": sorted(_ids(c, "stats")),
        "status_ids": sorted(_ids(c, "statuses")),
        "ability_ids": sorted(_ids(c, "abilities")),
        "combatant_ids": sorted(_ids(c, "combatants")),
        "encounter_ids": sorted(_ids(c, "encounters")),
    }


# ── per-target skeletons (the to-do names the piece; its exact JSON + tool are shown here) ──────
SKEL_META = (
    '{\n'
    '  "combat_model": "turn_based",\n'
    '  "stats": [\n'
    '    {"id": "hp",  "default": 30, "min": 0, "max": 30, "role": "resource_depletable"},\n'
    '    {"id": "mp",  "default": 10, "min": 0, "max": 10, "role": "resource_regenerating"},\n'
    '    {"id": "atk", "default": 5,  "role": "modifier"}\n'
    '  ],\n'
    '  "statuses": [\n'
    '    {"id": "poison", "name": "Poison", "tick": [{"stat": "hp", "op": "damage", "formula": {"base": 2}}]}\n'
    '  ]\n'
    '}\n'
    '// call set_combat_meta(combat_model="turn_based", stats=[...], statuses=[...]) ONCE — the foundation.\n'
    '// You NEED at least one role "resource_depletable" stat (hp) so a fight can END.\n'
    '//   roles: resource_depletable (HP), resource_regenerating (MP), modifier (feeds formulas), rating.\n'
    '// statuses are OPTIONAL; a status.tick effect is a {stat,op,formula} like an ability effect.'
)

SKEL_ABILITY = (
    '{\n'
    '  "name": "<short functional name — what it does, not a title>",\n'
    '  "targeting": {"shape": "single", "faction": "enemy", "range": "melee"},\n'
    '  "cost": [{"stat": "<a declared resource stat id>", "amount": 3}],\n'
    '  "effects": [{"stat": "<a declared stat id>", "op": "damage", "formula": {"base": 4}}]\n'
    '}\n'
    '// call write_ability(ability_id="slash", content={...}) — ONE ability per call.\n'
    '// Use the EXACT stat ids YOU declared (the progress note lists them) — do NOT copy "hp"/"atk"\n'
    '//   unless you actually declared those.\n'
    '// EVERY stat effect MUST have all three: "stat" (a declared id), "op" (one of\n'
    '//   damage | heal | set | add), and "formula": {"base": <number>}. A bare {"stat":"x","value":5}\n'
    '//   or a missing "op" is INVALID.\n'
    '// targeting.shape: self|single|all|line|cone|area. faction: self|ally|enemy|any. range: melee|ranged|any.\n'
    '// an effect is ONE of: {stat:<id>, op:.., formula:{base, scales_with?:<stat id>, scale?}}\n'
    '//   | {status:<declared status id>, duration:n} | {world:{set_flag:"..."}} (bridge to a story flag).\n'
    '// cost (optional): [{stat:<a declared stat>, amount:n}]. requires (optional): a condition, e.g. {flag:"x"}.\n'
    '// stats + statuses must already be declared via set_combat_meta.'
)

SKEL_COMBATANT = (
    '{\n'
    '  "character": "<a cast character id>",\n'
    '  "stats": [{"stat": "hp", "value": 30}],\n'
    '  "abilities": ["slash"]\n'
    '}\n'
    '// call write_combatant(combatant_id="cb_hero", content={...}) — ONE combatant per call.\n'
    '// character ties to a cast `characters` id (name/sprite). stats override the declared defaults.\n'
    '// abilities MUST be ability ids you already authored with write_ability.\n'
    '// The PLAYER fights as the PROTAGONIST: author one combatant whose character is the main\n'
    '// character (the story\'s POV), and use THAT one with faction "player" in every encounter —\n'
    '// never an enemy re-used on the player side.'
)

SKEL_ENCOUNTER = (
    '{\n'
    '  "background": "bg_<place>",\n'
    '  "combatants": [\n'
    '    {"ref": "cb_hero", "faction": "player", "position": {"cell": {"x": 1, "y": 2}}},\n'
    '    {"ref": "cb_foe",  "faction": "enemy",  "position": {"cell": {"x": 5, "y": 2}}}\n'
    '  ],\n'
    '  "victory": {"all_defeated": "enemy"},\n'
    '  "defeat":  {"all_defeated": "player"},\n'
    '  "on_victory": {"type": "jump", "target": "<a nodes id>"},\n'
    '  "on_defeat":  {"type": "end",  "ending": "game_over"}\n'
    '}\n'
    '// call write_encounter(encounter_id="enc_<slug>", content={...}) — ONE encounter per call.\n'
    '// combatants.ref MUST resolve to a combatant. If this fight needs an enemy you have NOT authored\n'
    '//   yet, author it FIRST in this same phase with write_combatant(combatant_id="cb_<foe>", ...),\n'
    '//   then reference it. Need >=1 player and >=1 enemy. Reuse the player combatant across fights.\n'
    '// victory/defeat: {all_defeated:<faction>} or {when:<condition>}. on_victory/on_defeat are\n'
    '//   node_ends (jump to a `nodes` id, or end{ending}) — combat resolves back into the story.\n'
    '// ENTRY: a place interactable needs action {type:start_combat, encounter:"enc_<slug>"} to start it.'
)

# ── live-id skeletons: the skeleton's example ids ("hp"/"slash") are the #1 thing a small model
#    copies, so every slice skeleton ends with the ACTUAL declared ids to use instead ─────────────
def _cast_ids(art: Dict) -> list:
    return sorted(c["id"] for c in (art.get("characters") or {}).get("characters", [])
                  if isinstance(c, dict) and isinstance(c.get("id"), str))


def _node_ids(art: Dict) -> list:
    return list((art.get("nodes") or {}).get("node_ids") or [])


def _skel_with_ids(base: str, fields):
    """skeleton -> callable(ctx) appending 'THE DECLARED IDS' — `fields` is (label, art->ids)."""
    def render(ctx):
        art = ctx.artifact
        lines = []
        for label, fn in fields:
            ids = fn(art)
            lines.append(f"//   {label}: {', '.join(ids) if ids else '(none authored yet)'}")
        return (base + "\n// THE DECLARED IDS — copy these EXACTLY, character for character. The "
                "example ids above\n//   are placeholders, NOT yours; an id not on this list will "
                "be REJECTED:\n" + "\n".join(lines))
    return render


_F_STATS = ("stats", lambda a: sorted(_ids(a.get("combat") or {}, "stats")))
_F_STATUSES = ("statuses", lambda a: sorted(_ids(a.get("combat") or {}, "statuses")))
_F_ABILITIES = ("abilities", lambda a: sorted(_ids(a.get("combat") or {}, "abilities")))
_F_COMBATANTS = ("combatants", lambda a: sorted(_ids(a.get("combat") or {}, "combatants")))
_F_CAST = ("cast character ids (for `character`)", _cast_ids)
_F_NODES = ("node ids (for on_victory/on_defeat jump targets)", _node_ids)

_META_TOOLS = frozenset({"set_combat_meta", "read_component", "request_review"})
_ABILITY_TOOLS = frozenset({"write_ability", "read_component", "request_review"})
_COMBATANT_TOOLS = frozenset({"write_combatant", "read_component", "request_review"})
# Encounters place combatants — but an encounter often wants an enemy the combatant floor never
# authored (three fights, three distinct monsters). So the encounter phase can ALSO write_combatant:
# author the missing enemy, then reference it. Without this the loop deadlocks — write_encounter
# rejects the phantom ref and write_combatant is out of scope, so no allowed tool can fix it.
_ENCOUNTER_TOOLS = frozenset({"write_encounter", "write_combatant", "read_component", "read_node",
                              "request_review"})
_MODE_TOOLS = frozenset({"set_combat_meta", "write_ability", "write_combatant", "write_encounter",
                         "read_component", "request_review"})
# A combat-slice crossref (a combatant's external character, an ability's flag gate) is fixed by
# rewriting the owning slice; an unreachable encounter by wiring a start_combat hotspot.
_CROSSREF_TOOLS = frozenset({"set_combat_meta", "write_ability", "write_combatant", "write_encounter",
                             "read_component", "read_node", "request_review"})
_REACH_TOOLS = frozenset({"read_component", "read_place", "add_interactable", "edit_place",
                          "write_place", "request_review"})

_GUARDS = {
    "min_abilities": {"count_tool": "write_ability", "id_key": "ability_id",
                      "id_list_key": "ability_ids", "noun": "ability"},
    "min_combatants": {"count_tool": "write_combatant", "id_key": "combatant_id",
                       "id_list_key": "combatant_ids", "noun": "combatant"},
    "min_encounters": {"count_tool": "write_encounter", "id_key": "encounter_id",
                       "id_list_key": "encounter_ids", "noun": "encounter"},
}

_PROGRESSION_TOOLS = frozenset({"set_progression", "write_combatant", "read_component",
                                "request_review"})

SKEL_PROGRESSION = (
    '{\n'
    '  "player": "<the PROTAGONIST\'s combatant id>",\n'
    '  "xp_var": "xp", "level_var": "level", "per_level": 20,\n'
    '  "growth": [{"stat": "hp", "per_level": 5}, {"stat": "attack", "per_level": 1}]\n'
    '}\n'
    '// call set_progression(progression={...}) — combat\'s growth loop.\n'
    '// player: the combatant the player fights as in EVERY encounter and wild fight.\n'
    '// xp_var/level_var become ordinary VARIABLES (declared for you): victories add the\n'
    '// defeated combatant\'s xp_yield to xp_var, level = floor(xp/per_level)+1, and any\n'
    '// condition may gate on the level variable. Each level-up applies every growth entry\n'
    '// and heals to full. Also make sure every ENEMY combatant carries "xp_yield": <n>\n'
    '// (re-author with write_combatant if one is missing).'
)


# ── per-error fix routing: a combat slice REWRITES the whole item (write_ability/write_combatant/
#    write_encounter/set_combat_meta/set_progression), so a crossref or structural repair on a slice
#    re-authors THAT ONE item with its slice tool — never edit_node. The map keys are the slice tokens
#    (ir_crossref.slice_token of a crossref path; the message prefix of a structural error): each →
#    (the slice's write tool, its authoring skeleton, the live-id fields to append). ─────────────────
_SLICE_FIX = {
    "combat_model": ("set_combat_meta", SKEL_META, []),
    "stats":        ("set_combat_meta", SKEL_META, []),
    "statuses":     ("set_combat_meta", SKEL_META, []),
    "abilities":    ("write_ability",   SKEL_ABILITY,     [_F_STATS, _F_STATUSES]),
    "combatants":   ("write_combatant", SKEL_COMBATANT,   [_F_STATS, _F_ABILITIES, _F_CAST]),
    "encounters":   ("write_encounter", SKEL_ENCOUNTER,   [_F_COMBATANTS, _F_NODES]),
    "progression":  ("set_progression", SKEL_PROGRESSION, [_F_COMBATANTS, _F_STATS]),
}
_SLICE_FIX_DEFAULT = ("write_encounter", SKEL_ENCOUNTER, [_F_COMBATANTS, _F_NODES])

# A crossref kind → the ONE catalogue the dangling ref must resolve into (ids only) + its kind-specific
# fix prompt. Kinds not listed (stat/status/ability/combatant/background) are combat-internal repoints
# → the generic prompt + combat's own declared ids.
_CROSSREF_KIND_PROMPT = {
    "character": "combat_crossref_character.txt",
    "node":      "combat_crossref_node.txt",
    "flag":      "combat_crossref_flag.txt",
    "variable":  "combat_crossref_variable.txt",
    "item":      "combat_crossref_item.txt",
}


def _crossref_catalogue(kind: str, art: Dict) -> List[str]:
    from maestro.modules import cast, inventory, scenes
    if kind == "character":
        return cast.character_index(art)
    if kind == "node":
        return scenes.nodes_index_block(art)
    if kind == "item":
        return inventory.item_index(art)
    if kind in ("flag", "variable"):
        return scenes.nodes_index_block(art)
    return combat_index_block(art)


def _structural_slice(msg: str) -> str:
    """The combat slice a v_combat structural message is about, from its `combat.<slice>` prefix."""
    m = re.match(r"combat\.([a-z_]+)", msg or "")
    return m.group(1) if m else ""


def _slice_fix_prompt(module, ctx, error, *, slice_key: str, system_file: str, catalogue: List[str]):
    """Assemble a slice-repair CorrectionPrompt: the kind/structural system prompt + the resolving
    catalogue + the failing slice's own write skeleton (live ids) + run-state, scoped to that slice's
    write tool. The model re-authors THE ONE named item, restating its valid fields and fixing the
    named one — no other slice is touched."""
    from maestro.modules.context import render_dict
    from maestro.modules.module import CorrectionPrompt, load_prompt
    write_tool, skel_base, fields = _SLICE_FIX.get(slice_key, _SLICE_FIX_DEFAULT)
    tools = ("read_component", "read_node", write_tool, "request_review")
    rd = render_dict(ctx, active="combat", target=error, available_tools=frozenset(tools))
    skeleton = _skel_with_ids(skel_base, fields)(ctx)
    user = "\n".join(cr.target_block(rd) + catalogue + ["", skeleton] + cr.tail_block(rd))
    return CorrectionPrompt(system=load_prompt(system_file), user=user, allowed_tools=tools)


def _crossref_fix(module, ctx, error):
    """A dangling combat-slice reference: repoint it (or drop the gate) by re-authoring the slice
    that holds it. The kind picks the resolving catalogue + prompt; the path's slice picks the tool."""
    from maestro.ir_crossref import slice_token
    return _slice_fix_prompt(
        module, ctx, error, slice_key=slice_token(error.path or ""),
        system_file=_CROSSREF_KIND_PROMPT.get(error.kind, "combat_crossref_generic.txt"),
        catalogue=_crossref_catalogue(error.kind, ctx.artifact))


def _structural_fix(module, ctx, error):
    """A v_combat structural failure (bad field/shape on one slice): re-author that one item with
    its slice tool, restating valid fields + fixing the named one. The slice comes from the message."""
    return _slice_fix_prompt(
        module, ctx, error, slice_key=_structural_slice(error.message or ""),
        system_file="combat_structural_fix.txt",
        catalogue=combat_index_block(ctx.artifact))


def _d_compiles(chk, m, ctx):
    """A whole-IR compile error whose path is in a COMBAT slice — combat owns it (the realization
    module that runs the compile can't rewrite the combat doc). Carry the failing slice on `path`
    so the fix routes to that slice's write tool."""
    errs = [e for e in checks.compile_errors(ctx.run_dir, ctx.engine)
            if checks.compile_slice(e) in _COMBAT_SLICES]
    if not errs:
        return []
    return [Error(type=chk.tier, code=chk.code, component="combat",
                  message="compile failed: invalid combat IR — " + "; ".join(errs[:5]),
                  path=errs[0].split(":", 1)[0].strip())]


def _compile_fix(module, ctx, error):
    """Route a combat compile error to its slice's write tool + a compile-repair prompt."""
    return _slice_fix_prompt(
        module, ctx, error, slice_key=checks.compile_slice(error.path or error.message or ""),
        system_file="combat_compile_fix.txt", catalogue=combat_index_block(ctx.artifact))


def _ctx_reach(module, rd: Dict) -> str:
    """An unreachable encounter is wired from a PLACE, not combat — target + the place index (where a
    start_combat hotspot goes) + run-state. The encounter itself is not touched."""
    from maestro.modules import world
    art = rd.get("artifact") or {}
    lines = cr.target_block(rd) + world.places_index_block(art) + cr.tail_block(rd)
    lines += ["", "Add a start_combat interactable to a walkable place. Do NOT touch the encounter."]
    return "\n".join(lines)


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


# ── detectors (dependency order: stat foundation, then the count slices, then the structural /
#    reachability / crossref backstops) ─────────────────────────────────────────────────────────
def _d_meta(chk, m, ctx):
    combat = ctx.artifact.get("combat") or {}
    meta = combat_meta_error(combat.get("combat_model", "turn_based"),
                             combat.get("stats"), combat.get("statuses"))
    return [Error(type=chk.tier, code=chk.code, component="combat",
                  message="declare the stat system first — " + meta)] if meta else []


def _floor_detector(path: str, noun: str):
    """A count slice's detector: fan the shortfall (`param(code) - len(path)`) into per-slot creates."""
    def detect(chk, m, ctx):
        gap = ctx.param(chk.code, 1) - checks.length(ctx.artifact, path)
        return checks.slot_errors(gap, type=chk.tier, code=chk.code, component="combat",
                                  noun=noun) if gap > 0 else []
    return detect


def _d_structural(chk, m, ctx):
    sv = v_combat(ctx.artifact.get("combat") or {})
    return [Error(type=chk.tier, code=chk.code, component="combat", message=sv)] if sv else []


def _d_reachable(chk, m, ctx):
    art = ctx.artifact
    entered = _start_combat_targets(art)
    out = []
    for e in (art.get("combat") or {}).get("encounters", []):
        eid = e.get("id")
        if isinstance(eid, str) and eid not in entered:
            out.append(Error(
                type=chk.tier, code=chk.code, component="combat", path=eid,
                message=(f"encounter '{eid}' is never started — give a place interactable an "
                         f"action {{type: 'start_combat', encounter: '{eid}'}} "
                         f"(add_interactable on a world place).")))
    return out


def _d_progression(chk, m, ctx):
    combat = ctx.artifact.get("combat") or {}
    if not combat.get("combatants"):
        return []
    prog = combat.get("progression")
    if prog is None:
        return [Error(type=chk.tier, code=chk.code, component="combat",
                      message=("no progression declared — the growth loop is missing. Call "
                               "set_progression(progression={player, xp_per_level, growth}) "
                               "with the PROTAGONIST's combatant as player."))]
    err = progression_error(prog, combat)
    if err:
        return [Error(type=chk.tier, code=chk.code, component="combat", message=err)]
    player = prog.get("player")
    missing = [cb["id"] for cb in combat.get("combatants", [])
               if isinstance(cb, dict) and cb.get("id") != player
               and not isinstance(cb.get("xp_yield"), int)]
    if missing:
        return [Error(type=chk.tier, code=chk.code, component="combat",
                      message=(f"combatants {missing} carry no xp_yield — defeating them must "
                               f"award XP. Re-author each with write_combatant including its "
                               f"existing fields plus \"xp_yield\": <n> (5-15 for mooks, more "
                               f"for elites)."))]
    return []


def _d_crossref(chk, m, ctx):
    # Combat-slice external refs (a combatant's character, an ability's flag gate) surface as combat's
    # own crossref so the fix rewrites the owning slice, not a place.
    from maestro.ir_crossref import slice_token
    out = []
    for rec in checks.crossref_failures(ctx.artifact):
        if slice_token(rec.get("path") or "") in _COMBAT_SLICES:
            out.append(Error(type=chk.tier, code=chk.code, component="combat",
                             message=rec["message"], path=rec.get("path"),
                             ref=rec.get("ref"), kind=rec.get("kind")))
    return out


class Combat(Module):
    id = "combat"
    layer = "engine"
    description = ("Turn-based combat: stats, abilities, and encounters the player fights through. "
                   "Godot engine only.")
    requires = ("world", "scenes")
    priority = 40
    component = "combat"
    mode_prompt = "combat_write.txt"
    mode_tools = _MODE_TOOLS
    schemas = {"combat": v_combat}
    projector = staticmethod(combat_view)
    projected = True
    tool_names = ("set_combat_meta", "write_ability", "write_combatant", "write_encounter",
                  "set_progression")

    # Dependency order, ONE slice at a time: the stat foundation, then abilities/combatants/encounters
    # (each a slot-guarded count target), then the structural backstop — all `blocking`, so a gap in
    # an upstream slice suppresses the downstream checks that reference it. Reachability + crossref
    # collect together once the slices are sound. Each check's skeleton shows exactly its one slice.
    checks = [
        Check("build_combat_meta", _d_meta, blocking=True, tools=_META_TOOLS, skeleton=SKEL_META),
        Check("min_abilities", _floor_detector("combat.abilities", "ability"), blocking=True,
              tools=_ABILITY_TOOLS, guard=_GUARDS["min_abilities"],
              skeleton=_skel_with_ids(SKEL_ABILITY, [_F_STATS, _F_STATUSES])),
        Check("min_combatants", _floor_detector("combat.combatants", "combatant"), blocking=True,
              tools=_COMBATANT_TOOLS, guard=_GUARDS["min_combatants"],
              skeleton=_skel_with_ids(SKEL_COMBATANT, [_F_STATS, _F_ABILITIES, _F_CAST])),
        Check("min_encounters", _floor_detector("combat.encounters", "encounter"), blocking=True,
              tools=_ENCOUNTER_TOOLS, guard=_GUARDS["min_encounters"],
              skeleton=_skel_with_ids(SKEL_ENCOUNTER, [_F_COMBATANTS, _F_NODES])),
        Check("build_progression", _d_progression, blocking=True, tools=_PROGRESSION_TOOLS,
              skeleton=_skel_with_ids(SKEL_PROGRESSION, [_F_COMBATANTS, _F_STATS])),
        Check("combat_structural", _d_structural, job="fix", blocking=True, tools=_CROSSREF_TOOLS,
              build_prompt=_structural_fix),
        Check("encounters_reachable", _d_reachable, job="fix", tools=_REACH_TOOLS,
              prompt="combat_reach_fix.txt", context=_ctx_reach),
        Check("crossref", _d_crossref, job="fix", tools=_CROSSREF_TOOLS, build_prompt=_crossref_fix),
        Check("compiles", _d_compiles, job="fix", when_clean=True, tools=_CROSSREF_TOOLS,
              build_prompt=_compile_fix),
    ]

    def params(self) -> Dict:
        return {"min_abilities": 3, "min_combatants": 3, "min_encounters": 2}

    def render_context(self, ctx: Dict) -> str:
        # The combat AUTHOR's context, crafted: the premise + character cards (a combatant IS a cast
        # member — competencies inform abilities, drives inform who fights), the scenes that exist
        # (on_victory/on_defeat jump targets), and the places (a fight is entered from a map).
        # The declared combat ids ride on each step's skeleton, not here.
        from maestro.modules import cast, scenes, world
        art = ctx.get("artifact") or {}
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        lines += cast.character_cards(art)
        lines += scenes.nodes_index_block(art)
        lines += world.places_index_block(art)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> list:
        return combat_index_block(artifact)


MODULE = Combat()
register_module(MODULE)
