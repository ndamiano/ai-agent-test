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
- **Prompts** are generated prose in the manifest; no styled/curated prompt stage yet.

## T1 — Prompt quality (the cheap lever first)
- [ ] **Styled prompt stage** — a stage that turns raw content into a good, style-consistent image
      prompt and **saves it in the manifest** (per the asset-pipeline redesign) — so prompts are
      inspectable, editable, climbable, and coherent across a game's assets.
- [ ] **Per-asset-type prompt improvement** — backgrounds (environmental-only, kill the named-creature
      bug — the lint in `quality_backlog.md` §7 is the guard, this is the source fix), character
      sprites (identity/consistency descriptors), CGs, tiles (already structured — refine).
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
- [ ] **Map generation redesign** — current map/tile output is garbage. Redesign around
      zone-requirements + a biome palette (mechanical OR palette, never free-form) + global geography;
      WFC scoped to fill, not backbone. (See the map-gen redesign direction — this is its home.)
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
