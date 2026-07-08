"""assets

This module defines all of the assets required to build a game. Currently, we only support
2d games that only need images, but this will eventually expand to include everything needed to
create a full 3d game as well. This module defines art direction, prompts, which assets are needed

Example games:
  - "a quiet two-hander visual novel" — assets + cast + story + scenes
  - "a point-and-click escape room"    — assets + cast + world + inventory
"""

from typing import Dict, Optional, Tuple

from maestro import context_render as cr
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


def location_index(artifact: Dict) -> list:
    """Background ids + a one-clause hint, no full prose — what a crossref fix needs to repoint a
    node's `location` / a room's `background`; the full `locations_block` is for authoring calls."""
    bgs = [b for b in (artifact.get("asset_manifest") or {}).get("backgrounds", [])
           if isinstance(b, dict) and b.get("id")]
    if not bgs:
        return []
    return ["", "LOCATIONS (these EXACT background ids):",
            *(f"  {b['id']} — {(b.get('description', '') or '')[:60]}" for b in bgs)]


# The manifest repairs round-trip through write_component: there is no per-entry edit tool, so the
# fix reads the manifest then rewrites it with ONE change. read_component is added explicitly because
# the module's authoring mode_tools omit it. The prompts below forbid a clobber-rewrite.
_REPAIR_TOOLS = frozenset({"read_component", "write_component", "request_review"})


def _d_character_ids(chk, m, ctx):
    chars = (ctx.artifact.get("asset_manifest") or {}).get("characters")
    if not (isinstance(chars, list) and chars):
        return []
    return m.wrap(chk, checks.each_has(ctx.artifact, "asset_manifest.characters", fields=["id"]))


def _ctx_character_ids(module, rd: Dict) -> str:
    """Repair context for a manifest sprite whose `id` must MATCH a cast id: the target + the EXACT
    cast roster to pick from + the manifest self-view + run-state. Like cr.ctx_structural but it also
    hands over the one catalogue the id must resolve into (the cast), the way crossref_character does."""
    from maestro.modules import cast
    art = rd.get("artifact") or {}
    lines = cr.target_block(rd) + cast.character_index(art) + module.self_digest(art) + cr.tail_block(rd)
    lines += ["", "Set the missing id to the cast id above that this sprite depicts."]
    return "\n".join(lines)


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
            ctx.artifact, "asset_manifest.backgrounds", fields=["id"])), context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="assets_background_field_patch.txt", skeleton=""),
        Check("character_ids", _d_character_ids, context=_ctx_character_ids,
              tools=_REPAIR_TOOLS, prompt="assets_character_ids_fix.txt", skeleton=""),
    ]

    def affected_components(self) -> Tuple[str, ...]:
        return ("asset_manifest",)

    def render_context(self, ctx: Dict) -> str:
        # The manifest derives from who and where: the premise (backgrounds come from where the
        # story happens), the character ids (sprite entries must match them), the item ids (icons
        # match place items). Ids, not cards — the manifest names assets, it doesn't write people.
        from maestro.modules import cast, inventory, story
        art = ctx.get("artifact") or {}
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        lines += cast.character_index(art)
        lines += story.story_block(art)
        lines += inventory.item_index(art)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> list:
        am = artifact.get("asset_manifest") or {}
        chars = [c.get("id") for c in am.get("characters", []) if isinstance(c, dict)]
        out = location_index(artifact)
        if chars:
            out += ["", f"MANIFEST CHARACTER IDS: {chars}"]
        return out


MODULE = Assets()
register_module(MODULE)
