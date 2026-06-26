"""assets — the image manifest. Authors `asset_manifest`.

Owns the manifest checks: backgrounds must exist, and every listed background / character sprite
needs an id (the compiler keys art off ids). Injected downstream as ids-only — the image-generation
prose is the biggest chunk of dead weight in a node step's context.
"""

from typing import Dict, List, Optional, Tuple

from maestro.modules import checks
from maestro.modules.module import Error, ErrorType, Module, register_module


def v_asset_manifest(c: Dict) -> Optional[str]:
    for key in ("backgrounds", "characters", "cgs"):
        if not isinstance(c.get(key), list):
            return f"asset_manifest.{key} must be a list (use [] if none)"
    for i, bg in enumerate(c.get("backgrounds", [])):
        if not isinstance(bg, dict) or not bg.get("id"):
            return f"asset_manifest.backgrounds[{i}] needs an 'id' (e.g. 'bg_office')"
    for i, ch in enumerate(c.get("characters", [])):
        if not isinstance(ch, dict) or not ch.get("id"):
            return f"asset_manifest.characters[{i}] needs an 'id' matching a premise character id"
    if "items" in c:
        if not isinstance(c["items"], list):
            return "asset_manifest.items must be a list (use [] if none)"
        for i, it in enumerate(c["items"]):
            if not isinstance(it, dict) or not it.get("id"):
                return f"asset_manifest.items[{i}] needs an 'id' matching a places item id"
    if "title_card" in c and not isinstance(c["title_card"], dict):
        return "asset_manifest.title_card must be an object"
    return None


SKEL_ASSET_MANIFEST = (
    '{\n'
    '  "backgrounds": [ {"id": "bg_<place>", "image_file": "<place>.png", "description": "..."} ],\n'
    '  "characters":  [ {"id": "<same id as premise>", "image_file": "<id>.png", "description": "..."} ],\n'
    '  "items": [ {"id": "<same id as a places item>", "image_file": "<id>.png", "description": "..."} ],\n'
    '  "cgs": [],\n'
    '  "title_card": {"image_file": "title_card.png", "description": "..."}\n'
    '}\n'
    '// asset_manifest holds IMAGES, not speakers. character ids here MUST match\n'
    '//   premise.characters ids exactly. items hold inventory ICONS; their ids MUST match\n'
    '//   places items ids. Omit "items" (or use []) for a visual novel with no inventory.'
)


class Assets(Module):
    id = "assets"
    priority = 20
    component = "asset_manifest"
    mode_prompt = "mode_asset.txt"
    mode_tools = frozenset({"write_component", "update_scratchpad", "request_review"})
    skeleton = SKEL_ASSET_MANIFEST
    schemas = {"asset_manifest": v_asset_manifest}
    skeletons = {"asset_manifest": SKEL_ASSET_MANIFEST}

    def affected_components(self) -> Tuple[str, ...]:
        return ("asset_manifest",)

    def get_errors(self, context) -> List[Error]:
        art = context.artifact
        errs: List[Error] = []

        def build(result, code):
            return checks.as_error(result, type=ErrorType.BUILD, code=code, component="asset_manifest")

        e = build(checks.exists(art, "asset_manifest.backgrounds"), "backgrounds_exist")
        if e:
            errs.append(e)
            return errs  # nothing to id-check until backgrounds exist
        ie = build(checks.each_has(art, "asset_manifest.backgrounds", fields=["id"]), "background_ids")
        if ie:
            errs.append(ie)
        chars = (art.get("asset_manifest") or {}).get("characters")
        if isinstance(chars, list) and chars:
            ce = build(checks.each_has(art, "asset_manifest.characters", fields=["id"]), "character_ids")
            if ce:
                errs.append(ce)
        return errs

    def context_view(self, c: Dict) -> Dict:
        def ids(key):
            return [x["id"] for x in (c.get(key) or []) if isinstance(x, dict) and x.get("id")]
        view = {"backgrounds": ids("backgrounds"), "characters": ids("characters")}
        if c.get("items"):
            view["items"] = ids("items")
        return view


MODULE = Assets()
register_module(MODULE)
