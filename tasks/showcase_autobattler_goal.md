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
- **Entrance beat**: pressing Fight slams the enemy board in from the right with screen shake and
  a "FIGHT!" banner, then combat auto-plays.
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
- [ ] S3. Roster has 12+ distinct creatures; each readable at a glance (art + hover card with
      stats and traits).

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
- [ ] C3. Loss of a fight costs player HP scaled to surviving enemies; the scoreboard is legible.

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
- [ ] F3. The entrance beat lands (enemy board slams in, FIGHT! banner) and battle speed /
      mute / volume settings audibly and visibly work.

## Working rules
- Fix notes name checklist items (e.g. "targets T2, C1"). No untargeted fixes.
- Build request states every system above as a concrete on-screen thing (see
  request-prompt-concreteness memory) — the checklist is the source the request is written from.
- Art rides the deferred pass: build first on ninfer, render on the ComfyUI flip, judge F1 after.
- Grades recorded per playtest below.

## Playtest log
(none yet)
