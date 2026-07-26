# World-First — residents, quests, ambient dialogue on the codegen stack

Verified: 2026-07-25

## Why
A world game should be a PLACE where people live, not a room with a win condition. Worldgen
now delivers the place (town + wilderness ring + POIs + roads), the kit delivers the
mechanics (dialogue loop, quests, notifications) — but WHO lives there and WHAT the player
does are still whatever the model improvises in `game.ts`. The surviving design decisions
(settled 2026-07-09, do not re-litigate): residents are furniture-for-people — every place
gets plausible inhabitants, never free-form, never absent; dialogue has two registers —
set-piece (rare) and ambient (cheap, common); quest state machines are code-owned, the model
fills content; the giver acknowledges completion; the player can always answer "what do I do
next".

## Background (VERIFIED 2026-07-23)
- The place: `src/worldgen/` (towns, settlements, poi, roads, heightmap…), seeded as
  `game/world.ts` by `src/maestro/codegen/worldgen_bridge.py` when the frozen spec sets
  `world`. Exports `WORLD` (buildings/plaza/gate/grass/pois/regions/road) + `heightAt` +
  `spawnWorld`; GENERATED, edit-refused; candidate seeds offset by run_id.
- The mechanics, in `runtime/engine.js`: `talkOpen/talkStep/talkHud` (~line 628 — the whole
  dialogue/shop loop; choice → `state.talkPick`), `kit.quest` add/complete/log (~line 667 —
  milestones that do NOT end the game; win/lose reserved for the spec's ending), `kit.notify`
  toasts. Scaffold interact partials (`src/maestro/codegen/scaffold_templates/interact_*.tmpl`)
  wire the talk loop when the spec uses dialogue.
- The content substrate: `src/maestro/codegen/data_files.py` — per-game datasets in
  `game/data/manifest.json` + flat rows; envelope id/name/look/presence/size/shape/color/parts;
  `ref:<dataset>` cross-references validated deterministically; typed `game/data.ts` generated;
  `kit.spawnData` builds entities from rows and binds the row id as asset id (so residents
  authored as rows are skinnable for free). Dataset design prompt:
  `src/maestro/codegen/prompts/design_data.txt`.
- There is NO probe gate any more (`player_not_in_world`/`dead_action`/`unbound_control` were
  deleted with it — `CLAUDE.md` § THE GATES DETECT BROKEN). Nothing checks that a resident is
  reachable or that a quest is completable; that gap is deliberate and human-owned.

## Guardrails
- **Residents and quest content are DATA ROWS, not code.** The fix loop sees schema + one
  example row; content edits are row edits. A resident hand-spawned in `game.ts` is a bug in
  the prompt, not a style.
- **The kit owns dialogue/quest mechanics.** REJECT any prompt change that invites the model
  to hand-roll a dialogue state machine or a quest tracker — widen `kit.quest`/`talk*` instead.
- **Keep the ownership split:** worldgen owns the PLACE, the scaffold owns CONTROLS, the model
  owns gameplay in `game.ts`. No new rival owner of `main.ts`/`world.ts`.
- **Deterministic checks over prompt instructions** where expressible (the producer-before-
  consumer lesson): validate quest/dialogue wiring statically, don't just ask nicely.
- `kit.quest` law stands: quests are milestones; only the spec's ending wins/loses.

## Tasks

### W1 — Residents as data rows
- [ ] `design_data.txt`: when the spec sets `world`, steer toward a `residents` dataset
      (name, look, a `home` naming a WORLD poi/building index or region, optional `stance`).
      Files: `src/maestro/codegen/prompts/design_data.txt`. Verify: two world builds produce
      a residents dataset with ≥4 rows, placed via `WORLD.buildings`/`WORLD.pois`.
      → done when: grep -n "residents" src/maestro/codegen/prompts/design_data.txt is non-empty
- [ ] Worked example in `runtime/kit_api_3d.md`: spawn residents from rows with
      `kit.spawnData`, positioned off `WORLD` + `heightAt`. Verify: a build places residents
      on the ground, inside/near their home.
      → done when: grep -n "resident" runtime/kit_api_3d.md shows a kit.spawnData(...) example

### W2 — Dialogue as data
- [ ] A `dialogue` dataset convention: rows `ref:residents` + lines/options, optional
      `requires`-style gate on quest state; a worked `talkOpen(state, npc, options)` example
      reading rows in `kit_api_3d.md`. Files: `design_data.txt`, `runtime/kit_api_3d.md`.
      Verify: talking to a resident shows row-authored lines; grade with `grade-scenes`.
      → done when: grep -n "ref:residents" src/maestro/codegen/prompts/design_data.txt matches
- [ ] Ambient register: cheap per-resident one-liners (a `talkOpen` with no options, or
      `kit.notify` barks on proximity) so verisimilitude residents aren't mute. Verify: a
      non-quest resident says something.
      → done when: grep -n "ambient" runtime/kit_api_3d.md is non-empty

### W3 — Quest graphs on kit.quest
- [ ] Widen `kit.quest` with `prereq` (an add whose HUD entry and giver offer gate on a prior
      quest's completion) — code-owned semantics, model fills titles/rewards. Files:
      `runtime/engine.js`, `runtime/engine.d.ts`, `runtime/kit_api*.md`; gates untouched.
      Test: a node unit run in `runtime/` — prereq quest hidden until parent completes.
      → done when: grep -n "prereq" runtime/engine.js matches, and tests/test_kit_depth.py passes
- [ ] Giver-acknowledges-completion as the documented pattern: the giver's dialogue rows
      carry a completed-state variant. Verify on a build: turn-in line changes after
      `quest.complete`.
      → done when: grep -n "completed-state" runtime/kit_api_3d.md is non-empty
- [ ] Static check (new non-blocking `Check` in `src/maestro/codegen/module.py`): every
      `kit.quest.complete(state,"id")` string in `game/*.ts` has a matching `quest.add` with
      that id, and ≥1 quest exists when the spec sets `world`. Test: tests/ unit on the check
      with a fixture source.
      → done when: `cd src && python -m pytest ../tests/test_codegen.py -q -k quest` passes

### W4 — Gold world game
- [ ] Hand-author one reference world game in `runtime/games/` (residents + 2–3 chained
      quests + ambient barks over a worldgen world.ts). It defines the bar and becomes the
      grading gold for world builds. Verify: playable via `runtime/index.html?game=<slug>`,
      "what do I do next" always answerable.
      → done when: runtime/games/world_gold/main.ts exists (hand-authored, not a build output)

## Parked
- Schedules / day-night movement for residents — needs a time primitive in the kit first.
- Factions/reputation as a mechanic — after quests prove out.
- A completability gate (headless-drive a quest chain to done) — wants scripted-input
  self-play (quality_backlog Q3) to exist first.
- VN-era machinery (bible/objectives modules, aspects layer, IR lifts, Godot journal UI) —
  deleted with the IR; the design lessons above are what survives.
