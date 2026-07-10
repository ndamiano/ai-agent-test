# Asset Quality — Better Prompting & Better Models

## Why
Asset quality is a ship-blocker (asset-monotony post-mortem; map-gen output is "vibe-coded garbage").
Two levers to pull, per asset type: **prompt better** and **use the right/better model for the
need**. This file is the **generation-side** home for art quality. The **validation-side** checks
(bg-description lint, character-consistency, CG quality gate) live in `quality_backlog.md` §7 — this
file makes the art good; that file catches when it isn't.

## Guardrails
- **Per-asset-type, not one model.** The stack already runs different models per need (Illustrious
  SDXL for sprites, ideogram4 for tiles, Hunyuan3D/TRELLIS for meshes) — that diversity is correct.
  Pick the best tool per asset type; don't collapse to one.
- **Local-first** (vision) — evaluate local models before an API; note the tradeoff when an API wins.
- **Prompts as data + climbable** — image prompts live as `.txt` and/or in the manifest, not inlined
  in Python; iterate them with the same discipline as generation prompts (`quality_backlog.md` §2).
- **Coordinate, don't duplicate** — style coherence goes through `game_style.md`; content-coupling +
  styled-prompt-stage through the asset-pipeline redesign; the *checks* through `quality_backlog.md` §7.

## Background (VERIFIED — `CLAUDE.md` + recon)
- **Characters:** neutral sprite + img2img emotion variants; `waiIllustriousSDXL_v170` checkpoint
  (`tests/test_comfyui_prompt.py:42`), ComfyUI (`src/tools/comfyui_tools.py`, `src/renpy/fns.py`).
- **Backgrounds:** Illustrious — **known caveat: renders a creature named in a bg description as the
  subject** (needs environmental-only prompts). Prompts are the SD prose in `asset_manifest`.
- **Tiles:** ideogram4 stack (`build_tile_job`, structured JSON captions, role-specific body +
  hex palette) + `make_seamless_tile`; fallback = role-aware DreamShaper formulas.
- **Meshes (hd2d):** Hunyuan3D-2.1 (~9s, untextured→triplanar projection) or TRELLIS.2-4B (~50-90s,
  native PBR) via `settings.comfyui.mesh_backend`. Known gaps: TRELLIS plinth slab + occasional black
  bakes (tracked in `docs/ROADMAP.md` "Godot/3D polish").
- **Feature objects:** item workflow + BiRefNet matting → matted sprite.
- **Prompts** are the styled prose SAVED on each stub by the styled prompt stage
  (`maestro/asset_prompts.py`); the `build_*_job` builders wrap them with model-specific
  scaffolding (quality tags, framing, negatives).

## T1 — Prompt quality (the cheap lever first)
- [x] **Completeness by construction** — every authoring tool that adds a visual thing emits an
      asset STUB into the manifest at creation time (`maestro/asset_stubs.py::reconcile_stubs`,
      called from the tool bodies), so an asset-less entity is impossible by construction, not a
      silent placeholder discovered at gen time. The `assets` module carries the done-condition
      (`assets_complete` — missing stub = Error, fix = reconcile); the compile-time placeholder
      backfills stay as the net. `generate_images` is now a pure consumer of the manifest (the old
      at-gen derivation of tokens/features/tiles/markers is deleted).
- [x] **Styled prompt stage** — `maestro/asset_prompts.py::apply_styled_prompts` runs once when
      content is done (before generation, sequenced in `run.run_build`): it reads the game's
      identity ONCE (spec concept + story spine tone/theme) into one deterministic style brief, then
      composes a style-consistent image prompt per stub and SAVES it on the manifest entry —
      inspectable in the browser, editable, climbable (`maestro/prompts/asset_*.txt`), re-runnable
      (`generate_images` + the HITL per-asset regen consume ONLY the saved prompt).
- [ ] **Per-asset-type prompt improvement** — backgrounds (`asset_background.txt` now forces
      environmental-only, the source fix for the named-creature bug; §7 lint is the guard), character
      sprites (identity/consistency descriptors), CGs, tiles (already structured — refine). The
      templates exist; climbing their wording is the remaining work.
- [ ] **Climb the image prompts** — treat each as a hill-climbable `.txt`, run the genre battery,
      judge before/after (`quality_backlog.md` §2 discipline applied to art).

## T2 — Model selection per need (the bigger lever)
- [ ] **Audit each asset type's current model** vs the state of the art for that specific need
      (character sprites, backgrounds, CGs, UI, tiles, meshes, feature objects).
- [ ] **Bake-off the candidates** — reuse the proven approach from the 3D mesh bake-off (benchmark N
      models on a fixed set of real game objects, judge, wire the winner as a selectable backend).
      Local-first, note API tradeoffs.
- [ ] **Make winners selectable backends** (like `mesh_backend`) so a better model drops in without a
      pipeline rewrite.

## T3 — Known bad spots (specific, high-value)
- [x] **Map generation redesign** — DONE (layout/rasterization): structure-first generators keyed
      on place kind (town road+parcel, interior room-graph, world_map terrain-fill) + the two-tier
      furniture-list model (a feature houses an interaction OR the LLM-derived per-place furniture
      list fills to density — never free-form), footprints sized by declared size and
      overlap-forbidden by construction, `map_builder.render_ascii` debug view. Global geography /
      edge-matched cross-zone alignment stays parked; WFC rejected as backbone.
- [ ] **Asset monotony → locations-with-states** — the same-place-every-scene problem is addressed by
      IR 0.2 first-class `locations` with img2img'd state variants (tracked in `docs/ROADMAP.md` Build
      quality; cross-ref, don't duplicate the work here).
- [ ] **Mesh polish** — TRELLIS plinth slab + black bakes (`docs/ROADMAP.md` "Godot/3D polish").

## T4 — Consistency
- [ ] **Character identity across scenes** — sprite drift; anchor generation so a character reads as
      the same person scene to scene (paired with the §7 consistency check).
- [ ] **Style coherence** — all of a game's assets share a look; drive off `game_style.md` tokens.

## Ordering
T1 (prompting) first — cheapest, and a styled-prompt stage benefits every asset type at once. T2
(model bake-offs) per asset type as prioritized. T3 map-gen redesign is high-value and can run in
parallel. Quality-gate everything with the genre battery + the §7 checks.

## Parked
- Which asset types most need a model upgrade — decide from the T2 audit.
- Local vs API per asset type (same tension as `game_media.md`).
