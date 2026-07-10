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
from maestro.modules.module import Check, Error, Module, register_module


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
_DESCRIBE_TOOLS = frozenset({"describe_asset", "read_component", "request_review"})


def _background_usage_block(artifact: Dict, bg_id: str) -> list:
    """Where the target background is already USED — a node/place/encounter that points at it. Empty
    when nothing does yet (the normal case: backgrounds are named before scenes exist), in which case
    the premise is the only grounding available."""
    out = []
    for nid, n in ((artifact.get("nodes") or {}).get("nodes") or {}).items():
        if isinstance(n, dict) and n.get("location") == bg_id:
            out.append(f"  scene {nid} is set here")
    for pid, p in ((artifact.get("places") or {}).get("places") or {}).items():
        if isinstance(p, dict) and p.get("background") == bg_id:
            out.append(f"  place {pid} is set here")
    for enc in (artifact.get("combat") or {}).get("encounters") or []:
        if isinstance(enc, dict) and enc.get("background") == bg_id:
            out.append(f"  encounter {enc.get('id')} is fought here")
    if not out:
        return []
    return ["", f"'{bg_id}' IS ALREADY USED BY:", *out]


def _d_missing_descriptions(chk, m, ctx):
    """Demand-driven fan: one error per stub entry with no prose description yet — every kind lands
    undescribed by construction (a character sprite, an overworld token, a map feature, an item
    icon, a background, a cg) and gets one authored VISUAL description each. Each fix is ONE
    `describe_asset` call, so a batch of N stubs costs N small calls instead of one call that must
    fit all of them."""
    from maestro.asset_stubs import missing_asset_descriptions
    return [Error(type=chk.tier, code=chk.code, component="asset_manifest",
                  path=e["id"], ref=e["id"], kind=e["kind"],
                  message=f"{e['kind']} entry '{e['id']}' has no description yet — describe it with "
                          f"describe_asset('{e['id']}', ...).")
            for e in missing_asset_descriptions(ctx.artifact)]


def _d_missing_style(chk, m, ctx):
    """The one shared art-direction brief every asset prompt is composed with. Authored (not
    template-filled from concept/theme — that produced unpaintable meta-prose like 'Art direction
    for A turn-based RPG where...') via the same describe_asset path as every stub."""
    manifest = ctx.artifact.get("asset_manifest") or {}
    if ((manifest.get("style") or {}).get("description") or "").strip():
        return []
    return [Error(type=chk.tier, code=chk.code, component="asset_manifest",
                  path="style", ref="style", kind="style",
                  message="no shared art direction yet — write it with "
                          "describe_asset('style', ...).")]


def _ctx_style(module, rd: Dict) -> str:
    """Grounding for the art-direction call: the premise plus the story spine's tone/theme — the
    identity the brief must TRANSLATE into visual language, never restate."""
    art = rd.get("artifact") or {}
    spec = rd.get("spec") or {}
    spine = (art.get("story") or {}).get("spine") or {}
    lines = cr.premise_block(rd) + [""] + cr.target_block(rd)
    if spine.get("tone") or spine.get("theme"):
        lines += ["", f"STORY TONE: {spine.get('tone', '')}",
                  f"STORY THEME: {spine.get('theme', '')}"]
    lines += ["", f"PRESENTATION: {spec.get('presentation', '2d')}"]
    lines += cr.tail_block(rd)
    lines += ["", "Call describe_asset(\"style\", ...) now with the visual art direction."]
    return "\n".join(lines)


def _ctx_describe_asset(module, rd: Dict) -> str:
    """The ONE entity this call describes: the premise + whatever grounds it concretely — the stub's
    own hint fields (label/theme/role), the matching cast card for a sprite, the matching item entry
    for an icon, or (backgrounds, the common case) any scene/place already set there. Never the whole
    manifest — a description is authored from what this ONE thing IS, not from the rest of the game."""
    from maestro.modules import cast, inventory
    art = rd.get("artifact") or {}
    target = rd.get("target")
    kind = getattr(target, "kind", None) if target is not None else None
    asset_id = getattr(target, "ref", None) if target is not None else None
    manifest = art.get("asset_manifest") or {}
    if kind == "title_card":
        entry = manifest.get("title_card")
    else:
        entry = next((e for e in manifest.get(kind) or []
                      if isinstance(e, dict) and e.get("id") == asset_id), None)
    lines = cr.premise_block(rd) + [""] + cr.target_block(rd)
    entry = entry or {}
    hint = {k: entry[k] for k in ("label", "theme", "role") if entry.get(k)}
    if hint:
        lines += ["", f"STUB DATA for {asset_id!r}: {hint}"]
    if kind in ("characters", "tokens"):
        cid = entry.get("char_id", asset_id)
        cards = cast.character_cards(art, only={cid})
        if cards:
            lines += cards
    elif kind in ("items", "markers"):
        lines += inventory.item_index(art)
    elif kind == "backgrounds":
        lines += _background_usage_block(art, asset_id)
    lines += cr.tail_block(rd)
    lines += ["", f"Call describe_asset({asset_id!r}, ...) now with a concrete description."]
    return "\n".join(lines)


