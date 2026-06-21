"""Code-enforced baseline done-conditions for a Game IR spec — now module-composed.

The proposer LLM drafts done-conditions unreliably (frozen specs have shipped missing the
structure/quality checks entirely, letting stub scenes and orphan nodes pass). The human gate
can't be the thing that catches a dropped check, so the substrate layer guarantees the floor:
every spec carries the union of its active modules' baselines regardless of what the proposer
emitted. A proposer may RAISE a `min` but never drop a check or set one below the floor.

The baselines + intrinsic build order now live on the modules (maestro.discrete); enforce_baseline
composes them for the spec's module set. Registered as a maestro spec-normalizer (see
spec_tools.register_spec_normalizer) so it runs at propose AND freeze — the human reviews, and
freezes, the real contract.
"""

from typing import Dict

import maestro.discrete  # noqa: F401 — ensure the discrete modules + presets are registered
from maestro.modules import compose, modules_for


def _identity(check: Dict):
    return (check.get("type"), check.get("path"), check.get("from"))


def enforce_baseline(spec: Dict) -> Dict:
    """Ensure every component the spec includes carries its modules' baseline done-conditions.

    Adds any missing check; for one that already exists, raises its `min` to the floor (never
    lowers) and unions `each_has` fields. Sets the intrinsic build-order deps authoritatively (a
    proposer that emits its own deps can otherwise close a cycle). Components the proposer omitted
    entirely are left alone — a missing premise/nodes is a louder failure the build surfaces anyway.
    Mutates and returns the spec.
    """
    by_id = {c.get("id"): c for c in spec.get("components", [])}
    composed = compose(modules_for(spec))

    for cid, deps in composed.deps.items():
        comp = by_id.get(cid)
        if comp is not None:
            comp["deps"] = [d for d in deps if d in by_id]

    for cid, baseline in composed.baseline.items():
        comp = by_id.get(cid)
        if comp is None:
            continue
        existing = comp.setdefault("done_conditions", [])
        index = {_identity(c): c for c in existing if isinstance(c, dict)}
        for base in baseline:
            cur = index.get(_identity(base))
            if cur is None:
                existing.append(dict(base))
            else:
                if "min" in base:
                    cur["min"] = max(cur.get("min", base["min"]), base["min"])
                if isinstance(base.get("fields"), list):
                    merged = list(cur.get("fields", []))
                    for f in base["fields"]:
                        if f not in merged:
                            merged.append(f)
                    cur["fields"] = merged
    return spec
