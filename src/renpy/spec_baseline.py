"""Code-enforced baseline done-conditions for a Ren'Py VN spec.

The proposer LLM drafts done-conditions, but unreliably — it has frozen specs missing the
structure/quality checks entirely, letting stub scenes and orphan nodes pass. The human gate
can't be the thing that catches a dropped check (that means eyeballing JSON every freeze), so
the genre layer guarantees the floor here: every Ren'Py spec carries these checks regardless
of what the proposer emitted. A proposer may RAISE a `min` (a longer game) but never drop a
check or set a `min` below the floor.

Registered as a maestro spec-normalizer (see spec_tools.register_spec_normalizer) so it runs
at propose AND freeze — the human reviews, and freezes, the real contract.
"""

from typing import Dict, List

# component_id -> baseline done-conditions. `min`s are the floor; the proposer can only go up.
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
    "node_scripts": [
        {"type": "count", "path": "node_scripts.node_ids", "min": 20},
        {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
         "to": "node_scripts.node_ids"},
        {"type": "reachable_from_start"},
        {"type": "min_branches", "min": 2},
        {"type": "each_node_min_lines", "min": 10},
        {"type": "all_characters_speak"},
        {"type": "compiles"},
    ],
}


def _identity(check: Dict):
    # What makes two checks "the same condition" when merging: the kind, plus what it points
    # at (so two distinct `count`s on different paths don't collide).
    return (check.get("type"), check.get("path"), check.get("from"))


def enforce_baseline(spec: Dict) -> Dict:
    """Ensure every Ren'Py component the spec includes carries its baseline done-conditions.

    Adds any missing check; for one that already exists, raises its `min` to the floor (never
    lowers). Mutates and returns the spec. Components the proposer omitted entirely are left
    alone — a VN missing premise/node_scripts is a louder failure the build surfaces anyway.
    """
    by_id = {c.get("id"): c for c in spec.get("components", [])}
    for cid, baseline in _BASELINE.items():
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
