"""Code-enforced baseline done-conditions for a Game IR spec.

The proposer LLM drafts done-conditions, but unreliably — it has frozen specs missing the
structure/quality checks entirely, letting stub scenes and orphan nodes pass. The human gate
can't be the thing that catches a dropped check, so the genre layer guarantees the floor here:
every spec carries these checks regardless of what the proposer emitted. A proposer may RAISE a
`min` (a longer game) but never drop a check or set a `min` below the floor.

Registered as a maestro spec-normalizer (see spec_tools.register_spec_normalizer) so it runs at
propose AND freeze — the human reviews, and freezes, the real contract.
"""

from typing import Dict, List

# component_id -> baseline done-conditions. `min`s are the floor; the proposer can only go up.
# The body component is `nodes` (structured IR nodes); checks read it directly (no Ren'Py text).
_BASELINE: Dict[str, List[Dict]] = {
    "premise": [
        {"type": "exists", "path": "premise.central_question"},
        {"type": "count", "path": "premise.characters", "min": 3},
        {"type": "each_has", "path": "premise.characters",
         "fields": ["id", "name", "voice", "temperament", "drive", "history",
                    "competencies", "example_lines"]},
        {"type": "count", "path": "premise.endings", "min": 3},
        {"type": "distinct", "path": "premise.endings", "key": "id"},
    ],
    "asset_manifest": [
        {"type": "exists", "path": "asset_manifest.backgrounds"},
        {"type": "each_has", "path": "asset_manifest.characters", "fields": ["id"]},
    ],
    "nodes": [
        {"type": "count", "path": "nodes.node_ids", "min": 20},
        {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
         "to": "nodes.node_ids"},
        {"type": "reachable_from_start"},
        {"type": "min_branches", "min": 2},
        # One IR line == one dialogue beat (the old check counted quoted lines ~1:1), so the
        # floor is in beats; ~6 substantial beats ≈ the old 10-quoted-line target.
        {"type": "each_node_min_lines", "min": 6},
        {"type": "all_characters_speak"},
        {"type": "compiles"},
    ],
}

# Point-and-click baseline. The terminal/compiles-gated piece is `places` (it stitches the
# whole script.rpy); `nodes` is reused only for NPC dialogue that talk-hotspots call, so its
# floor is light. premise carries NPCs, not a branching cast — no endings, no 3-char minimum.
_BASELINE_PNC: Dict[str, List[Dict]] = {
    "premise": [
        {"type": "exists", "path": "premise.central_question"},
        {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]},
    ],
    "asset_manifest": [
        {"type": "exists", "path": "asset_manifest.backgrounds"},
        {"type": "each_has", "path": "asset_manifest.backgrounds", "fields": ["id"]},
    ],
    "nodes": [
        {"type": "each_node_min_lines", "min": 3},
    ],
    "places": [
        {"type": "exists", "path": "places.start_place"},
        {"type": "count", "path": "places.place_ids", "min": 3},
        {"type": "each_place_min_interactables", "min": 2},
        {"type": "places_reachable"},
        {"type": "items_obtainable"},
        {"type": "items_used"},
        {"type": "goal_reachable"},
        {"type": "compiles"},
    ],
}

_BASELINES = {"vn": _BASELINE, "point_and_click": _BASELINE_PNC}

# Intrinsic build order for a point-and-click spec: places' talk-hotspots `call` dialogue nodes
# that must exist first, and everything sits on premise/assets. dep_order is derived from deps,
# so we SET them authoritatively here — a proposer that emits its own deps can otherwise close a
# cycle. component_id -> its canonical deps.
_PNC_DEPS: Dict[str, List[str]] = {
    "premise": [],
    "asset_manifest": ["premise"],
    "nodes": ["premise"],
    "places": ["premise", "asset_manifest", "nodes"],
}


def _enforce_pnc_deps(by_id: Dict[str, Dict]) -> None:
    for cid, deps in _PNC_DEPS.items():
        comp = by_id.get(cid)
        if comp is None:
            continue
        comp["deps"] = [d for d in deps if d in by_id]


def _identity(check: Dict):
    return (check.get("type"), check.get("path"), check.get("from"))


def enforce_baseline(spec: Dict) -> Dict:
    """Ensure every component the spec includes carries its baseline done-conditions.

    Adds any missing check; for one that already exists, raises its `min` to the floor (never
    lowers). Mutates and returns the spec. Components the proposer omitted entirely are left
    alone — a missing premise/nodes is a louder failure the build surfaces anyway.
    """
    by_id = {c.get("id"): c for c in spec.get("components", [])}
    genre = spec.get("genre", "vn")
    if genre == "point_and_click":
        _enforce_pnc_deps(by_id)
    baseline_set = _BASELINES.get(genre, _BASELINE)
    for cid, baseline in baseline_set.items():
        comp = by_id.get(cid)
        if comp is None:
            continue
        existing = comp.setdefault("done_conditions", [])
        index = {_identity(c): c for c in existing if isinstance(c, dict)}
        for base in baseline:
            cur = index.get(_identity(base))
            if cur is None:
                existing.append(dict(base))
            elif "min" in base:
                cur["min"] = max(cur.get("min", base["min"]), base["min"])
    return spec
