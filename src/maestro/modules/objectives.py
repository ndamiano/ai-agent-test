"""objectives — the goal state machine over the flag substrate. Authors the `objectives` component.

One objective = one quest as a strict step chain: every step but the last carries `advance` (the
condition that completes it — {"flag"}, {"item"} (holding it advances, no dead flag), or the full
condition grammar), and the last carries `resolutions` (how the quest can end, each landing a flag).
CODE OWNS THE TRANSITION SEMANTICS (the storyline-terminus rule generalized): the model picks an
archetype from the library below and fills its slots — titles, summaries, conditions, journal text —
it never invents how steps advance or resolve. States compile to ordinary flags/items, so the
runtime needs no new condition grammar and no new state store: the journal/HUD derive the current
state from flags at read time.

Demand comes from the bible: each tension fans exactly one objective (the main tension's objective
is the win path). The tension id rides the on-disk component as provenance and is STRIPPED at
assemble — the bible never reaches the IR.

Example world:
  - "the levy is bleeding the town dry" (main tension) → a broker quest: hear the guild, take the
    ledger, side with guild or keep — bible + world + objectives (+ scenes for the dialogue)
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from maestro import context_render as cr
from maestro.modules import checks, views
from maestro.modules.module import Check, Error, Module, register_module


# ── the archetype library (STEP TEMPLATES — code owns the shape) ──────────────
# min_steps counts ALL steps (advance steps + the final resolution step); `resolutions` is the
# (lo, hi) count the final step must carry. The blurb is the model-facing one-liner.
ARCHETYPES: Dict[str, Dict] = {
    "fetch": {"blurb": "recover the means, then act on the target",
              "min_steps": 2, "resolutions": (1, 1)},
    "escort": {"blurb": "bring someone or something safely to where it must go",
               "min_steps": 2, "resolutions": (1, 1)},
    "investigate": {"blurb": "find the source of a wrong, then end it",
                    "min_steps": 2, "resolutions": (1, 1)},
    "broker": {"blurb": "hear the sides, gather leverage, choose between them",
               "min_steps": 3, "resolutions": (2, 3)},
    "moral_fork": {"blurb": "one hard decision, two resolutions, no wrong answer — the world reacts",
                   "min_steps": 2, "resolutions": (2, 2)},
}


def _ir_defs() -> Dict:
    import json
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[3] / "docs" / "game_ir.schema.json")
                      .read_text(encoding="utf-8"))["$defs"]


_COND_VALIDATOR = None


def condition_error(cond) -> Optional[str]:
    """A step's advance / a resolution's requires must be a REAL condition from the shared grammar
    ({"flag"}, {"item"}, {"var","op","value"}, all/any/not) — validated against the IR schema's
    condition def, the same contract every gate in the game already honours."""
    if not isinstance(cond, dict) or not cond:
        return ('must be a condition object — {"flag": "x"}, {"item": "item_y"}, '
                '{"var": "g", "op": ">=", "value": 3}, or all/any/not over those')
    global _COND_VALIDATOR
    if _COND_VALIDATOR is None:
        import jsonschema
        _COND_VALIDATOR = jsonschema.Draft202012Validator(
            {"$ref": "#/$defs/condition", "$defs": _ir_defs()})
    errs = sorted(_COND_VALIDATOR.iter_errors(cond), key=lambda e: len(list(e.path)))
    if errs:
        loc = "/".join(str(p) for p in errs[0].path) or "condition"
        return f"invalid condition at {loc}: {errs[0].message}"
    return None


# ── structural validators (write-time, one objective at a time) ───────────────
def required_journal_keys(o: Dict) -> Set[str]:
    """The journal states an objective must narrate: 'offered' (until the first step completes),
    one entry per ADVANCE step (shown after it completes — what to do next), and one per
    resolution. The final step's own id is an OPTIONAL extra key (shown when every advance step is
    done and only the final act remains)."""
    steps = o.get("steps") or []
    keys = {"offered"}
    for s in steps[:-1]:
        if isinstance(s, dict) and s.get("id"):
            keys.add(s["id"])
    last = steps[-1] if steps and isinstance(steps[-1], dict) else {}
    for r in last.get("resolutions") or []:
        if isinstance(r, dict) and r.get("id"):
            keys.add(f"resolved.{r['id']}")
    return keys


_OBJECTIVE_KEYS = frozenset({"id", "tension", "archetype", "title", "main", "steps", "journal"})
_STEP_KEYS = frozenset({"id", "summary", "advance", "resolutions"})
_RESOLUTION_KEYS = frozenset({"id", "flag", "summary", "requires"})


def v_resolution_one(r: Dict) -> Optional[str]:
    if not isinstance(r, dict):
        return "a resolution must be a JSON object"
    if not r.get("id"):
        return "a resolution needs an 'id' (e.g. 'opened')"
    unknown = sorted(set(r) - _RESOLUTION_KEYS)
    if unknown:
        return (f"resolution {r.get('id')!r} does not take {unknown} — its keys are "
                f"{sorted(_RESOLUTION_KEYS)}")
    if not isinstance(r.get("flag"), str) or not r["flag"]:
        return (f"resolution {r.get('id')!r} needs a 'flag' (the flag the world checks after this "
                f"ending — gates and the win goal read it)")
    if "requires" in r:
        err = condition_error(r["requires"])
        if err:
            return f"resolution {r['id']!r} requires: {err}"
    return None


def v_objective_one(o: Dict) -> Optional[str]:
    """The full structural gate for ONE objective — the archetype's step template enforced in code.
    Shared by add_objective / edit_objective_step (born-compliant, journal included) and v_objectives."""
    if not isinstance(o, dict):
        return "an objective must be a JSON object"
    if not o.get("id"):
        return "an objective needs an 'id' (e.g. 'obj_levy')"
    oid = o["id"]
    if not isinstance(o.get("tension"), str) or not o["tension"]:
        return f"objective {oid!r} needs 'tension': the bible tension id it answers"
    arch = ARCHETYPES.get(o.get("archetype"))
    if arch is None:
        return (f"objective {oid!r}: archetype {o.get('archetype')!r} is not in the library — "
                f"one of {sorted(ARCHETYPES)}")
    if not o.get("title"):
        return f"objective {oid!r} needs a 'title' (the quest name the player sees)"
    unknown = sorted(set(o) - _OBJECTIVE_KEYS)
    if unknown:
        return (f"objective {oid!r} does not take {unknown} — its keys are "
                f"{sorted(_OBJECTIVE_KEYS)} (code owns the shape; no free-form fields)")
    steps = o.get("steps")
    if not isinstance(steps, list) or len(steps) < arch["min_steps"]:
        return (f"objective {oid!r}: a {o['archetype']} needs at least {arch['min_steps']} steps "
                f"(advance steps building to one final resolution step)")
    seen: Set[str] = set()
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not s.get("id"):
            return f"objective {oid!r} steps[{i}] needs an 'id' (e.g. 's{i + 1}')"
        if s["id"] in seen:
            return f"objective {oid!r}: duplicate step id {s['id']!r}"
        seen.add(s["id"])
        if not s.get("summary"):
            return f"objective {oid!r} step {s['id']!r} needs a 'summary' (what the player does)"
        unknown = sorted(set(s) - _STEP_KEYS)
        if unknown:
            return (f"objective {oid!r} step {s['id']!r} does not take {unknown} — a step is "
                    f"{{id, summary, advance}} or (final only) {{id, summary, resolutions}}")
        final = i == len(steps) - 1
        if final:
            if "advance" in s:
                return (f"objective {oid!r} step {s['id']!r}: the FINAL step carries 'resolutions', "
                        f"never 'advance' — completing a resolution IS how the chain ends")
            lo, hi = arch["resolutions"]
            res = s.get("resolutions")
            if not isinstance(res, list) or not (lo <= len(res) <= hi):
                return (f"objective {oid!r}: a {o['archetype']} ends with "
                        f"{lo if lo == hi else f'{lo}-{hi}'} resolution(s) on its final step "
                        f"(got {len(res) if isinstance(res, list) else 'none'})")
            rids = set()
            for r in res:
                err = v_resolution_one(r)
                if err:
                    return f"objective {oid!r}: {err}"
                if r["id"] in rids:
                    return f"objective {oid!r}: duplicate resolution id {r['id']!r}"
                rids.add(r["id"])
        else:
            if "resolutions" in s:
                return (f"objective {oid!r} step {s['id']!r}: only the FINAL step carries "
                        f"'resolutions' — an earlier step advances on a condition")
            err = condition_error(s.get("advance"))
            if err:
                return f"objective {oid!r} step {s['id']!r} advance: {err}"
    journal = o.get("journal")
    if not isinstance(journal, dict):
        return f"objective {oid!r} needs a 'journal' object (state -> the entry the player reads)"
    missing = sorted(k for k in required_journal_keys(o)
                     if not (isinstance(journal.get(k), str) and journal[k].strip()))
    if missing:
        return (f"objective {oid!r} journal is missing text for {missing} — one entry per state: "
                f"'offered', each advance step's id (what to do AFTER it completes), and "
                f"'resolved.<id>' per resolution")
    return None


def v_objectives(c: Dict) -> Optional[str]:
    """The DONE shape of the whole component (gates a full write_component overwrite): unique-id
    objectives with exactly one 'main' (the win path). Several objectives may hang off one tension
    — the gold game runs three quests off a single tension web."""
    objs = c.get("objectives")
    if not isinstance(objs, list) or not objs:
        return "objectives.objectives must be a non-empty list of objective objects"
    ids: Set[str] = set()
    mains = 0
    for i, o in enumerate(objs):
        err = v_objective_one(o)
        if err:
            return f"objectives.objectives[{i}]: {err}"
        if o["id"] in ids:
            return f"objectives.objectives: duplicate objective id {o['id']!r}"
        ids.add(o["id"])
        if o.get("main"):
            mains += 1
    if mains != 1:
        return f"objectives needs exactly one main objective (the win path); found {mains}"
    return None


# ── authoring skeleton (small-model: skeleton first) ──────────────────────────
SKEL_OBJECTIVE_ONE = (
    '// objective_id + `tension` are code-assigned from YOUR TARGET\'s tension. `content` is this\n'
    '// ONE objective — a quest as a strict step chain over flags/items (code owns transitions):\n'
    '{\n'
    '  "archetype": "fetch | escort | investigate | broker | moral_fork",\n'
    '  "title": "<short quest name the player sees>",\n'
    '  "steps": [\n'
    '    {"id": "s1", "summary": "hear the giver\'s case (name who, where)",\n'
    '     "advance": {"flag": "levy_heard"}},\n'
    '    {"id": "s2", "summary": "recover the means (name the place it waits in)",\n'
    '     "advance": {"item": "item_crank"}},   // holding the item advances — no extra flag\n'
    '    {"id": "s3", "summary": "the final act", "resolutions": [\n'
    '      {"id": "opened", "flag": "weir_open"},\n'
    '      {"id": "paid", "flag": "debt_paid", "requires": {"item": "coin_pouch"}}]}\n'
    '  ],\n'
    '  "journal": {\n'
    '    "offered": "the quest as pitched — shown until the first step completes",\n'
    '    "s1": "shown AFTER s1 completes: what to do next, concretely",\n'
    '    "s2": "shown AFTER s2 completes",\n'
    '    "s3": "OPTIONAL: shown when every earlier step is done and only the final act remains",\n'
    '    "resolved.opened": "the aftermath, one entry per resolution",\n'
    '    "resolved.paid": "..."\n'
    '  }\n'
    '}\n'
    '// EVERY step except the last carries `advance` — the condition completing it: {"flag":"x"},\n'
    '//   {"item":"item_y"}, or {"all":[...]} over those. The LAST step carries `resolutions`\n'
    '//   (count fixed by the archetype); each resolution\'s flag is what the world checks after.\n'
    '// Cite flags/items real content already produces where they exist (see the indexes); a new\n'
    '//   flag becomes a to-do for the dialogue/world authors — name it like an event, in-world.'
)


# ── presentation blocks (this module owns how its component appears elsewhere) ─
def _objectives(artifact: Dict) -> List[Dict]:
    return [o for o in (artifact.get("objectives") or {}).get("objectives") or []
            if isinstance(o, dict) and o.get("id")]


def archetype_block() -> List[str]:
    return ["", "ARCHETYPES (pick the one that fits the tension; code enforces its step shape):",
            *(f"  {aid} — {a['blurb']} (>= {a['min_steps']} steps, "
              f"{a['resolutions'][0] if a['resolutions'][0] == a['resolutions'][1] else '%d-%d' % a['resolutions']} resolution(s))"
              for aid, a in ARCHETYPES.items())]


def objectives_block(artifact: Dict) -> List[str]:
    """The quest chains rendered compactly for OTHER modules' prompts — the flags/items each step
    advances on are the wiring contract the dialogue/world authors must produce."""
    import json
    objs = _objectives(artifact)
    if not objs:
        return []
    out = ["", "OBJECTIVES (the quest chains — scenes/hotspots must PRODUCE the flags/items each "
               "step advances on):"]
    for o in objs:
        tag = "MAIN, the win path" if o.get("main") else "side"
        out.append(f"  {o['id']} ({o.get('archetype', '?')}, {tag}): {o.get('title', '')}")
        for s in o.get("steps") or []:
            if "resolutions" in s:
                ends = ", ".join(f"{r.get('id')}→{r.get('flag')}" for r in s["resolutions"] or [])
                out.append(f"    {s.get('id')} — {s.get('summary', '')} [ends: {ends}]")
            else:
                out.append(f"    {s.get('id')} — {s.get('summary', '')} "
                           f"[advance: {json.dumps(s.get('advance'), ensure_ascii=False)}]")
    return out


def _bible_tensions(artifact: Dict) -> List[Dict]:
    return [t for t in (artifact.get("bible") or {}).get("tensions") or []
            if isinstance(t, dict) and t.get("id")]


def objectives_view(artifact: Dict) -> Dict:
    """The slot-guard's view: objective ids (no-overwrite keys) + the tensions still owed an
    objective, main first (the win path is authored before the side quests)."""
    covered = {o.get("tension") for o in _objectives(artifact)}
    open_t = [t for t in _bible_tensions(artifact) if t["id"] not in covered]
    open_t.sort(key=lambda t: t.get("scale") != "main")   # stable: declaration order, main first
    return {"objective_ids": [o["id"] for o in _objectives(artifact)],
            "open_tensions": [{"id": t["id"], "scale": t.get("scale", "side")} for t in open_t]}


# ── slot guard: code fills the assigned tension + a deterministic objective id ─
def _obj_id(tension_id: str, taken) -> str:
    cand = "obj_" + re.sub(r"^tension_", "", tension_id)
    return cand if cand not in set(taken or []) else f"obj_{tension_id}"


def _one(_view) -> int:
    """One objective per step — each is authored seeing the chains already written, the main one
    first (the same sequential-coherence rule as bible tensions / story beats)."""
    return 1


def _pick_tension(view: Dict, slot: int) -> Optional[Dict]:
    open_t = view.get("open_tensions") or []
    if not open_t:
        return None
    t = open_t[min(slot, len(open_t) - 1)]
    return {"id": _obj_id(t["id"], view.get("objective_ids")), "tension": t["id"]}


def _stamp_objective(view: Dict, assigned: Optional[Dict], args: Dict) -> Dict:
    """The guard's prepare hook: code-fill the write's identity — the objective id and the tension
    it answers come from the assigned slot, never the model (a model-picked id is the surface
    collisions live on; the tension is the demand key)."""
    args = dict(args or {})
    if assigned is not None:
        args["objective_id"] = assigned["id"]
        content = args.get("content")
        if isinstance(content, dict):
            content["tension"] = assigned["tension"]
    return args


_ADD_TOOLS = frozenset({"add_objective", "read_component", "request_review"})
_EDIT_TOOLS = frozenset({"edit_objective_step", "read_component", "request_review"})
# Additive micro-tools (add_effect/add_interactable) + the surgical remove_gate — never
# edit_node/edit_place: a replace-shaped edit lets the wiring fixer cannibalize an existing
# effect or gate to close this error (the fix-A-breaks-B churn observed live).
_WIRE_TOOLS = frozenset({"edit_objective_step", "read_component", "read_node", "read_place",
                         "add_effect", "remove_gate", "add_interactable", "request_review"})
_OBJ_GUARD = {"count_tool": "add_objective", "id_key": "objective_id",
              "id_list_key": "objective_ids", "noun": "objective", "cap": _one,
              "assign": _pick_tension, "prepare": _stamp_objective}


# ── the wiring walk: producers + the gated world/node graph ───────────────────
def _positive_refs(cond) -> Set[str]:
    """Flags/vars/items a condition requires POSITIVELY (a `not` subtree demands absence — it
    needs no producer and never closes an edge)."""
    if not isinstance(cond, dict):
        return set()
    out: Set[str] = set()
    for k in ("flag", "var"):
        if isinstance(cond.get(k), str):
            out.add(cond[k])
    it = cond.get("item")
    if isinstance(it, str):
        out.add(it)
    elif isinstance(it, list):
        out |= {x for x in it if isinstance(x, str)}
    for k in ("all", "any"):
        for c in cond.get(k, []) or []:
            out |= _positive_refs(c)
    return out


def _step_grants(step: Dict) -> Set[str]:
    """What completing this step makes true: an advance step grants its condition's positive refs;
    the final step grants its resolution flags."""
    if "resolutions" in step:
        return {r["flag"] for r in step.get("resolutions") or []
                if isinstance(r, dict) and isinstance(r.get("flag"), str)}
    return _positive_refs(step.get("advance"))


def _producers(artifact: Dict) -> Dict[str, List[Tuple]]:
    """ref -> the sites that produce it. A site is ('node', nid, [gates]) — a line/menu-choice
    effect; ('place', pid, [gates]) — a take hotspot or a use outcome (the clause's requires is the
    gate); or ('free',) — combat world-effects and system-derived leveled variables, reachable by
    definition (a fight/derivation isn't on the walk graph)."""
    prods: Dict[str, List[Tuple]] = {}

    def add(ref, site):
        if isinstance(ref, str) and ref:
            prods.setdefault(ref, []).append(site)

    def add_effects(effs, site):
        for eff in effs or []:
            if not isinstance(eff, dict):
                continue
            for ref in checks.effect_targets(eff):
                if eff.get("clear_flag") != ref:   # clearing produces absence, not the flag
                    add(ref, site)
            if isinstance(eff.get("add_item"), str):
                add(eff["add_item"], site)

    _, nodes = views.nodes_of(artifact)
    for nid, n in nodes.items():
        for ln in n.get("lines", []) or []:
            if isinstance(ln, dict):
                add_effects(ln.get("effects"), ("node", nid, []))
        end = n.get("end", {}) or {}
        if end.get("type") == "menu":
            for ch in end.get("choices", []) or []:
                gates = [ch["requires"]] if isinstance(ch.get("requires"), dict) else []
                add_effects(ch.get("effects"), ("node", nid, gates))

    pc = artifact.get("places") or {}
    for pid, p in (pc.get("places") or {}).items():
        for h in p.get("interactables", []) if isinstance(p, dict) else []:
            a = h.get("action", {}) or {}
            if a.get("type") == "take" and isinstance(a.get("item"), str):
                add(a["item"], ("place", pid, []))
            if a.get("type") == "use":
                for cl in a.get("clauses", []) or []:
                    if isinstance(cl, dict) and isinstance(cl.get("outcome"), dict):
                        gates = [cl["requires"]] if isinstance(cl.get("requires"), dict) else []
                        add_effects(cl["outcome"].get("effects"), ("place", pid, gates))
                fb = a.get("fallback")
                if isinstance(fb, dict):
                    add_effects(fb.get("effects"), ("place", pid, []))

    combat = artifact.get("combat") or {}
    for slice_key, tick_key in (("abilities", "effects"), ("statuses", "tick")):
        for row in combat.get(slice_key) or []:
            for ce in (row.get(tick_key) or []) if isinstance(row, dict) else []:
                if isinstance(ce, dict) and isinstance(ce.get("world"), dict):
                    add_effects([ce["world"]], ("free",))
    for v in pc.get("variables") or []:
        if isinstance(v, dict) and v.get("level_var"):
            add(v["level_var"], ("free",))
    prog = combat.get("progression")
    if isinstance(prog, dict) and prog.get("xp_var"):
        add(prog["xp_var"], ("free",))
    return prods


def _world_edges(artifact: Dict):
    """The player's whole traversal graph, node- and place-space fused: 'place:P'/'node:N' ids with
    gated edges (move requires, menu-choice requires, start_combat requires; talk and jump are
    free; a start_combat flows into its encounter's on_victory/on_defeat node ends). Returns
    (gated_edges, roots) — gated_edges: id -> [(target_id, [gate conditions])]."""
    gedges: Dict[str, List[Tuple[str, List]]] = {}
    _, nodes = views.nodes_of(artifact)
    pc = artifact.get("places") or {}
    places = pc.get("places") or {}

    enc_targets: Dict[str, List[str]] = {}
    for e in (artifact.get("combat") or {}).get("encounters") or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        outs = []
        for key in ("on_victory", "on_defeat"):
            ne = e.get(key)
            if isinstance(ne, dict):
                outs += [t for t in views.node_targets({"end": ne}) if t]
        enc_targets[e["id"]] = outs

    for pid, p in places.items():
        out = gedges.setdefault(f"place:{pid}", [])
        for h in p.get("interactables", []) if isinstance(p, dict) else []:
            a = h.get("action", {}) or {}
            gates = [a["requires"]] if isinstance(a.get("requires"), dict) else []
            if a.get("type") == "move" and a.get("target"):
                out.append((f"place:{a['target']}", gates))
            elif a.get("type") == "talk" and a.get("node"):
                out.append((f"node:{a['node']}", []))
            elif a.get("type") == "start_combat" and a.get("encounter"):
                for t in enc_targets.get(a["encounter"], []):
                    out.append((f"node:{t}", gates))

    for nid, n in nodes.items():
        out = gedges.setdefault(f"node:{nid}", [])
        end = n.get("end", {}) or {}
        if end.get("type") == "jump" and end.get("target"):
            out.append((f"node:{end['target']}", []))
        elif end.get("type") == "menu":
            for ch in end.get("choices", []) or []:
                if ch.get("target"):
                    gates = [ch["requires"]] if isinstance(ch.get("requires"), dict) else []
                    out.append((f"node:{ch['target']}", gates))

    if places:
        start = pc.get("start_place")
        pids = pc.get("place_ids") or list(places)
        roots = [f"place:{start if start in places else pids[0]}"]
    else:
        nodes_comp = artifact.get("nodes") or {}
        node_ids = nodes_comp.get("node_ids") or []
        start = nodes_comp.get("start") or (node_ids[0] if node_ids else None)
        roots = [f"node:{start}"] if start else []
    return gedges, roots


def _reachable_with(gedges: Dict, roots: List[str], forbidden: Set[str]) -> Set[str]:
    """Which sites the player can reach while the forbidden refs are still false: an edge gated
    POSITIVELY on any of them is closed; every other gate is passable (it belongs to an earlier
    step or an unrelated thread and will be satisfied in its own time — the liberal rule that
    catches order inversion without false-flagging side content)."""
    edges = {k: [t for t, gates in v
                 if not any(_positive_refs(g) & forbidden for g in gates)]
             for k, v in gedges.items()}
    return views.reachable(list(edges), edges, roots)


def _site_ok(site: Tuple, reach: Set[str], forbidden: Set[str]) -> bool:
    if site[0] == "free":
        return True
    if f"{site[0]}:{site[1]}" not in reach:
        return False
    return not any(_positive_refs(g) & forbidden for g in site[2])


def _blocking_gate(gedges: Dict, roots: List[str], site: Tuple, forbidden: Set[str]) -> str:
    """Name WHY the producer is out of order: walk the ungated shortest path to it and quote the
    first edge whose gate needs a forbidden ref — the concrete inversion the fix must move."""
    if site[0] == "free" or not roots:
        return ""
    plain = {k: [t for t, _ in v] for k, v in gedges.items()}
    path = views.shortest_path(roots[0], f"{site[0]}:{site[1]}", plain)
    if not path:
        return "no route from the start reaches it at all"
    for src, dst in zip(path, path[1:]):
        for t, gates in gedges.get(src, []):
            if t != dst:
                continue
            hit = set().union(*(_positive_refs(g) for g in gates)) & forbidden if gates else set()
            if hit:
                return (f"the route ({' → '.join(p.split(':', 1)[1] for p in path)}) passes a gate "
                        f"at {src.split(':', 1)[1]!r} that needs {sorted(hit)} — state this step "
                        f"(or a later one) only creates")
    own = set().union(*(_positive_refs(g) for g in site[2])) & forbidden if site[2] else set()
    if own:
        return f"the producing site itself is gated on {sorted(own)}"
    return "it sits behind a gate on this step's own (or a later step's) state"


def _has_content(artifact: Dict) -> bool:
    """Wiring is only checkable once realization content exists — world-first authors the chains
    BEFORE the places/scenes that produce their flags, so an empty world is not a wiring defect."""
    return bool((artifact.get("nodes") or {}).get("nodes")
                or (artifact.get("places") or {}).get("places"))


# ── detectors ─────────────────────────────────────────────────────────────────
def _d_demanded(chk, m, ctx):
    """Demand-from-tensions (inventory-shaped, keyed on the tension id): every bible tension fans
    exactly one objective; the main tension's objective is the win path."""
    covered = {o.get("tension") for o in _objectives(ctx.artifact)}
    out = []
    for t in sorted(_bible_tensions(ctx.artifact),
                    key=lambda t: t.get("scale") != "main"):   # declaration order, main first
        if t["id"] in covered:
            continue
        main = t.get("scale") == "main"
        out.append(Error(
            type=chk.tier, code=chk.code, component="objectives", path=t["id"], ref=t["id"],
            kind="tension",
            message=(f"tension '{t['id']}' ({t.get('scale', 'side')}) has no objective — shape it "
                     f"into a quest with add_objective: pick the archetype that fits \""
                     f"{t.get('summary', '')}\"."
                     + (" This is the MAIN tension — its objective is the game's win path."
                        if main else ""))))
    return out


def _main_stamp(objs: List[Dict], scale: Dict[str, Optional[str]]) -> List[bool]:
    """The derived main flags: the FIRST objective citing the main-scale tension is the win path
    (a tension web may carry several quests — the gold game hangs three off one tension)."""
    out, taken = [], False
    for o in objs:
        is_main = not taken and scale.get(o.get("tension")) == "main"
        out.append(is_main)
        taken = taken or is_main
    return out


def _d_one_main(chk, m, ctx):
    """`main` is DERIVED — the first objective on the bible's main tension (add_objective stamps
    it) — so a mismatch is repaired deterministically by _restamp_main, no LLM. Emits only when a
    restamp would fix it: some objective cites the main tension but the flags disagree."""
    objs = _objectives(ctx.artifact)
    if not objs:
        return []
    scale = {t["id"]: t.get("scale") for t in _bible_tensions(ctx.artifact)}
    if "main" not in scale.values():
        return []
    want = _main_stamp(objs, scale)
    wrong = [o["id"] for o, w in zip(objs, want) if bool(o.get("main")) != w]
    if not wrong or not any(want):
        return []
    return [Error(type=chk.tier, code=chk.code, component="objectives", path="#main",
                  message=(f"objective 'main' flags disagree with the bible ({wrong} mis-stamped) — "
                           f"exactly one objective is main: the main tension's."))]


def _restamp_main(module, context, error, slot, services, dispatch) -> None:
    """main derives from the cited tension's scale — a deterministic component rewrite, no LLM."""
    comp = dict(context.artifact.get("objectives") or {})
    objs = comp.get("objectives") or []
    scale = {t["id"]: t.get("scale") for t in _bible_tensions(context.artifact)}
    want = _main_stamp(objs, scale)
    comp["objectives"] = [{**o, "main": w} for o, w in zip(objs, want)]
    services.allowed = None   # code-driven step: no prior prompt scoped the tools
    result = dispatch("write_component", {"component_id": "objectives", "content": comp})
    services._report("restamped objective main flags from the bible: "
                     + ("ok" if result.get("ok") else f"error — {result.get('error')}"))


def _d_producer_order(chk, m, ctx):
    """THE path-aware wiring check: every advance ref has a producer, and that producer is
    reachable BEFORE its step — walking the gated node+place graph with this step's (and every
    later step's) state still false. A producer sitting behind its own gate, or behind a later
    step's, is an order inversion no amount of play can resolve."""
    art = ctx.artifact
    if not _has_content(art):
        return []
    prods = _producers(art)
    gedges, roots = _world_edges(art)
    out = []
    for o in _objectives(art):
        steps = o.get("steps") or []
        grants = [_step_grants(s) if isinstance(s, dict) else set() for s in steps]
        for i, s in enumerate(steps[:-1]):
            refs = _positive_refs(s.get("advance"))
            if not refs:
                continue
            forbidden = set().union(*grants[i:])
            reach = _reachable_with(gedges, roots, forbidden)
            for ref in sorted(refs):
                sites = prods.get(ref) or []
                if not sites:
                    out.append(Error(
                        type=chk.tier, code=chk.code, component="objectives",
                        path=f"{o['id']}.{s.get('id')}", ref=ref,
                        message=(f"objective '{o['id']}' step '{s.get('id')}' advances on '{ref}' "
                                 f"but NOTHING produces it — add the effect where it should happen "
                                 f"(a set_flag on a scene line/choice via edit_node, a take/use "
                                 f"outcome via edit_place/add_interactable), or repoint the step's "
                                 f"advance at state that exists (edit_objective_step).")))
                elif not any(_site_ok(site, reach, forbidden) for site in sites):
                    why = _blocking_gate(gedges, roots, sites[0], forbidden)
                    where = ", ".join(f"{st[0]} {st[1]}" for st in sites if st[0] != "free")
                    out.append(Error(
                        type=chk.tier, code=chk.code, component="objectives",
                        path=f"{o['id']}.{s.get('id')}", ref=ref,
                        message=(f"objective '{o['id']}' step '{s.get('id')}': the producer of "
                                 f"'{ref}' ({where}) is OUT OF ORDER — {why}. The player must be "
                                 f"able to reach it before this step resolves: move the producer, "
                                 f"loosen the gate, or reorder the steps (edit_objective_step).")))
    return out


def _d_resolutions_reachable(chk, m, ctx):
    """Every ending must be winnable: each resolution's flag has a producer the player can reach
    with the whole chain done (forbidden = only the resolution's own flag — a self-gated ending is
    the degenerate inversion)."""
    art = ctx.artifact
    if not _has_content(art):
        return []
    prods = _producers(art)
    gedges, roots = _world_edges(art)
    out = []
    for o in _objectives(art):
        steps = o.get("steps") or []
        last = steps[-1] if steps and isinstance(steps[-1], dict) else {}
        for r in last.get("resolutions") or []:
            flag = r.get("flag")
            if not isinstance(flag, str):
                continue
            forbidden = {flag}
            reach = _reachable_with(gedges, roots, forbidden)
            sites = prods.get(flag) or []
            if not sites:
                out.append(Error(
                    type=chk.tier, code=chk.code, component="objectives",
                    path=f"{o['id']}.{r.get('id')}", ref=flag,
                    message=(f"objective '{o['id']}' resolution '{r.get('id')}' lands flag "
                             f"'{flag}' but NOTHING sets it — the ending can never happen. Add "
                             f"the set_flag where the resolution occurs (edit_node/edit_place/"
                             f"add_interactable), or repoint it (edit_objective_step).")))
            elif not any(_site_ok(site, reach, forbidden) for site in sites):
                why = _blocking_gate(gedges, roots, sites[0], forbidden)
                out.append(Error(
                    type=chk.tier, code=chk.code, component="objectives",
                    path=f"{o['id']}.{r.get('id')}", ref=flag,
                    message=(f"objective '{o['id']}' resolution '{r.get('id')}': the producer of "
                             f"'{flag}' is unreachable — {why}. Open a route or move the producer.")))
    return out


def _d_journal(chk, m, ctx):
    """Text per state, so the journal never renders a hole: 'offered', each advance step, each
    resolution. (add_objective enforces this born-compliant; this catches later edits.)"""
    out = []
    for o in _objectives(ctx.artifact):
        journal = o.get("journal") if isinstance(o.get("journal"), dict) else {}
        missing = sorted(k for k in required_journal_keys(o)
                         if not (isinstance(journal.get(k), str) and journal[k].strip()))
        if missing:
            out.append(Error(
                type=chk.tier, code=chk.code, component="objectives", path=o["id"],
                message=(f"objective '{o['id']}' journal is missing entries for {missing} — write "
                         f"them with edit_objective_step('{o['id']}', journal={{...}}): 'offered' "
                         f"is the pitch, a step's entry is what to do AFTER it completes, "
                         f"'resolved.<id>' is the aftermath.")))
    return out


def _ctx_wire(module, rd: Dict) -> str:
    """The wiring fixer edits OTHER components (a set_flag effect via add_effect, a use hotspot
    via add_interactable) — it needs the id-level indexes of what actually exists to target,
    not just the objectives digest (ctx_structural), or it guesses ids blind and thrashes. It also
    needs the PRODUCED-state catalog: without it, "repoint at state that exists" degenerates into a
    shell game — every unproducible flag gets repointed at another unproducible flag."""
    from maestro.modules import inventory, scenes, world
    art = rd.get("artifact") or {}
    lines = cr.target_block(rd) + objectives_block(art)
    lines += world.places_index_block(art)
    lines += scenes.nodes_index_block(art)
    lines += inventory.item_index(art)
    prods = _producers(art)
    if prods:
        lines += ["", "STATE THAT EXISTS (has a producer — the ONLY legal repoint targets):"]
        for ref in sorted(prods):
            sites = ", ".join(f"{s[0]} {s[1]}" if len(s) > 1 else s[0] for s in prods[ref][:3])
            lines.append(f"  {ref} — produced at: {sites}")
    lines += ["", "Any flag NOT listed above has NO producer — repointing at it moves the error, "
              "it does not fix it. If the state the step needs is not listed, ADD its producer "
              "instead (add_effect / add_interactable)."]
    lines += cr.tail_block(rd)
    lines += ["", "Make the one change that clears the target — use the EXACT ids above."]
    return "\n".join(lines)


def _d_crossref(chk, m, ctx):
    """The objectives IR slice's dangling references (a condition naming an undeclared var, a
    malformed ref). A dangling ITEM is demand — inventory authors it (same routing as world/scenes)."""
    from maestro.ir_crossref import slice_token
    has_inv = "inventory" in (ctx.spec.get("modules") or [])
    return [Error(type=chk.tier, code=chk.code, component="objectives", message=rec["message"],
                  path=rec.get("path"), ref=rec.get("ref"), kind=rec.get("kind"))
            for rec in checks.crossref_failures(ctx.artifact)
            if slice_token(rec.get("path") or "") == "objectives"
            and not (has_inv and rec.get("kind") == "item")]


class Objectives(Module):
    id = "objectives"
    layer = "engine"
    # NOT selectable=False: that would force it into EVERY composition (the always-on foundation)
    # and, being godot-only, route every plain VN to godot. layer="engine" already hides it from
    # the proposer's catalog; the world-game aspects' requires pull it in (quests lands with W3).
    selectable = True
    description = ("The goal state machine — per-tension quest chains with a journal, a win path, "
                   "and code-owned step transitions over the flag substrate.")
    priority = 45        # after the bible/cast/story plan, before the realization modules' fixes
    requires = ("bible",)
    component = "objectives"
    mode_prompt = "objectives_add.txt"
    mode_tools = _EDIT_TOOLS
    skeleton = SKEL_OBJECTIVE_ONE
    schemas = {"objectives": v_objectives}
    skeletons = {"objectives": SKEL_OBJECTIVE_ONE}
    projector = staticmethod(objectives_view)
    projected = True     # godot-only (journal/HUD render) — routes the engine like combat does
    tool_names = ("add_objective", "edit_objective_step")

    # Repairs on the chains already written come first (sequential coherence — the next objective
    # is authored against sound siblings), then the demand fan (when_clean, one per uncovered
    # tension), then the slice's crossref terminal.
    checks = [
        Check("one_main_objective", _d_one_main, job="fix", run=_restamp_main,
              tools=frozenset({"write_component", "read_component"}), skeleton=""),
        Check("producer_order", _d_producer_order, job="fix", context=_ctx_wire,
              tools=_WIRE_TOOLS, prompt="objectives_wire_fix.txt", skeleton=""),
        Check("journal_complete", _d_journal, job="fix", context=cr.ctx_structural,
              tools=_EDIT_TOOLS, prompt="objectives_journal_fix.txt", skeleton=""),
        Check("resolutions_reachable", _d_resolutions_reachable, job="fix",
              context=_ctx_wire, tools=_WIRE_TOOLS,
              prompt="objectives_wire_fix.txt", skeleton=""),
        Check("demanded_objectives", _d_demanded, when_clean=True, tools=_ADD_TOOLS,
              guard=_OBJ_GUARD, prompt="objectives_add.txt", skeleton=SKEL_OBJECTIVE_ONE),
        Check("crossref", _d_crossref, job="fix", when_clean=True,
              build_prompt=cr.crossref_correction, tools=_EDIT_TOOLS),
    ]

    def render_context(self, ctx: Dict) -> str:
        # A quest is authored FROM the world: the premise, the bible (its tension is the target),
        # the chains already written (stay distinct, don't re-gate their flags), and the id-level
        # indexes of what exists to advance on (places, scenes, items). Never scene prose.
        from maestro.modules import inventory, scenes, world
        from maestro.modules.bible import bible_block
        art = ctx.get("artifact") or {}
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        lines += bible_block(art)
        lines += archetype_block()
        lines += objectives_block(art)
        lines += world.places_index_block(art)
        lines += scenes.nodes_index_block(art)
        lines += inventory.item_index(art)
        lines += cr.tail_block(ctx)
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> List[str]:
        return objectives_block(artifact)


MODULE = Objectives()
register_module(MODULE)
