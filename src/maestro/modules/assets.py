"""assets

This module defines all of the assets required to build a game. Currently, we only support
2d games that only need images, but this will eventually expand to include everything needed to
create a full 3d game as well. This module defines art direction, prompts, which assets are needed

Example games:
  - "a quiet two-hander visual novel" — assets + cast + story + scenes
  - "a point-and-click escape room"    — assets + cast + world + inventory
"""

from typing import Dict, Optional, Tuple

from maestro.modules import checks
from maestro.modules.module import Check, Module, register_module


def locations_block(artifact: Dict) -> list:
    """Backgrounds with their descriptions — the only sense of PLACE an author has."""
    bgs = [b for b in (artifact.get("asset_manifest") or {}).get("backgrounds", [])
           if isinstance(b, dict) and b.get("id")]
    if not bgs:
        return []
    return ["", "LOCATIONS (a node's `location` / a room's `background` is one of these EXACT ids):",
            *(f"  {b['id']} — {b.get('description', '')}" for b in bgs)]


def _d_character_ids(chk, m, ctx):
    chars = (ctx.artifact.get("asset_manifest") or {}).get("characters")
    if not (isinstance(chars, list) and chars):
        return []
    return m.wrap(chk, checks.each_has(ctx.artifact, "asset_manifest.characters", fields=["id"]))


def v_asset_manifest(c: Dict) -> Optional[str]:
    for key in ("backgrounds", "characters", "cgs"):
        if not isinstance(c.get(key), list):
            return f"asset_manifest.{key} must be a list (use [] if none)"
    for i, bg in enumerate(c.get("backgrounds", [])):
        if not isinstance(bg, dict) or not bg.get("id"):
            return f"asset_manifest.backgrounds[{i}] needs an 'id' (e.g. 'bg_office')"
    for i, ch in enumerate(c.get("characters", [])):
        if not isinstance(ch, dict) or not ch.get("id"):
            return f"asset_manifest.characters[{i}] needs an 'id' matching a characters component id"
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
    '  "characters":  [ {"id": "<same id as in the characters component>", "image_file": "<id>.png", "description": "..."} ],\n'
    '  "items": [ {"id": "<same id as a places item>", "image_file": "<id>.png", "description": "..."} ],\n'
    '  "cgs": [],\n'
    '  "title_card": {"image_file": "title_card.png", "description": "..."}\n'
    '}\n'
    '// asset_manifest holds IMAGES, not speakers. character ids here MUST match\n'
    '//   characters component ids exactly. items hold inventory ICONS; their ids MUST match\n'
    '//   places items ids. Omit "items" (or use []) for a visual novel with no inventory.'
)


class Assets(Module):
    id = "assets"
    selectable = False   # always-on: every engine keys art off the manifest's ids
    priority = 20
    component = "asset_manifest"
    mode_prompt = "assets_write.txt"
    mode_tools = frozenset({"write_component", "request_review"})
    skeleton = SKEL_ASSET_MANIFEST
    schemas = {"asset_manifest": v_asset_manifest}
    skeletons = {"asset_manifest": SKEL_ASSET_MANIFEST}

    checks = [
        # Backgrounds must exist before any id-check is meaningful — blocking.
        Check("backgrounds_exist", lambda chk, m, ctx: m.wrap(chk, checks.exists(
            ctx.artifact, "asset_manifest.backgrounds")), blocking=True),
        Check("background_ids", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "asset_manifest.backgrounds", fields=["id"]))),
        Check("character_ids", _d_character_ids),
    ]

    def affected_components(self) -> Tuple[str, ...]:
        return ("asset_manifest",)

    def render_context(self, ctx: Dict) -> str:
        # The manifest derives from who and what exists: character cards (sprite descriptions come
        # from the person), the story (backgrounds come from where it happens), the item catalogue
        # (inventory icons).
        from maestro import context_render as cr
        from maestro.modules import cast, inventory, story
        art = ctx.get("artifact") or {}
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += cast.character_cards(art)
        lines += story.story_block(art)
        lines += inventory.items_block(art)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)


MODULE = Assets()
register_module(MODULE)
