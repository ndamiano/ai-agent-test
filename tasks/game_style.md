# Game Style — LLM-Authored Look & UX + Projection Theming

## Why
Every generated game currently looks the same — a fixed Ren'Py template and a fixed Godot runtime
with hardcoded chrome (title screen, pause menu, inventory strip). The projection is one-size. Magic
output needs per-game visual identity + better in-game UX: a horror game should not look like a cozy
farming game. Let the LLM author a **style layer** the projections theme off, and extend the
projection work to consume it.

**Scope note — this is the OUTPUT game's UX/style, NOT the Maestro app's.** The app's create-flow UX
is `create_ux_research.md`. This file is about how the *generated game* looks and feels. Keep them
separate.

## Guardrails
- **Style is DATA (spec/IR), not fixed prompt text.** Per `generalization_mandate`: don't hardcode
  one look into the projection or prompt — the model authors tone/theme per game; the projection is
  a neutral renderer of those tokens.
- **Projection stays an engine-agnostic seam.** Adding style must not fork the core — reuse the
  `(engine, module_id)` projection registry pattern; every engine that renders a game themes off the
  same style tokens.
- **Style can't break structural correctness.** A theme may restyle UI but not remove required
  affordances (a save button, a readable dialogue box). Structural checks stay authoritative.
- Start with **visual theme** (palette/type/layout/motion); deeper interaction-UX is a later scope
  decision (see Parked).

## Background (from `CLAUDE.md`)
- **Projections are fixed code today:**
  - Ren'Py: `renpy/ir_vn.py` / `ir_pnc.py` → `script.rpy`, plus `renpy/renpy_templates/` +
    `renpy/templating.py` (`render_template`). Styling is baked into the templates.
  - Godot: a static GDScript runtime (`godot/runtime/*.gd`) with a `PRESENTERS` registry and
    **hardcoded chrome** — title screen, Esc pause menu, ending overlay, inventory strip, HP bars.
- **Projection registry** keyed `(engine, module_id)` (`renpy/projections.py`, `godot/projections.py`);
  a `projected` module with no projection for the chosen engine fails `unprojectable`.
- **No style/theme data in the IR** — `ir.meta` carries `presentation` (2d/hd2d) but nothing about
  palette/typography/UI skin.
- Related: the asset pipeline (`renpy/fns.py`, ComfyUI) already generates art; a style layer should
  coordinate with it (fonts, UI frames, backgrounds generated to match the theme) —
  see `asset_pipeline_redesign` direction.

## T1 — Style component + module
- [ ] **Design the style token set** — palette, typography, layout density, motion/transitions, UI
      skin, overall mood — the minimal set that meaningfully differentiates games. Grounded in the
      game's concept + story tone.
- [ ] **New `style` module/aspect** authoring a `style` component (foundation-ish: most games want
      one). It authors from the concept/story (a checkable component: required tokens present, tied
      to the game's tone).
- [ ] **New component shape → full checklist:** write tool(s), `ir_assemble` lift into
      `ir.meta.style` (or a top-level `style`), a `docs/game_ir.schema.json` fragment (+ rationale).

## T2 — Projections consume style (the "more projection work")
- [ ] **Ren'Py:** parameterize `renpy_templates/` + `templating.py` off the style tokens — screen
      styles, colors, fonts, textbox skin, transitions. One neutral template themed by data.
- [ ] **Godot:** drive the runtime `Theme` + presenter chrome (title/pause/ending/inventory/HP UI)
      off the style tokens instead of hardcoded values. Fonts, colors, panel skins dynamic.
- [ ] **Both engines read the same `style` tokens** — no per-engine style authoring; the seam stays
      engine-agnostic.

## T3 — Asset coordination (optional, higher polish)
- [ ] Generate UI assets to match the theme (fonts, frames/panels, button skins, backgrounds) via the
      asset pipeline, keyed off the style tokens — so the generated art and the UI chrome cohere.

## T4 — In-game UX beyond visuals (scope decision — see Parked)
- [ ] Consider whether the LLM should also influence interaction UX (pacing, control hints,
      accessibility affordances) — likely a follow-up once visual theming lands.

## Ordering
T1 (schema + style module) first — nothing to consume without it. Then T2, Ren'Py before Godot
(simpler templating surface). T3/T4 are polish/scope-outs. Quality-gate with the genre battery: a
horror vs cozy vs sci-fi game must read as visually distinct.

## Parked
- Visual theme vs interaction-UX depth — start visual, decide interaction later.
- How far to let style go before it threatens legibility/accessibility (guardrail: structural
  affordances are non-negotiable).
- Relationship to `hd2d`/`presentation` — style tokens should apply across 2d/hd2d presenters.
