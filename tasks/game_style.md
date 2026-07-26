# Game Style — per-game visual identity

Verified: 2026-07-25

## Why
Every generated game reads visually samey: shapes on a dark ground (2D) or sky-fog primitives
(3D), sprites/meshes rendered by one shared workflow with no per-game art direction. A horror
game should not look like a cozy farming game. The mechanism is NOT a theme engine — it's
making the existing asset prompts, data-row visuals, and scaffold config cohere around one
per-game style. ROADMAP names the missing piece: the **styled-prompt stage** (Phase 5,
pending) — derive the game's identity once, compose it into every asset prompt.

## Background (VERIFIED 2026-07-23)
- Asset planning: `src/maestro/codegen/reskin.py` — deterministic plan from data rows' `look`
  fields (`data_files.sprite_plan_from_data`; prompts ARE the look strings verbatim), LLM
  fallback `plan_assets`/`plan_meshes` (`prompts/plan_assets.txt`, `plan_meshes.txt`). Every
  prompt goes through `tools/comfyui_tools.build_item_payload` unmodified.
- Row visuals: `src/maestro/codegen/data_files.py` envelope — `look` (art prompt), `size`,
  `shape`, `color`, `parts` (compound 2D look). A row's fields ARE its whole unskinned visual.
- Scaffold config: `src/maestro/codegen/scaffold.py` sets 2D size defaults and the 3D
  `background` (sky for world games, dark otherwise) — and `config.background` tints the 3D
  distance fog (`runtime/engine3d.js`), so one value is already most of a 3D mood.
- Per-asset regenerate exists (`src/api/routers/games.py`
  `POST /{run_id}/assets/{asset_id}/regenerate` → `reskin.regenerate_asset`) — a style pass
  can re-render without a whole re-skin.
- The spec (`prompts/spec_draft.txt`) has entities carry "WHAT + size + look" but no
  game-level art direction field.

## Guardrails
- **Style is DATA on disk** (a saved brief + row fields + saved prompts), never inlined
  Python strings — same climbability rule as prompts.
- **Additive like assets:** a game with no style pass still passes every gate and renders as
  shapes. Style must never touch the sim or the gates.
- **One brief, composed everywhere** — don't let each asset prompt re-derive tone (that's the
  monotony bug inverted: incoherence).
- **Don't fork the kit per genre.** Mood rides config + prompts + row colors; the engine stays
  neutral.

## Tasks

### S1 — Styled-prompt stage (the ROADMAP pending item)
- [ ] Derive ONE style brief per game at skin time — from the frozen spec's
      title/genre/mechanics (deterministic template or one small LLM call), saved to
      `runs/<id>/game/style.json`. Files: `src/maestro/codegen/reskin.py`, new
      `prompts/style_brief.txt`. Test: brief exists after `--assets`, stable across re-runs.
      → done when: prompts/style_brief.txt exists and reskin.py writes game/style.json
        (grep -n "style.json" src/maestro/codegen/reskin.py is non-empty)
- [ ] Compose the brief into every asset prompt (data-plan and LLM-plan paths both) before
      `build_item_payload`; the composed prompt is what lands in `assets.json` so regenerate
      reuses it. Files: `reskin.py`. Test: two assets of one game share the brief's palette
      words; existing reskin tests stay green.
      → done when: pytest tests/test_reskin.py passes and grep -n "style" reskin.py shows the
        brief composed in both the data-plan and plan_assets/plan_meshes paths

### S2 — Coherent row visuals
- [ ] `prompts/design_data.txt`: ask for a coherent palette across a game's rows (`color`
      values from one stated palette, `look` strings sharing the game's art direction).
      Verify: unskinned build reads tonally coherent (shapes share a palette).
      → done when: grep -n "palette" src/maestro/codegen/prompts/design_data.txt is non-empty

### S3 — Scaffold mood config
- [ ] Let the spec carry an optional mood/palette hint that maps to scaffold config: 2D
      background color, 3D `background`/fog tint (today hardcoded sky/dark). Files:
      `prompts/spec_draft.txt`, `src/maestro/codegen/scaffold.py`,
      `scaffold_templates/*.tmpl` config blocks. Test: scaffold unit test — a "horror" hint
      lands a dark background on a world game instead of sky.
      → done when: a test in tests/test_scaffold.py asserts a horror-hint world spec renders
        `background: "#101018"` (not sky), and passes

### S4 — Distinctness gate (manual)
- [ ] Battery check after S1–S3: build horror vs cozy vs sci-fi specs, screenshot each — they
      must read as visually distinct games. Record verdicts here; this is the workstream's
      done-condition.
      → done when: this file's S4 section contains a dated verdict table/line for all three specs

## Parked
- Generated UI chrome (fonts, panels, HUD skins) — the HUD is engine-drawn text today; revisit
  when the HUD surface grows.
- Motion/transition style — overlaps game_media.md M4 (animation); coordinate there.
- Per-game lighting rigs in 3D (beyond fog/background tint) — engine3d owns one hemisphere
  light; widen only when a build demands it.
