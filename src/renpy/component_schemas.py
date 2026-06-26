"""Component schemas + authoring skeletons — sourced from the mechanic-module registry.

The per-component validators and skeletons live on the modules (substrate level, shared by both
engine backends). This module stays as the stable surface the rest of the code imports —
SCHEMAS / SKELETONS / validate_component / skeleton_guide — but every value is assembled from the
registered modules, so adding a module (not editing this file) grows the set.
"""

from typing import Callable, Dict, Optional

import maestro.modules  # noqa: F401 — registers the modules on import
from maestro.modules import MODULE_REGISTRY

SCHEMAS: Dict[str, Callable[[Dict], Optional[str]]] = {}
SKELETONS: Dict[str, str] = {}
for _m in MODULE_REGISTRY.values():
    SCHEMAS.update(_m.schemas)
    SKELETONS.update(_m.skeletons)


def validate_component(component_id: str, content) -> Optional[str]:
    fn = SCHEMAS.get(component_id)
    if fn is None:
        return None
    if not isinstance(content, dict):
        return f"{component_id} must be a JSON object"
    return fn(content)


def skeleton_guide(component_ids=None) -> str:
    component_ids = list(SKELETONS) if component_ids is None else component_ids
    blocks = [f"### {cid}\n{SKELETONS[cid]}" for cid in component_ids if cid in SKELETONS]
    return "Required component shapes (author content in exactly these shapes):\n\n" + "\n\n".join(blocks)
