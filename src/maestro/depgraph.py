"""depgraph — the reified asset dependency graph, for downstream-dirty propagation (Epic B).

Two dependency sources already exist in the system (see `docs/hitl_architecture.md`); this
re-walks them at ASSET (idkey) granularity instead of the coarser granularity each was built for:

  - reference edges  — `ir_crossref.py` proves every id reference RESOLVES (a jump target, a talk
    node, a combatant's character, ...), but only reports the broken ones and attributes them to a
    COMPONENT, not a specific item. Here we want every reference, valid or not, tied to the exact
    holder that wrote it — so we walk the decomposed components directly, mirroring ir_crossref's
    reference-kind taxonomy (node/character/place/encounter/combatant/ability) without going
    through the assembled IR or its pass/fail framing.
  - state-flow edges — `maestro/modules/state.py`'s `_walk_state` pairs every flag/var/item with
    the host COMPONENT that produces/consumes it ("nodes", "places"), not the specific node/place.
    That's enough for the wiring check (any node counts as a producer) but not for "which node
    depends on which" — so we re-walk with the same effect/condition primitives, keyed by item id.

Both normalize to one shape: `graph[X]` is the set of asset idkeys that must go dirty when X is
edited — the direction propagation walks. `build_dependency_graph` is pure (artifact in, edges
out); `mark_downstream_dirty` is the side-effecting entry point B3's tool hooks call.
"""

from typing import Dict, List, Set

from maestro.modules import checks, views
from maestro.modules.human import asset_idkey, dirty_entries, set_dirty

_PROPAGATION_NOTE = "an asset this depends on was just edited — review this for continued " \
                    "consistency and fix anything that no longer fits"


def _add(graph: Dict[str, Set[str]], edited: str, dependent: str) -> None:
    """Editing `edited` should dirty `dependent`. No self-edges (an asset never depends on itself)."""
    if edited and dependent and edited != dependent:
        graph.setdefault(edited, set()).add(dependent)


# ── (a) reference edges: edit the REFERENCED asset -> dirty its HOLDER ──────────────────────────
def _reference_edges(artifact: Dict, graph: Dict[str, Set[str]]) -> None:
    node_ids, nodes = views.nodes_of(artifact)
    for nid in node_ids:
        node = nodes.get(nid) or {}
        holder = asset_idkey("nodes", nid)
        for ln in node.get("lines", []) or []:
            if isinstance(ln, dict) and ln.get("speaker"):
                _add(graph, asset_idkey("characters", ln["speaker"]), holder)
        for target in views.node_targets(node):
            _add(graph, asset_idkey("nodes", target), holder)

    place_ids, places, _ = views.places_of(artifact)
    for pid in place_ids:
        place = places.get(pid) or {}
        holder = asset_idkey("places", pid)
        for h in place.get("interactables", []) or []:
            action = h.get("action") if isinstance(h, dict) else None
            if not isinstance(action, dict):
                continue
            t = action.get("type")
            if t == "move" and action.get("target"):
                _add(graph, asset_idkey("places", action["target"]), holder)
            elif t == "talk" and action.get("node"):
                _add(graph, asset_idkey("nodes", action["node"]), holder)
            elif t == "start_combat" and action.get("encounter"):
                _add(graph, asset_idkey("combat", action["encounter"]), holder)

    # combat lives as one on-disk blob, but each sub-entity (ability/combatant/encounter/stat/
    # status) has its own id and is itself an editable asset — "combat:<id>", same convention as
    # every other component (see human.asset_idkey's own docstring example, "combat:firebolt").
    combat = artifact.get("combat") or {}

    def _effect_ref(ce, holder: str) -> None:
        if not isinstance(ce, dict):
            return
        if ce.get("status"):
            _add(graph, asset_idkey("combat", ce["status"]), holder)
        elif ce.get("stat"):
            _add(graph, asset_idkey("combat", ce["stat"]), holder)
            scales_with = (ce.get("formula") or {}).get("scales_with")
            if scales_with:
                _add(graph, asset_idkey("combat", scales_with), holder)

    for ab in combat.get("abilities", []) or []:
        if not (isinstance(ab, dict) and ab.get("id")):
            continue
        holder = asset_idkey("combat", ab["id"])
        for c in ab.get("cost", []) or []:
            if isinstance(c, dict) and c.get("stat"):
                _add(graph, asset_idkey("combat", c["stat"]), holder)
        for ce in ab.get("effects", []) or []:
            _effect_ref(ce, holder)

    for st in combat.get("statuses", []) or []:
        if not (isinstance(st, dict) and st.get("id")):
            continue
        holder = asset_idkey("combat", st["id"])
        for ce in st.get("tick", []) or []:
            _effect_ref(ce, holder)

    for cb in combat.get("combatants", []) or []:
        if not (isinstance(cb, dict) and cb.get("id")):
            continue
        holder = asset_idkey("combat", cb["id"])
        if cb.get("character"):
            _add(graph, asset_idkey("characters", cb["character"]), holder)
        for sv in cb.get("stats", []) or []:
            if isinstance(sv, dict) and sv.get("stat"):
                _add(graph, asset_idkey("combat", sv["stat"]), holder)
        for ab in cb.get("abilities", []) or []:
            if isinstance(ab, str):
                _add(graph, asset_idkey("combat", ab), holder)

    for enc in combat.get("encounters", []) or []:
        if not (isinstance(enc, dict) and enc.get("id")):
            continue
        holder = asset_idkey("combat", enc["id"])
        for c in enc.get("combatants", []) or []:
            if isinstance(c, dict) and c.get("ref"):
                _add(graph, asset_idkey("combat", c["ref"]), holder)


