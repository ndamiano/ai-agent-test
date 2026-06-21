"""Component schemas + authoring skeletons — now sourced from the mechanic-module registry.

The per-component validators and skeletons moved up to maestro.discrete (substrate level, shared
by both engine backends). This module stays as the stable surface the rest of the code imports —
SCHEMAS / SKELETONS / validate_component / skeleton_guide / GENRE_COMPONENTS — but every value is
assembled from the registered modules, so adding a module (not editing this file) is how the set
grows.
"""

from typing import Callable, Dict, Optional

import maestro.discrete  # noqa: F401 — registers the discrete modules + presets on import
from maestro.modules import MODULE_REGISTRY, PRESETS, compose

SCHEMAS: Dict[str, Callable[[Dict], Optional[str]]] = {}
SKELETONS: Dict[str, str] = {}
for _m in MODULE_REGISTRY.values():
    SCHEMAS.update(_m.schemas)
    SKELETONS.update(_m.skeletons)

# Which components each preset's spec carries (derived from the preset's module composition).
GENRE_COMPONENTS: Dict[str, list] = {
    name: compose(p.modules).components for name, p in PRESETS.items()
}


def validate_component(component_id: str, content) -> Optional[str]:
    fn = SCHEMAS.get(component_id)
    if fn is None:
        return None
    if not isinstance(content, dict):
        return f"{component_id} must be a JSON object"
    return fn(content)


def skeleton_guide(component_ids=None, genre: str = "vn") -> str:
    if component_ids is None:
        preset = PRESETS.get(genre) or PRESETS["vn"]
        component_ids = compose(preset.modules).components
    blocks = [f"### {cid}\n{SKELETONS[cid]}" for cid in component_ids if cid in SKELETONS]
    return "Required component shapes (author content in exactly these shapes):\n\n" + "\n\n".join(blocks)
