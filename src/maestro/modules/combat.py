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

from typing import Dict, List, Optional

from maestro.modules import checks
from maestro.modules.module import Error, ErrorType, Module, register_module

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
_COMBAT_SLICES = frozenset({"stats", "statuses", "abilities", "combatants", "encounters"})


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
    '  "name": "Slash",\n'
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
    '// abilities MUST be ability ids you already authored with write_ability.'
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

SKEL_FIX = (
    '// Fix the named combat error with the slice tool it belongs to:\n'
    '//   set_combat_meta (model/stats/statuses) | write_ability | write_combatant | write_encounter.\n'
    '// A crossref on a combatant.character => rewrite that combatant (write_combatant) with a real cast id.\n'
    '// An unreachable encounter => add a start_combat interactable on a world place (add_interactable).'
)

_TARGET_SKELETON = {
    "build_combat_meta": SKEL_META,
    "min_abilities": SKEL_ABILITY,
    "min_combatants": SKEL_COMBATANT,
    "min_encounters": SKEL_ENCOUNTER,
}

_META_TOOLS = frozenset({"set_combat_meta", "read_component", "update_scratchpad", "request_review"})
_ABILITY_TOOLS = frozenset({"write_ability", "read_component", "update_scratchpad", "request_review"})
_COMBATANT_TOOLS = frozenset({"write_combatant", "read_component", "update_scratchpad", "request_review"})
# Encounters place combatants — but an encounter often wants an enemy the combatant floor never
# authored (three fights, three distinct monsters). So the encounter phase can ALSO write_combatant:
# author the missing enemy, then reference it. Without this the loop deadlocks — write_encounter
# rejects the phantom ref and write_combatant is out of scope, so no allowed tool can fix it.
_ENCOUNTER_TOOLS = frozenset({"write_encounter", "write_combatant", "read_component", "read_node",
                              "update_scratchpad", "request_review"})
_MODE_TOOLS = frozenset({"set_combat_meta", "write_ability", "write_combatant", "write_encounter",
                         "read_component", "update_scratchpad", "request_review"})
# A combat-slice crossref (a combatant's external character, an ability's flag gate) is fixed by
# rewriting the owning slice; an unreachable encounter by wiring a start_combat hotspot.
_CROSSREF_TOOLS = frozenset({"set_combat_meta", "write_ability", "write_combatant", "write_encounter",
                             "read_component", "read_node", "update_scratchpad", "request_review"})
_REACH_TOOLS = frozenset({"read_component", "read_place", "add_interactable", "edit_place",
                          "write_place", "update_scratchpad", "request_review"})

_GUARDS = {
    "min_abilities": {"count_tool": "write_ability", "id_key": "ability_id",
                      "id_list_key": "ability_ids", "noun": "ability"},
    "min_combatants": {"count_tool": "write_combatant", "id_key": "combatant_id",
                       "id_list_key": "combatant_ids", "noun": "combatant"},
    "min_encounters": {"count_tool": "write_encounter", "id_key": "encounter_id",
                       "id_list_key": "encounter_ids", "noun": "encounter"},
}


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
    schemas = {"combat": v_combat}
    prompts = {"author": "combat_write.txt", "fix": "combat_write.txt"}
    target_jobs = {"build_combat_meta": "author", "min_abilities": "author",
                   "min_combatants": "author", "min_encounters": "author",
                   "crossref": "fix", "combat_structural": "fix", "encounters_reachable": "fix"}
    target_tools = {"build_combat_meta": _META_TOOLS, "min_abilities": _ABILITY_TOOLS,
                    "min_combatants": _COMBATANT_TOOLS, "min_encounters": _ENCOUNTER_TOOLS,
                    "crossref": _CROSSREF_TOOLS, "combat_structural": _CROSSREF_TOOLS,
                    "encounters_reachable": _REACH_TOOLS}
    projector = staticmethod(combat_view)
    projected = True
    tool_names = ("set_combat_meta", "write_ability", "write_combatant", "write_encounter")
    create_guards = _GUARDS   # each count target (abilities/combatants/encounters) is slot-guarded

    def params(self) -> Dict:
        return {"min_abilities": 2, "min_combatants": 2, "min_encounters": 1}

    def _apply_target(self, code: str) -> None:
        # One mental model per call: the skeleton (+ tool) for the slice this target authors/fixes.
        self.skeleton = _TARGET_SKELETON.get(code, SKEL_FIX)
        self.skeletons = {"combat": self.skeleton}

    def get_correction_prompt(self, context, error: Error):
        self._apply_target(error.code)
        return super().get_correction_prompt(context, error)

    def get_errors(self, context) -> List[Error]:
        art = context.artifact
        combat = art.get("combat") or {}

        # Dependency order, ONE target at a time: the stat foundation, then abilities, combatants,
        # encounters — each grown by its own author loop — then the cross-slice/structural backstops.
        meta = combat_meta_error(combat.get("combat_model", "turn_based"),
                                 combat.get("stats"), combat.get("statuses"))
        if meta:
            return [Error(type=ErrorType.BUILD, code="build_combat_meta", component="combat",
                          message="declare the stat system first — " + meta)]

        for code, path, floor in (("min_abilities", "combat.abilities", "min_abilities"),
                                  ("min_combatants", "combat.combatants", "min_combatants"),
                                  ("min_encounters", "combat.encounters", "min_encounters")):
            gap = context.param(floor, 1) - checks.length(art, path)
            if gap > 0:
                return checks.slot_errors(gap, type=ErrorType.BUILD, code=code, component="combat",
                                          noun=_GUARDS[code]["noun"])

        errs: List[Error] = []
        # Structural backstop: cross-slice consistency the per-item writes can't all see at once.
        sv = v_combat(combat)
        if sv:
            return [Error(type=ErrorType.FIX, code="combat_structural", component="combat",
                          message=sv)]

        # Each encounter must be reachable — entered by some place's start_combat hotspot.
        entered = _start_combat_targets(art)
        for e in combat.get("encounters", []):
            eid = e.get("id")
            if isinstance(eid, str) and eid not in entered:
                errs.append(Error(
                    type=ErrorType.FIX, code="encounters_reachable", component="combat", path=eid,
                    message=(f"encounter '{eid}' is never started — give a place interactable an "
                             f"action {{type: 'start_combat', encounter: '{eid}'}} "
                             f"(add_interactable on a world place).")))

        # Combat-slice external references (a combatant's character, an ability's flag gate) surface
        # as combat's own crossref errors so the fix rewrites the owning slice, not a place.
        from maestro.ir_crossref import slice_token
        for rec in checks.crossref_failures(art):
            if slice_token(rec.get("path", "")) in _COMBAT_SLICES:
                errs.append(Error(type=ErrorType.FIX, code="crossref", component="combat",
                                  message=rec["message"], path=rec.get("path"), ref=rec.get("ref")))
        return errs


MODULE = Combat()
register_module(MODULE)