# ── (b) state-flow edges: edit the PRODUCER -> dirty its CONSUMER(s) ────────────────────────────
def _state_flow_edges(artifact: Dict, graph: Dict[str, Set[str]]) -> None:
    info: Dict[str, Dict[str, Set[str]]] = {}

    def rec(sid, slot: str, holder: str) -> None:
        if sid and isinstance(sid, str) and holder:
            info.setdefault(sid, {"prod": set(), "cons": set()})[slot].add(holder)

    def scan_effects(effs, holder: str) -> None:
        for eff in effs or []:
            if not isinstance(eff, dict):
                continue
            for k in ("set_flag", "clear_flag"):
                if eff.get(k):
                    rec(eff[k], "prod", holder)
            for k in ("set_var", "add_var"):
                sv = eff.get(k)
                if isinstance(sv, dict) and sv.get("var"):
                    rec(sv["var"], "prod", holder)
            if eff.get("add_item"):
                rec(eff["add_item"], "prod", holder)
            if eff.get("remove_item"):
                rec(eff["remove_item"], "cons", holder)

    def scan_condition(cond, holder: str) -> None:
        for ref in checks.cond_state_refs(cond):
            rec(ref, "cons", holder)
        for it in views.cond_items(cond):
            rec(it, "cons", holder)

    node_ids, nodes = views.nodes_of(artifact)
    for nid in node_ids:
        node = nodes.get(nid) or {}
        holder = asset_idkey("nodes", nid)
        scan_effects(checks.node_effects(node), holder)
        end = node.get("end") or {}
        if end.get("type") == "menu":
            for ch in end.get("choices", []) or []:
                scan_condition(ch.get("requires"), holder)

    place_ids, places, _ = views.places_of(artifact)
    for pid in place_ids:
        place = places.get(pid) or {}
        holder = asset_idkey("places", pid)
        for h in place.get("interactables", []) or []:
            action = h.get("action") if isinstance(h, dict) else None
            if not isinstance(action, dict):
                continue
            if action.get("type") == "take" and action.get("item"):
                rec(action["item"], "prod", holder)
            scan_effects(views.action_effects(action), holder)
            for cond in views.action_conditions(action):
                scan_condition(cond, holder)

    for p in info.values():
        for producer in p["prod"]:
            for consumer in p["cons"]:
                _add(graph, producer, consumer)

    # The item catalog entry is itself an editable asset ("items:<id>") — renaming/rebalancing it
    # should dirty every node/place that produces (takes) or consumes (requires/uses) it.
    item_ids = {i.get("id") for i in (artifact.get("items") or {}).get("items") or []
               if isinstance(i, dict) and i.get("id")}
    for sid, p in info.items():
        if sid not in item_ids:
            continue
        item_key = asset_idkey("items", sid)
        for holder in p["prod"] | p["cons"]:
            _add(graph, item_key, holder)


def build_dependency_graph(artifact: Dict) -> Dict[str, Set[str]]:
    """The full edge set: `graph[idkey]` is every asset idkey that must go dirty when `idkey` is
    edited. Pure — no state I/O, so Epic C can call it for a "this will affect N assets" preview
    without side effects."""
    graph: Dict[str, Set[str]] = {}
    _reference_edges(artifact, graph)
    _state_flow_edges(artifact, graph)
    return graph


def downstream_closure(graph: Dict[str, Set[str]], idkey: str) -> Set[str]:
    """Every asset that depends on `idkey`, directly or transitively — never includes `idkey`
    itself, even if a cycle walks back to it (cycle-safe: `seen` gates re-visits)."""
    seen: Set[str] = set()
    frontier = [idkey]
    while frontier:
        for dependent in graph.get(frontier.pop(), ()):
            if dependent != idkey and dependent not in seen:
                seen.add(dependent)
                frontier.append(dependent)
    return seen


def mark_downstream_dirty(state, spec, idkey: str, note: str = _PROPAGATION_NOTE) -> List[str]:
    """Reflag `idkey`'s whole downstream closure dirty — even an asset blessed long ago (a bless
    only ever means "clean given upstream as it was then"; when upstream moves, the flag comes
    back). Never flags `idkey` itself. A dependent that is already dirty for a specific reason
    (a human's own thumbs-down note) keeps that note rather than being overwritten with the
    generic propagation one — only the flag itself always reflags.

    `spec` isn't needed to build the graph (the on-disk artifact is self-describing) but is taken
    for symmetry with the other (spec, state) call sites and so a future spec-scoped propagation
    rule doesn't need an interface change.
    """
    graph = build_dependency_graph(state.load_artifact())
    dependents = downstream_closure(graph, idkey)
    existing_notes = {d.get("idkey"): d.get("note") for d in dirty_entries(state)}
    flagged = sorted(dependents)
    for dep in flagged:
        set_dirty(state, dep, existing_notes.get(dep) or note)
    return flagged
