# Asset Quality — better prompting + the right model per asset type

## Why
Art quality is a ship-lever with two knobs, per asset type: **prompt better** and **use the
right model for the need**. Generation now lives in the codegen reskin pipeline; today ONE
square item workflow renders every 2D sprite AND every 3D mesh source image — fine for props,
wrong for characters, tiles and buildings. This file is the generation-side home; style
coherence is `game_style.md` (the styled-prompt stage composes there, model scaffolding lands
here).

## Background (VERIFIED 2026-07-23)
- Pipeline: `src/maestro/codegen/reskin.py` (plan from data rows' `look` fields via
  `data_files.sprite_plan_from_data`, LLM fallback `plan_assets`/`plan_meshes`;
  `start_asset_chain` enqueues the whole batch) → `src/maestro/codegen/asset_chain.py`
  (save_sprite / mesh_from_image / decimate / `skin` finalize) →
  `src/tools/comfyui_tools.py` (`build_item_job`/`build_item_payload`) → the `image` queue.
  Per-asset re-render: `POST /{run_id}/assets/{asset_id}/regenerate`
  (`src/api/routers/games.py` → `reskin.regenerate_asset`).
- Models in play: `src/config/workflows/txt2img_item.json` = **flux1-schnell-fp8** + BiRefNet
  matting (EVERY asset goes through this, 1024² square); `src/config/workflows/txt2img.json`
  = **animaOfficial_preview3Base** (UNET/T5, currently unused by reskin). Meshes:
  **TRELLIS.2-4B** (`src/tools/trellis_server.py`, `mesh` queue) → 50k-tri GLB → bundled at
  ~20k tris (`runtime/decimate.mjs`).
- Prompt text: a data-driven plan uses the row's `look` string VERBATIM as the positive;
  prompts land in `assets.json` and regenerate consumes only the saved prompt.
  `screen_image_prompt` (`src/tools/safety.py`) can block a prompt → job not sent.
- **Hard-won, still true:** T5/flux-class encoders read PROSE — danbooru quality tags and
  tag-style negatives are Illustrious-era culture and off-distribution (they caused subject
  drift; the verbatim-prompt rule in `build_item_job`'s docstring is the fix). BiRefNet
  matting exists because props shipped with baked backgrounds. TRELLIS decimation to 50k (not
  500k) cut postprocess 4-13s → ~2s and uploads ~8x with equal-or-better meshes — don't
  regress it. Illustrious-era caveat to RE-VERIFY on flux/anima: a creature named in an
  environment description gets rendered as the subject.

## Guardrails
- **Per-asset-type, not one model** — pick the best tool per need; a single checkpoint for
  everything is the current bug, not a simplification to preserve.
- **Local-first**; note the tradeoff explicitly if an API ever wins a bake-off.
- **Prompts as saved data** — model-agnostic art direction lives in `look`/the style brief;
  model-SPECIFIC scaffolding (framing, negatives, resolution) lives in the `build_*_job`
  builders and workflow json, nowhere else.
- **Coordinate, don't duplicate:** style brief = `game_style.md` S1; audio = `game_media.md`.

## Tasks

### A1 — Prompt quality (cheap lever first)
- [ ] Climb the `look`-authoring guidance in `prompts/design_data.txt` + the fallback
      planners (`prompts/plan_assets.txt`, `plan_meshes.txt`): concrete subject, material,
      view/framing per asset kind. Verify: before/after renders on one game, judged
      side-by-side (quality_backlog Q2 discipline).
- [ ] Prompt-lint at plan time: warn when a `look` names a creature inside an environment
      prompt or a scene around an object (the subject-drift class). Files: `reskin.py`.
      Test: unit on the lint with fixture plans.

### A2 — Per-asset-type jobs (the current single-workflow bug)
- [ ] Split `build_item_job` into typed builders: character sprite (full-body, transparent),
      prop/item (current square), tile/terrain (seamless), mesh-source image (single object,
      neutral ground — TRELLIS input quality bounds mesh quality). Route by the plan entry's
      kind (2D rows vs mesh plan already distinguishes). Files: `src/tools/comfyui_tools.py`,
      `src/config/workflows/*.json`, `reskin.py` plan entries. Test: each builder unit-tested
      on workflow shape (pattern: existing comfyui tests).

### A3 — Model bake-offs per type (the bigger lever)
- [ ] Audit + bake-off per asset type on a fixed set of real game objects (the proven 3D
      bake-off method): flux-schnell vs anima vs a current SOTA per need; judge, record the
      call HERE, wire the winner as that type's workflow json (the workflow file IS the
      selectable-backend seam). Local-first.

### A4 — Mesh polish
- [ ] Re-verify the TRELLIS plinth-slab / black-bake artifacts on the current 4B server +
      50k pipeline; if still present, attack via mesh-source image prompts (A2's builder)
      before touching the server. Files: `src/tools/trellis_server.py` only if image-side
      fails. Verify: building/char/foliage set renders clean in `run3d`.

### A5 — Consistency
- [ ] Character identity: a game's character re-renders (regenerate endpoint) should keep
      reading as the same entity — carry the full original `look` + style brief into
      regenerate prompts rather than the user's bare replacement text. Files: `reskin.py`
      `regenerate_asset`. Test: unit — regenerate prompt contains the brief.

## Parked
- Which asset types most need a model upgrade — decide from the A3 audit.
- Sprite animation frames — `game_media.md` M4 owns it.
- Terrain texture quality (worldgen's `assets/terrain.png`) — revisit with A3's tile slot.
- Local vs API per asset type — same tension as game_media; default local.
