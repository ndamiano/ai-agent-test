"""Asset-manifest completeness by construction.

Every visual thing a game references — a character sprite, an overworld token, a scene
background, a map feature, a terrain tile, an inventory icon, a hotspot marker, the title card —
gets a STUB in `asset_manifest` the moment the content that needs it is authored. `reconcile_stubs`
is the single deterministic derivation: given the on-disk artifact it computes the COMPLETE set of
stubs the current content demands and merges it into the manifest, preserving anything the styled
prompt stage (`asset_prompts.py`) or an author already wrote (the `prompt`/`description`/`image_file`
fields survive a re-run). The authoring tools call it after every visual write, so an asset-less
entity is impossible by construction — never a silent placeholder discovered at generation time.

Derivation lives HERE, in one place, not scattered inside `renpy.fns.generate_images`; generation
just walks the stubs and renders each from its saved prompt. Pure + engine-neutral: dict in, dict
out. The filename slugs (`feature_<slug>`, `tile_<slug>`, `prop_<slug>`) route through
`renpy.fns.tile_slug` so a stub's file matches byte-for-byte what the Godot overworld probes for.
"""

from typing import Dict, List, Optional


_RPG_KINDS = {"world_map", "town", "interior"}


def walkable(artifact: Dict) -> bool:
    """True when the game has a walkable RPG zone (tokens/tiles/features/markers are its assets)."""
    places = (artifact.get("places") or {}).get("places") or {}
    return any(isinstance(p, dict) and p.get("kind") in _RPG_KINDS for p in places.values())


def _by_id(rows) -> Dict[str, Dict]:
    return {r["id"]: r for r in (rows or []) if isinstance(r, dict) and r.get("id")}


def _keep(prev: Optional[Dict], fresh: Dict) -> Dict:
    """Merge a freshly-derived stub over its prior version so an authored/styled field survives a
    reconcile: a prior `prompt`/`image_file` and a non-empty prior `description` win; the derived
    subject fields refresh everything else."""
    if not prev:
        return fresh
    out = {**fresh}
    for k in ("prompt", "image_file"):
        if prev.get(k):
            out[k] = prev[k]
    if prev.get("description"):
        out["description"] = prev["description"]
    return out


def _cast(artifact: Dict) -> List[Dict]:
    return [c for c in ((artifact.get("characters") or {}).get("characters") or [])
            if isinstance(c, dict) and c.get("id")]


def _additive(rows: List[Dict], prev, id_key="id") -> List[Dict]:
    """Merge derived `rows` over existing stubs and preserve any prior stub they don't cover. Used
    for the append-only kinds (cast + inventory only grow — there is no delete tool), so a stub is
    never destroyed by a reconcile; features/tiles/markers, which genuinely change, are recomputed."""
    have = _by_id(prev)
    out, seen = [], set()
    for r in rows:
        out.append(_keep(have.get(r[id_key]), r))
        seen.add(r[id_key])
    for e in (prev or []):
        if isinstance(e, dict) and e.get(id_key) not in seen:
            out.append(e)
    return out


def _character_stubs(cast: List[Dict], prev) -> List[Dict]:
    """One sprite stub per cast member — the manifest character list mirrors the cast (replaces the
    old gen-time _merge_cast_into_manifest backfill), additive so a hand-made stub survives."""
    return _additive([{
        "id": c["id"], "image_file": f"{c['id']}.png",
        "description": c.get("description") or c.get("voice") or c.get("name") or c["id"]}
        for c in cast], prev)


def _token_stubs(char_stubs: List[Dict], prev) -> List[Dict]:
    """One overworld token per CHARACTER STUB (walkable games only) — the chibi avatar/NPC marker
    the Godot overworld draws at ~1 tile, probed as <cid>_token.png."""
    have = _by_id(prev)
    return [_keep(have.get(f"{c['id']}_token"), {
        "id": f"{c['id']}_token", "image_file": f"{c['id']}_token.png", "char_id": c["id"],
        "description": c.get("description") or c["id"]})
        for c in char_stubs if isinstance(c, dict) and c.get("id")]


def _item_stubs(items: List[Dict], prev) -> List[Dict]:
    """One icon stub per inventory item — the manifest item list mirrors the catalogue, additive."""
    return _additive([{
        "id": it["id"], "image_file": f"{it['id']}.png",
        "description": it.get("examine") or it.get("name") or it["id"]}
        for it in items if isinstance(it, dict) and it.get("id")], prev)


