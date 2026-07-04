"""Cross-reference linter for the Game IR — a hard agent-loop gate.

The JSON Schema (docs/game_ir.schema.json) proves an IR is well-SHAPED; it cannot prove a
reference RESOLVES — that a `speaker` is a real character, a `jump` lands on a real node, an item
condition names a declared item, an ability scales on a declared stat. Those dangling references
are exactly the bugs that used to surface only as a compile failure dozens of steps later. This
walks the IR and returns one actionable error per unresolved reference (with a JSON-path-ish
location), so the loop can refuse to advance until the list is empty.

Pure function over a schema-valid IR dict — no engine, no I/O. Asset references
(background / sprite / icon) are deliberately NOT checked here: assets live in the separate asset
manifest, so resolving them is a cross-schema check against that manifest, not this one.
"""

from typing import Dict, List

# Reference kinds name an id that lives in ANOTHER slice (a jump's node, an opponent's character):
# the fix is to repoint the reference, so the failure belongs to the slice that HOLDS it. Declaration
# kinds name something that must be DECLARED (a variable/flag/item/stat); the fix is to declare it
# wherever that kind is declared (the spine's set_*_meta), so those stay on the declaring component.
_REFERENCE_KINDS = frozenset({"node", "character", "place", "encounter", "combatant",
                              "ability"})


def slice_token(path: str) -> str:
    """The IR-slice token a crossref path belongs to — the routing key for failure attribution.
    `card_matches[mid].opponent` → 'card_matches'; `nodes[nid].end.target` → 'nodes'. `start` is
    split one deeper (start.node vs start.place point at different slices)."""
    if path.startswith("start."):
        return ".".join(path.split(".")[:2])
    for sep in ("[", "."):
        if sep in path:
            return path.split(sep, 1)[0]
    return path


def is_reference_kind(kind: str) -> bool:
    """A reference (repoint to fix) routes to the slice that holds it; a declaration (declare to
    fix) stays on the spine. See _REFERENCE_KINDS."""
    return kind in _REFERENCE_KINDS


def crossref_errors(ir: Dict) -> List[str]:
    """Return one error string per unresolved id reference. Empty list = every reference resolves."""
    return [r["message"] for r in crossref_records(ir)]


