# Game Media — Music, Sound Effects, Animation

## Why
A game needs a soundscape and motion to feel alive. Today the asset pipeline generates **images**
(two-pass emotion img2img) and **best-effort voice** (TTS) — nothing else. **Music, sound effects,
and animation are all missing, all needed, each a separate pipeline.** This file covers the three as
distinct legs sharing one spine (declare in the manifest → generate → wire per engine).

## Background (VERIFIED — `CLAUDE.md` + prior recon)
- **Current asset gen** (`src/renpy/fns.py`, at build-end `src/maestro/run.py:62-77`):
  - `generate_images` — backgrounds, character sprites + emotion img2img variants, CGs, items,
    features, tiles. Sequential.
  - `generate_voices` — per-line kokoro TTS for VN, silent-`.wav` placeholder backfill (fail-soft).
  - **No music, no SFX, no animation** anywhere.
- **Sprite "expression" swaps** (per-line face change via the emotion map) are the closest thing to
  animation, but they're static image swaps, not motion.
- **Manifest:** `docs/asset_manifest.schema.json` defines the asset shapes — extend it per leg.
- **Engine playback surfaces:** Ren'Py has native `play music` / `play sound` (via `ir_vn`/`ir_pnc`
  + templates); Godot needs `AudioStreamPlayer` / `AnimationPlayer` in the runtime presenters.
- **Ties:**
  - `ROADMAP.md` already lists "Music generation (MusicGen / Suno)".
  - `scaleout.md` S2 — parallel asset gen must include these new passes.
  - `game_style.md` — motion/transition style tokens overlap the animation leg.
  - `asset_pipeline_redesign` direction — couple asset stubs to content authoring + a styled prompt
    stage; these legs should follow that shape, not bolt on separately.

## Guardrails
- **Declared as DATA.** Each media asset is a manifest entry the LLM/modules author (tied to the
  content that uses it), generated later — not hardcoded, not freeform.
- **Fail-soft like voices** — a missing music/SFX/animation degrades to silence/static, never blocks
  the build.
- **Engine-agnostic seam** — the IR carries the media refs; each engine projects playback. No core
  fork; reuse the projection registry.
- **Local-first generation preferred** (matches the local-runnable vision) — evaluate local models
  before committing to an API; note the tradeoff per leg.

## Leg M1 — Music (score / ambient)
- [ ] **Model choice** — local (MusicGen / stable-audio) vs API (Suno). Latency/quality/license/
      local-fit tradeoff. Prefer local; note the call.
- [ ] **Declaration** — music tied to scene/zone/mood (per node `location`+mood, per `place`, per
      story beat). Author from story tone + `story_state`. Manifest schema fragment.
- [ ] **Generation pass** — a `generate_music` step (coverage + fallback like voices).
- [ ] **Playback** — Ren'Py `play music` per scene; Godot `AudioStreamPlayer` keyed on place/mood in
      the runtime presenters. IR lift + crossref.
- [ ] **Tests:** declared tracks generate or fall back silently; the right track plays per scene/zone.

## Leg M2 — Sound effects
- [ ] **Model / source** — local SFX gen (AudioGen / stable-audio) vs a curated library (license
      check). Note the call.
- [ ] **Declaration** — SFX tied to actions/effects/hotspots/combat events (hit, door, pickup, UI,
      ability). Author alongside the event that fires them.
- [ ] **Generation pass** — `generate_sfx` (coverage + fallback).
- [ ] **Playback / trigger wiring** — Ren'Py `play sound` on the event; Godot one-shot
      `AudioStreamPlayer` from `run_action`/combat resolution. IR lift + crossref of event → sfx.
- [ ] **Tests:** an event with a declared SFX triggers it; missing SFX is silent, not a crash.

## Leg M3 — Animation (motion beyond static swaps)
This leg has a real approach fork — resolve it first (see Parked).
- [ ] **Scope + approach decision** — which of: sprite motion (idle sway, talk, expression *tweens*
      not just swaps), UI/scene transitions, effect animations (combat hits, pickups). And how:
      **generated frames vs in-engine procedural** (Godot `AnimationPlayer`/tweens, Ren'Py ATL) vs a
      Live2D-style rig. Likely mostly procedural + a few generated — decide per animation type.
- [ ] **Declaration** — animations tied to characters/actions/transitions in the IR (what animates,
      when, which motion). Schema fragment.
- [ ] **Realization** — procedural animations authored as data the runtime interprets (tween specs);
      any generated-frame animations go through a generation pass with fallback to static.
- [ ] **Playback** — Godot runtime animation driver; Ren'Py ATL/transform in the templates. Coordinate
      motion style with `game_style.md` tokens.
- [ ] **Tests:** a declared animation plays; absence degrades to static; no structural break.

## Ordering
Independent legs; suggested order **M1 (music) → M2 (SFX) → M3 (animation)** — music is roadmap-primed
and clearest, SFX is similar shape, animation is the fuzziest (approach fork). Each follows the same
declare→generate→project→test shape and each plugs into `scaleout.md` S2's parallel gen.

## Parked (owner / research)
- Local vs API generation, per modality (music especially — Suno quality vs local-fit).
- **Animation approach** — generated vs procedural is a genuine research fork; procedural is cheaper
  + more reliable, generated is richer. Probably a mix; scope at M3.
- Licensing if any curated SFX/music library is used instead of generation.