def _referenced_locations(artifact: Dict) -> List[str]:
    """Background ids the content points at — every node `location` + every room `background`, in
    first-seen order. Each MUST have a background stub or a scene renders on nothing."""
    seen: Dict[str, None] = {}
    for n in ((artifact.get("nodes") or {}).get("nodes") or {}).values():
        loc = n.get("location") if isinstance(n, dict) else None
        if isinstance(loc, str) and loc:
            seen.setdefault(loc, None)
    for p in ((artifact.get("places") or {}).get("places") or {}).values():
        bg = p.get("background") if isinstance(p, dict) else None
        if isinstance(bg, str) and bg:
            seen.setdefault(bg, None)
    for enc in (artifact.get("combat") or {}).get("encounters") or []:
        bg = enc.get("background") if isinstance(enc, dict) else None
        if isinstance(bg, str) and bg:
            seen.setdefault(bg, None)
    return list(seen)


def _bg_file(bg_id: str) -> str:
    """Background filename — mirrors the compiler default (`bg_x` -> `x.png`) so lint resolves it."""
    return (bg_id[3:] + ".png") if bg_id.startswith("bg_") else f"{bg_id}.png"


def _background_stubs(artifact: Dict, prev) -> List[Dict]:
    """Ensure a background stub for every referenced location (additive: the assets module still owns
    the descriptive prose, so an authored description is preserved and a bg authored ahead of its
    first reference is kept — reconcile only guarantees coverage, never prunes)."""
    have = _by_id(prev)
    out = list(prev or [])
    for loc in _referenced_locations(artifact):
        if loc not in have:
            stub = {"id": loc, "image_file": _bg_file(loc), "description": ""}
            out.append(stub)
            have[loc] = stub
    return out


def _feature_stubs(places_comp: Dict, prev) -> List[Dict]:
    from renpy.fns import _collect_feature_specs, tile_slug
    have = _by_id(prev)
    return [_keep(have.get(f"feature_{tile_slug(label)}"), {
        "id": f"feature_{tile_slug(label)}", "image_file": f"feature_{tile_slug(label)}.png",
        "kind": kind, "label": label, "description": label})
        for kind, label in _collect_feature_specs(places_comp)]


def _tile_stubs(places_comp: Dict, prev) -> List[Dict]:
    from renpy.fns import _collect_tile_specs, tile_slug
    have = _by_id(prev)
    return [_keep(have.get(f"tile_{tile_slug(theme)}"), {
        "id": f"tile_{tile_slug(theme)}", "image_file": f"tile_{tile_slug(theme)}.png",
        "theme": theme, "role": role, "description": f"{role} terrain: {theme}"})
        for theme, role in _collect_tile_specs(places_comp)]


def _marker_stubs(places_comp: Dict, prev) -> List[Dict]:
    """Overworld hotspot markers: one signpost for move exits, one banner for a win tile, one
    battle standard for combat tiles, one small prop per distinct examine/use hotspot (its label
    is the subject — a use hotspot is a physical mechanism standing in the world). All ride the
    item-icon pipeline. NO cap: every hotspot the game authored gets its marker — a capped list
    silently rendered the overflow as bare colour chips."""
    from renpy.fns import tile_slug
    have = _by_id(prev)
    verbs, prop_labels = set(), []
    for p in (places_comp.get("places") or {}).values():
        if not isinstance(p, dict) or p.get("kind") not in _RPG_KINDS:
            continue
        for hot in p.get("interactables") or []:
            a = (hot or {}).get("action") or {}
            verbs.add(a.get("type"))
            if a.get("type") in ("examine", "use") and hot.get("label"):
                prop_labels.append(hot["label"])
    out = []
    if "move" in verbs:
        out.append(_keep(have.get("marker_signpost"), {
            "id": "marker_signpost", "image_file": "marker_signpost.png",
            "description": "weathered wooden trail signpost with a blank arrow board"}))
    if "win" in verbs:
        out.append(_keep(have.get("marker_banner"), {
            "id": "marker_banner", "image_file": "marker_banner.png",
            "description": "small victory banner on a standing pole"}))
    if "start_combat" in verbs:
        out.append(_keep(have.get("marker_combat"), {
            "id": "marker_combat", "image_file": "marker_combat.png",
            "description": "crossed swords over a small battle standard"}))
    for label in dict.fromkeys(prop_labels):
        if not tile_slug(label):
            continue
        mid = f"prop_{tile_slug(label)}"
        out.append(_keep(have.get(mid), {
            "id": mid, "image_file": f"{mid}.png", "description": label}))
    return out


