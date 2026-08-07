# Showcase goal: autobattler

The showcase-tier demo: built staged, then iterated with targeted fix notes until it meets THIS
document. Every playtest grades against the checklist; every fix note names the checklist items it
targets. Items are experience claims a player can verify in one run — not implementation notes.

Game: **Essence Bazaar** (working name) — buy creatures from a rotating shop, place them on a
board, watch them fight escalating enemy warbands, chase trait synergies, survive to the final
round.

## Screen & visual shape

- **Menu screen**: New Game, Settings. Settings: battle speed multiplier, global mute, sound
  volume. The game has sound and obeys all three.
- **In game**: shop along the bottom — 4 creatures + a shuffle (reroll) button, each card shows
  image/name/cost, hover shows stats and traits. Bought units land in a tray on the right edge;
  drag from tray onto the grid.
- **Battlefield is 3D** (vendored three.js): dark bazaar arena floor, two facing grids of
  4 rows × 3 columns (player left, enemy right), code-built glowing slab cells, fixed 3/4 camera.
- **Creatures are miniature standees**: one stylized 2D flux sprite (matted webp) on a small 3D
  base, upright in the scene like a tabletop miniature, **unlit materials** (dodges the measured
  3D near-black lighting failure). No internal animation ever — all motion is whole-miniature
  transforms: lunge to attack, shake on hit, tip over + fade on death, base glow when buffed.
  One render per creature reused in shop card, tray chip and standee.
- **Entrance beat** (revised 2026-08-04): the enemy warband slams in from the right at PLANNING
  start — the player scouts the actual enemy board while building a lineup. Pressing Fight slides
  the two grids together until adjacent (no empty gap mid-battle), FIGHT! banner as they meet;
  they part again for the next planning phase.
- **Melee reach**: ~1.5 tile-widths between standee edges — swings start before contact.
- **DOM/3D split (forced)**: 3D is the battlefield only; menu, settings, shop, tray, tooltips,
  panels are HTML/CSS over the canvas. The one 3D interaction is the drop raycast picking a cell.

## Goal checklist

### Economy (the greed-vs-tempo dial)
- [ ] E1. Gold arrives each round; income visibly scales as the run progresses.
- [ ] E2. Reroll costs gold; saving vs spending is a real decision a player can articulate.
- [ ] E3. A player who over-spends early feels it later — and can see why.

### Shop & roster
- [ ] S1. Shop offers 4 creatures a round + shuffle, from a pool with cost tiers (1–4); higher
      tiers appear as the run advances.
- [ ] S2. Owning three copies of a creature visibly combines them into a stronger version.
- [ ] S3. Roster has 25+ distinct creatures (revised up from 12, playtest 2026-08-03); each
      readable at a glance (art + hover card with stats and traits).

### Board & positioning
- [ ] B1. Creatures are placed by drag; front/back placement changes outcomes (tanks soak in
      front, fragile damage lives in back).
- [ ] B2. Board cap grows during the run, so late boards feel bigger than early ones.

### Traits & synergy
- [ ] T1. Each creature carries 1–2 traits; thresholds (e.g. 2/4 of a trait) grant buffs.
- [ ] T2. Active trait counts and their effects are visible on a panel during planning AND
      legible in combat (a buffed unit looks buffed).
- [ ] T3. At least three distinct viable comps exist across a few runs — not one dominant build.

### Combat (the watchable payoff)
- [ ] C1. Fights auto-resolve over ~20+ seconds and are followable: attacks animate, HP bars
      move, deaths are events, damage numbers fly.
- [ ] C2. Abilities fire visibly and matter (a heal turns a fight, a splash clears swarms).
- [ ] C3. (revised 2026-08-04) Player HP drops ONLY on a lost round, scaled to surviving enemies
      (~15/100 typical); unit deaths cost nothing by themselves. Round ends flash a ~2s banner
      (won: +gold from winning + interest; lost: interest only) and roll straight into planning —
      no button. Proper screens only at run end (HP 0 / round 12 survived).

### Opponent & run arc
- [ ] O1. Enemy warbands escalate across ~12 rounds and vary in shape (swarm round, tank round,
      synergy round) so the player adapts, not just outgrows.
- [ ] O2. A run ends in a proper victory screen (survive round 12) or defeat screen (HP 0), with
      a one-line cause a player can read ("overwhelmed by the swarm round with no splash").
- [ ] O3. A full run lands ~15 minutes.

### Feel
- [ ] F1. Dark whimsical look; every creature has standee art; the 3D bazaar arena reads as one
      place; unlit standees stay readable.
- [ ] F2. UX friction low: hover cards, drag that works, no dead-end states, restart from the end
      screens.
- [x] F3. The entrance beat lands (enemy board slams in, FIGHT! banner) and battle speed /
      mute / volume settings audibly and visibly work. (2026-08-04: sound + menus judged "fine,
      improvable, accepted" by Nick.)

## Working rules
- Fix notes name checklist items (e.g. "targets T2, C1"). No untargeted fixes.
- Build request states every system above as a concrete on-screen thing (see
  request-prompt-concreteness memory) — the checklist is the source the request is written from.
- Art rides the deferred pass: build first on ninfer, render on the ComfyUI flip, judge F1 after.
- Grades recorded per playtest below.

## Status
SHIPPED to prod showcase tier 2026-08-04 (title "Arena Combat", run 499d9fefaa43, ~52 fix
builds from the staged one-shot, full 45-asset art coverage). Balance is the open workstream:
fixes continue locally on the same run; re-ship = re-tar run dir → extract on prod → re-stage
(same id, demo entry already in place). Checklist grading pending Nick's full-run verdicts.

## Playtest log

### 2026-08-03 — v1 (staged build 499d9fefaa43, art in)
Overall: strong positive vibe, "first one that doesn't feel awful" — but not coherent yet.

Working: standees + bases + HP bars look great; shop (4 cards, art, cost, stats+trait, shuffle)
fully functional; buying works; trait panel live with counts and active buffs (T1 ✓, T2-planning
✓); THREE MAGES COMBINED to M★ (S2 ✓); round counter 1/12, player HP, gold, speed slider (x2);
combat resolves with targeting, damage numbers, HP bars, narrated log; enemies have their own
roster (Wraith, Goblin); Support synergy applied in combat.

Broken / missing, in fix order:
1. ONE 4x3 board — enemies spawn interleaved on the player's own grid (goal: two facing grids).
2. Fight button dead: enables only when enemies on grid, but enemies spawn inside startFight.
3. Crash after victory: checkCombinations combos.js:43 "template is undefined"
   (afterBattleVictory → showEndScreen path) — blocks the round loop.
4. Back-row standees render with opaque black rectangles (alpha ignored, positional).
5. Combat is entirely static in-place "fires at" — no melee movement/lunges (C1 partial).
6. Only 5 units seen, all cost 3 (S3 ✗; tier pricing flat; tank/ranger/sorcerer exist in data,
   unreached).
7. Drag goes to the sidebar chip widget, not the 3D board; tray grows unbounded (cap ~8);
   Clear Board button unwanted.
8. Composition: half the frame is empty foreground.
Unverified: E1-E3, B2, T3, C3, O1 shapes, O2 screens, O3, sounds/menu/entrance beat.
