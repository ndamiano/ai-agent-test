# Game Media — audio (music + SFX) and animation

## Why
A game needs a soundscape and motion to feel alive. Today `kit.audio.play` is an explicit
no-op stub (`runtime/engine.js:872` — "stub; real backend wired later"); there is no music,
no SFX, and animation is limited to engine3d's procedural walk-bob. Three legs, one spine:
kit surface first (fail-soft), procedural/local generation second, model-generated content
last — the prior stack proved the wiring end-to-end with a zero-dependency procedural stub
before any model existed, and that ordering is the keeper lesson.

## Background (VERIFIED 2026-07-23)
- Kit/runtime: `runtime/engine.js` `run()` (browser 2D; preloads `assets.json` sprites),
  `simulate()` (headless — audio must stay no-op there), `kit.register` bindings (the natural
  SFX trigger points), `kit.audio.play` stub. 3D: `runtime/engine3d.js` `run3d` (preloads
  `assets.json` meshes; procedural walk-bob already animates movers). Probe forbids
  `Math.random`/`Date.now` — audio calls must never affect sim state.
- Generation transport: the worker-pull queue is the ONLY path to a GPU. Handlers keyed by
  payload `kind` in `src/worker/handlers.py` (`HANDLERS = {http, comfy_image, trellis_mesh}`);
  per-queue second estimates in `src/db/estimates.py` (llm 30 / image 45 / mesh 240); asset
  jobs chain via `src/maestro/codegen/asset_chain.py` (`CONTINUATIONS`/`OPERATIONS`/
  `FINALIZERS` — mesh_from_image, save_sprite, decimate, skin) with blobs landing in
  `<data_dir>/blobs/`; batches enqueue all-at-once via `reskin.start_asset_chain`.
- Assets are additive by law: no asset ⇒ gates pass, shapes render. Audio inherits this.
- ComfyUI is the image backend (`src/tools/comfyui_tools.py`, workflows in
  `src/config/workflows/`); ComfyUI also runs audio models (stable-audio/ACE-Step) — a music
  job can be a new workflow on the existing `image` queue's target before earning its own
  queue.

## Guardrails
- **Fail-soft everywhere:** missing/failed audio degrades to silence, missing animation to
  static. No gate ever blocks on media. (The old stack's silent-placeholder backfill lesson.)
- **Sim/render law:** audio and animation are render-side. `kit.audio.*` is a no-op headless;
  animation state that affects gameplay is a sim bug.
- **Local-first, procedural-first:** a deterministic synth/tween that ships beats a model that
  doesn't. Evaluate MusicGen/stable-audio/AudioGen only after the wiring is proven.
- **The queue is the only transport** — no direct-call fallback to any audio backend.

## Tasks

### M1 — Audio backend in the kit
- [ ] `kit.audio.play(id)` + `kit.audio.music(id)` real in `run()`/`run3d` via WebAudio
      (preload from a new `assets.json` `audio` section; missing id ⇒ silent no-op), stub
      unchanged headless. Files: `runtime/engine.js`, `runtime/engine3d.js`,
      `runtime/engine.d.ts`, `runtime/kit_api*.md`. Test: node unit — headless sim with audio
      calls is deterministic; a game calling an unknown id doesn't crash render gate.

### M2 — SFX (procedural first)
- [ ] A small synthesized SFX set (hit/pickup/ui/step — WebAudio oscillators or tiny baked
      wavs we own) as kit-provided defaults, triggered from game code at `kit.register`
      actions and collisions. Files: `runtime/engine.js`, kit docs + a worked example.
      Verify: a build's actions audibly fire; probe/headless unaffected.

### M3 — Music generation pipeline
- [ ] Derive the track plan deterministically (no LLM call): one ambient bed keyed off the
      spec's genre/mood (+ per-region beds for a `world` game) — the old per-place derivation
      lesson. Files: `src/maestro/codegen/reskin.py` (plan alongside sprites/meshes).
- [ ] New job kind through the existing chain: a ComfyUI audio workflow under
      `src/config/workflows/`, payload built in `src/tools/comfyui_tools.py`, a `save_audio`
      OPERATION in `asset_chain.py`, estimate entry in `db/estimates.py`. Rides the `skin`
      finalize so staging/bundling is untouched. Test: unit on the chain op (mirrors
      save_sprite tests); fail ⇒ silent game, batch still finalizes.
- [ ] Model choice bake-off (stable-audio vs ACE-Step vs MusicGen, local) — record the call
      here; wire the winner's workflow json. Note VRAM fit vs the image queue's card.

### M4 — Animation
- [ ] Procedural first: 2D kit tween helpers (squash on land, hit-flash, idle bob — mirrors
      engine3d's walk-bob) applied render-side in `drawEntity`. Files: `runtime/engine.js`,
      kit docs. Verify: movers visibly animate in a build; sim state untouched.
- [ ] Generated frames later: N-frame sprite variants per `look` row through the EXISTING
      image pipeline (same `build_item_payload`, frame suffix ids), kit-side frame cycling on
      entity `anim` tags. Only after procedural proves insufficient — generated frames cost a
      render per frame per entity.

## Parked
- Voice/TTS — no dialogue audio until world_first.md's dialogue-as-data lands.
- A dedicated `audio` queue + worker — start on the `image` queue's ComfyUI; split only if
  VRAM contention shows up.
- Licensing for any curated SFX/music library — moot while we synthesize our own.
- Suno-class API music — conflicts with local-first; revisit only if local quality stalls.