def crossref_records(ir: Dict) -> List[Dict]:
    """Structured form of crossref_errors: one record {path, ref, kind, message} per unresolved
    reference. `path` locates the bad reference (its leading token names the IR slice that holds
    it); `kind` is what failed to resolve. The compile gate joins `message`s; failure attribution
    reads `path`+`kind` to route each error to the component that can fix it."""
    chars = {c["id"] for c in ir.get("characters", [])}
    nodes = {n["id"] for n in ir.get("nodes", [])}
    places = {p["id"] for p in ir.get("places", [])}
    items = {i["id"] for i in ir.get("items", [])}
    variables = {v["id"] for v in ir.get("variables", [])}
    flags = set(ir.get("flags", []))
    stats = {s["id"] for s in ir.get("stats", [])}
    statuses = {s["id"] for s in ir.get("statuses", [])}
    abilities = {a["id"] for a in ir.get("abilities", [])}
    combatants = {c["id"] for c in ir.get("combatants", [])}
    encounters = {e["id"] for e in ir.get("encounters", [])}

    records: List[Dict] = []

    def bad(path: str, ref, kind: str) -> None:
        records.append({"path": path, "ref": ref, "kind": kind,
                        "message": f"{path}: {ref!r} is not a declared {kind}"})

    def check_condition(cond, path: str) -> None:
        if not isinstance(cond, dict):
            return
        if "item" in cond:
            if cond["item"] not in items:
                bad(path, cond["item"], "item")
        elif "flag" in cond:
            if cond["flag"] not in flags:
                bad(path, cond["flag"], "flag")
        elif "var" in cond:
            if cond["var"] not in variables:
                bad(f"{path}.var", cond["var"], "variable")
            val = cond.get("value")
            if isinstance(val, dict) and val.get("var") not in variables:
                bad(f"{path}.value.var", val.get("var"), "variable")
        elif "not" in cond:
            check_condition(cond["not"], f"{path}.not")
        elif "all" in cond:
            for i, c in enumerate(cond["all"]):
                check_condition(c, f"{path}.all[{i}]")
        elif "any" in cond:
            for i, c in enumerate(cond["any"]):
                check_condition(c, f"{path}.any[{i}]")

    def check_effect(e, path: str) -> None:
        """A narrative effect (flags / vars / items)."""
        if "set_flag" in e and e["set_flag"] not in flags:
            bad(path, e["set_flag"], "flag")
        elif "clear_flag" in e and e["clear_flag"] not in flags:
            bad(path, e["clear_flag"], "flag")
        elif "add_item" in e and e["add_item"] not in items:
            bad(path, e["add_item"], "item")
        elif "remove_item" in e and e["remove_item"] not in items:
            bad(path, e["remove_item"], "item")
        elif "set_var" in e and e["set_var"]["var"] not in variables:
            bad(path, e["set_var"]["var"], "variable")
        elif "add_var" in e and e["add_var"]["var"] not in variables:
            bad(path, e["add_var"]["var"], "variable")

    def check_effects(effects, path: str) -> None:
        for i, e in enumerate(effects or []):
            check_effect(e, f"{path}[{i}]")

    def check_combat_effect(ce, path: str) -> None:
        """An ability/status effect — operates on stats/statuses, or bridges to world state."""
        if not isinstance(ce, dict):
            return
        if "world" in ce:
            check_effect(ce["world"], f"{path}.world")
        elif "status" in ce:
            if ce["status"] not in statuses:
                bad(f"{path}.status", ce["status"], "status")
        elif "stat" in ce:
            if ce["stat"] not in stats:
                bad(f"{path}.stat", ce["stat"], "stat")
            sw = (ce.get("formula") or {}).get("scales_with")
            if sw is not None and sw not in stats:
                bad(f"{path}.formula.scales_with", sw, "stat")

    def check_outcome(outcome, path: str) -> None:
        if isinstance(outcome, dict):
            check_effects(outcome.get("effects"), f"{path}.effects")

    def check_node_end(end, path: str) -> None:
        if not isinstance(end, dict):
            return
        if end.get("type") == "jump":
            if end["target"] not in nodes:
                bad(f"{path}.target", end["target"], "node")
        elif end.get("type") == "menu":
            for i, ch in enumerate(end.get("choices", [])):
                if ch["target"] not in nodes:
                    bad(f"{path}.choices[{i}].target", ch["target"], "node")
                if "requires" in ch:
                    check_condition(ch["requires"], f"{path}.choices[{i}].requires")
                check_effects(ch.get("effects"), f"{path}.choices[{i}].effects")

    def check_action(action: Dict, path: str) -> None:
        t = action.get("type")
        if t == "take":
            if action["item"] not in items:
                bad(f"{path}.item", action["item"], "item")
        elif t == "talk":
            if action["node"] not in nodes:
                records.append({
                    "path": f"{path}.node", "ref": action["node"], "kind": "node",
                    "message": (
                        f"{path}.node: talk targets node '{action['node']}' which does not exist — "
                        f"either change this hotspot to an examine action (edit_place), or "
                        f"write the dialogue node '{action['node']}' in the `nodes` component.")})
        elif t == "move":
            if action["target"] not in places:
                bad(f"{path}.target", action["target"], "place")
            if "requires" in action:
                check_condition(action["requires"], f"{path}.requires")
        elif t == "use":
            for i, clause in enumerate(action.get("clauses", [])):
                check_condition(clause.get("requires"), f"{path}.clauses[{i}].requires")
                check_outcome(clause.get("outcome"), f"{path}.clauses[{i}].outcome")
            if "fallback" in action:
                check_outcome(action["fallback"], f"{path}.fallback")
        elif t == "win":
            if "requires" in action:
                check_condition(action["requires"], f"{path}.requires")
        elif t == "start_combat":
            if action["encounter"] not in encounters:
                bad(f"{path}.encounter", action["encounter"], "encounter")
            if "requires" in action:
                check_condition(action["requires"], f"{path}.requires")
        # examine resolves nothing

    # ── narrative ──────────────────────────────────────────────────────────
    for node in ir.get("nodes", []):
        nid = node.get("id")
        for i, line in enumerate(node.get("lines", [])):
            sp = line.get("speaker")
            if sp is not None and sp not in chars:
                bad(f"nodes[{nid}].lines[{i}].speaker", sp, "character")
            check_effects(line.get("effects"), f"nodes[{nid}].lines[{i}].effects")
        check_node_end(node.get("end", {}), f"nodes[{nid}].end")

    for place in ir.get("places", []):
        pid = place.get("id")
        for i, it in enumerate(place.get("interactables", [])):
            check_action(it.get("action", {}),
                         f"places[{pid}].interactables[{it.get('id', i)}].action")

    if "goal" in ir:
        check_condition(ir["goal"], "goal")

    start = ir.get("start", {})
    if "place" in start and start["place"] not in places:
        bad("start.place", start["place"], "place")
    if "node" in start and start["node"] not in nodes:
        bad("start.node", start["node"], "node")

    # ── combat ─────────────────────────────────────────────────────────────
    for ab in ir.get("abilities", []):
        aid = ab.get("id")
        for i, c in enumerate(ab.get("cost", [])):
            if c["stat"] not in stats:
                bad(f"abilities[{aid}].cost[{i}].stat", c["stat"], "stat")
        for i, ce in enumerate(ab.get("effects", [])):
            check_combat_effect(ce, f"abilities[{aid}].effects[{i}]")
        if "requires" in ab:
            check_condition(ab["requires"], f"abilities[{aid}].requires")

    for st in ir.get("statuses", []):
        sid = st.get("id")
        for i, ce in enumerate(st.get("tick", [])):
            check_combat_effect(ce, f"statuses[{sid}].tick[{i}]")

    for cb in ir.get("combatants", []):
        cid = cb.get("id")
        if "character" in cb and cb["character"] not in chars:
            bad(f"combatants[{cid}].character", cb["character"], "character")
        for i, sv in enumerate(cb.get("stats", [])):
            if sv["stat"] not in stats:
                bad(f"combatants[{cid}].stats[{i}].stat", sv["stat"], "stat")
        for i, ab in enumerate(cb.get("abilities", [])):
            if ab not in abilities:
                bad(f"combatants[{cid}].abilities[{i}]", ab, "ability")

    backgrounds = {b["id"] for b in ir.get("backgrounds", []) if isinstance(b, dict)}
    for enc in ir.get("encounters", []):
        eid = enc.get("id")
        for i, c in enumerate(enc.get("combatants", [])):
            if c["ref"] not in combatants:
                bad(f"encounters[{eid}].combatants[{i}].ref", c["ref"], "combatant")
        # observed: a live build shipped ok=True with an encounter background that existed
        # nowhere in the manifest — every other background reference is gated, this one wasn't
        if backgrounds and enc.get("background") and enc["background"] not in backgrounds:
            records.append({
                "path": f"encounters[{eid}].background", "ref": enc["background"],
                "kind": "background", "message": (
                    f"encounters[{eid}].background: '{enc['background']}' is not in "
                    f"asset_manifest.backgrounds {sorted(backgrounds)} — set it to an existing "
                    f"background id.")})
        for key in ("victory", "defeat"):
            ec = enc.get(key)
            if isinstance(ec, dict) and "when" in ec:
                check_condition(ec["when"], f"encounters[{eid}].{key}.when")
        for key in ("on_victory", "on_defeat"):
            if key in enc:
                check_node_end(enc[key], f"encounters[{eid}].{key}")

    return records