def _title_card_stub(spec: Dict, prev):
    """The title screen — kept if one was already stubbed, else created once the game has an
    identity (a spec title/concept). Returns None to omit it (a bare artifact with no title)."""
    if isinstance(prev, dict) and prev.get("image_file"):
        return _keep(prev, {"image_file": "title_card.png",
                            "description": prev.get("description") or "the game's title screen"})
    title = (spec.get("title") or spec.get("concept") or "").strip()
    if not title:
        return None
    return {"image_file": "title_card.png", "description": title}


# The visual-entity stub lists a completeness sweep walks; `title_card` is the one singleton.
STUB_LISTS = ("backgrounds", "characters", "tokens", "items", "cgs",
              "features", "tiles", "markers")


def reconcile_stubs(artifact: Dict, spec: Optional[Dict] = None) -> Dict:
    """Return `asset_manifest` completed for the current content: a stub for every visual entity,
    merged over the existing manifest (authored/styled fields preserved). The single derivation
    point — tools call it after a visual write; generation consumes the result. `spec` supplies the
    title-card subject (title/concept); absent, it falls back to the manifest's own."""
    spec = spec or {}
    manifest = dict(artifact.get("asset_manifest") or {})
    cast = _cast(artifact)
    items = (artifact.get("items") or {}).get("items") or []
    places_comp = artifact.get("places") or {}

    manifest["characters"] = _character_stubs(cast, manifest.get("characters"))
    manifest["items"] = _item_stubs(items, manifest.get("items"))
    manifest["backgrounds"] = _background_stubs(artifact, manifest.get("backgrounds"))
    manifest["features"] = _feature_stubs(places_comp, manifest.get("features"))
    manifest["tiles"] = _tile_stubs(places_comp, manifest.get("tiles"))
    manifest["markers"] = _marker_stubs(places_comp, manifest.get("markers"))
    tc = _title_card_stub(spec, manifest.get("title_card"))
    if tc:
        manifest["title_card"] = tc
    else:
        manifest.pop("title_card", None)
    manifest["cgs"] = list(manifest.get("cgs") or [])
    if walkable(artifact):
        manifest["tokens"] = _token_stubs(manifest["characters"], manifest.get("tokens"))
    else:
        manifest.pop("tokens", None)
    return manifest


def declared_files(manifest: Dict) -> List[str]:
    """Every image file the manifest declares (across all stub kinds + title_card) — the set a
    post-generation completeness verification expects on disk."""
    out: List[str] = []
    for key in STUB_LISTS:
        for e in manifest.get(key) or []:
            if isinstance(e, dict) and e.get("image_file"):
                out.append(e["image_file"])
    tc = manifest.get("title_card") or {}
    if isinstance(tc, dict) and tc.get("image_file"):
        out.append(tc["image_file"])
    return out


def missing_stub_entities(artifact: Dict, spec: Optional[Dict] = None) -> List[str]:
    """Ids the current content demands that the manifest has no stub for — an entity that would
    render as nothing. Empty once `reconcile_stubs` has run, so this is the completeness assertion
    the assets module enforces as a done-condition (never satisfied by a silent placeholder)."""
    manifest = artifact.get("asset_manifest") or {}
    complete = reconcile_stubs(artifact, spec)
    missing: List[str] = []
    for key in STUB_LISTS:
        have = {e.get("id") for e in (manifest.get(key) or []) if isinstance(e, dict)}
        missing += [e["id"] for e in (complete.get(key) or []) if e.get("id") not in have]
    if complete.get("title_card") and not (manifest.get("title_card") or {}).get("image_file"):
        missing.append("title_card")
    return missing


def missing_asset_descriptions(artifact: Dict) -> List[Dict]:
    """Stub entries that EXIST (an id + image_file already stamped by `reconcile_stubs`) but carry no
    prose `description` yet — the demand-driven authoring queue `describe_asset` drains one at a
    time. Reads the manifest AS WRITTEN, never a fresh reconcile: a kind whose stub derives its
    description from an upstream component (characters/items/tokens/features/tiles/markers) already
    carries one by construction, so in practice this only ever queues backgrounds (and an optional
    cg) — the one prose surface nothing upstream can derive for it."""
    manifest = artifact.get("asset_manifest") or {}
    out: List[Dict] = []
    for key in STUB_LISTS:
        for e in manifest.get(key) or []:
            if isinstance(e, dict) and e.get("id") and not (e.get("description") or "").strip():
                out.append({"kind": key, "id": e["id"]})
    tc = manifest.get("title_card")
    if isinstance(tc, dict) and tc.get("image_file") and not (tc.get("description") or "").strip():
        out.append({"kind": "title_card", "id": "title_card"})
    return out