def _d_assets_complete(chk, m, ctx):
    """Completeness by construction: every visual entity the content references must have an asset
    stub. `_reconcile_stubs` (called by the authoring tools) keeps this satisfied; this is the
    done-condition that PROVES it, so a missing asset is a hard error, never a silent placeholder.
    Its fix re-runs the deterministic reconcile (a code step, no LLM)."""
    from maestro.asset_stubs import missing_stub_entities
    missing = missing_stub_entities(ctx.artifact)
    if not missing:
        return []
    return [Error(type=chk.tier, code=chk.code, component="asset_manifest",
                  message=f"missing asset stubs for {missing} — every visual entity needs a "
                          f"manifest entry (regenerate the stubs)")]


def _run_reconcile(module, ctx, error, slot, services, dispatch):
    from maestro.asset_stubs import reconcile_stubs
    services.state.write_component(
        "asset_manifest", reconcile_stubs(services.state.load_artifact()))


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
    '// The FINAL shape once everything is authored (reference only — for a human editing the raw\n'
    '//   component). No single call fills this: backgrounds/cgs ids are authored via\n'
    '//   assets_backgrounds_shape.txt, characters/items/tokens/features/tiles/markers/title_card are\n'
    '//   derived automatically from the cast/items/places/spec, and every entry\'s `description` is\n'
    '//   filled one at a time by describe_asset.'
)

# The background/cg PLAN — ids only, no prose. Descriptions are a separate, per-entry call
# (describe_asset) so a big cast/location count can never blow one call's output budget.
SKEL_ASSET_SHAPE = (
    '{\n'
    '  "backgrounds": [ {"id": "bg_<place>"} ],\n'
    '  "characters": [],\n'
    '  "cgs": [ {"id": "cg_<beat>"} ]\n'
    '}\n'
    '// ids ONLY here — no description, no image_file. "characters" MUST stay [] (sprite entries are\n'
    '//   derived automatically from the cast, never invented here). "cgs" is OPTIONAL — omit or [].\n'
    '//   Each background/cg gets its prose description in a LATER, separate call.'
)

SKEL_DESCRIBE_ONE = (
    '// asset_id (the tool arg) is the EXACT id named in YOUR TARGET above — never invent a new one.\n'
    'describe_asset(asset_id="<that id>", description="<2-4 concrete sentences>")'
)

SKEL_DESCRIBE_STYLE = (
    'describe_asset(asset_id="style", description="<1-3 sentences of concrete visual art direction:'
    ' palette, lighting, materials, rendering style>")'
)


class Assets(Module):
    id = "assets"
    selectable = False   # always-on: every engine keys art off the manifest's ids
    priority = 20
    component = "asset_manifest"
    mode_prompt = "assets_backgrounds_shape.txt"
    mode_tools = frozenset({"write_component", "request_review"})
    skeleton = SKEL_ASSET_MANIFEST
    schemas = {"asset_manifest": v_asset_manifest}
    skeletons = {"asset_manifest": SKEL_ASSET_MANIFEST}

    checks = [
        # Backgrounds must exist before any id-check is meaningful — blocking. The fix authors ONLY
        # the id shape (SKEL_ASSET_SHAPE) — never the whole-manifest prose write that used to blow
        # the output token cap on a cast+location-heavy game (one giant JSON, unparseable mid-write).
        Check("backgrounds_exist", lambda chk, m, ctx: m.wrap(chk, checks.exists(
            ctx.artifact, "asset_manifest.backgrounds")), blocking=True,
            prompt="assets_backgrounds_shape.txt", skeleton=SKEL_ASSET_SHAPE),
        Check("background_ids", lambda chk, m, ctx: m.wrap(chk, checks.each_has(
            ctx.artifact, "asset_manifest.backgrounds", fields=["id"])), context=cr.ctx_structural,
            tools=_REPAIR_TOOLS, prompt="assets_background_field_patch.txt", skeleton=""),
        # The shared art-direction brief — authored once, before the per-stub descriptions it will
        # be composed with at the styled prompt stage.
        Check("missing_style", _d_missing_style, context=_ctx_style,
              tools=_DESCRIBE_TOOLS, when_clean=True,
              prompt="assets_style.txt", skeleton=SKEL_DESCRIBE_STYLE),
        # Demand-driven fan, one describe_asset call per undescribed stub (mirrors inventory's
        # demanded_items): decomposes the prose-authoring load N ways instead of one call for all N.
        Check("missing_descriptions", _d_missing_descriptions, context=_ctx_describe_asset,
              tools=_DESCRIBE_TOOLS, when_clean=True,
              prompt="assets_describe.txt", skeleton=SKEL_DESCRIBE_ONE),
        # Terminal completeness sweep — every referenced visual entity has a stub. Runs only once
        # the cheaper checks pass; its fix is the deterministic stub reconcile, not an LLM step.
        Check("assets_complete", _d_assets_complete, when_clean=True, run=_run_reconcile),
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
