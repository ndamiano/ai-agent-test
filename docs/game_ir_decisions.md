# Game IR — Design Decisions

Rationale behind `game_ir.schema.json` and `asset_manifest.schema.json`. The schemas are the
contract; this is the *why*. Keep them in sync — if a decision here changes, change the schema
(and vice versa).

## Thesis

The model is better at JSON than at Ren'Py — JSON is far more common in its training data. The
Ren'Py "freedom" the generator had was illusory anyway: the prompts already constrained it to a
fixed verb set (examine / take / use / talk / move / win) and a fixed node shape (lines +
jump/menu). The IR just *names* those shapes so they can't be malformed. A deterministic backend
compiles the IR to an engine (Ren'Py today; RPGMaker / web / Twine later), so most error classes
the repair prompts fight — quote escaping, speaker format, menu indentation, dangling jumps —
become impossible by construction, and validation/reachability become data walks instead of
regex over engine source.

## Genres on one IR

- **visual_novel** — `places` empty (or one backdrop place); the game *is* the node graph.
  `start.node` set.
- **point_and_click** — `places` = rooms with `rect`-positioned hotspots; `nodes` = NPC dialogue
  that talk-hotspots call. `start.place` set.
- **rpg** *(future)* — `place.kind` = world_map / town / interior; interactables positioned by
  `cell` (grid) instead of `rect` (pixels); NPCs = interactables with a `talk` action + a sprite +
  a cell. Nodes unchanged. Combat/stats are **not** modeled yet — they'd be a new component, not a
  change to this IR.

## Resolved decisions

1. **Narration = `speaker: null` only.** No reserved `"narrator"` id, so a real character *can*
   be named "The Narrator". Fewer reserved words.
2. **`position` is one object with exactly one projection** — `rect {x,y,w,h}` (pixels) or
   `cell {x,y}` (grid), enforced by `oneOf`. Shared across genres; more projections can be added
   later without touching call sites.
3. **Numeric variables + comparisons.** `variables` declares numeric state (trust, gold, days).
   A `var` condition compares with op ∈ {==,!=,<,<=,>,>=}. The compared `value` is an **operand**:
   a literal number *or* another variable (`{var: ...}`) — so `sleight_of_hand > perception`
   works. Effects `set_var` / `add_var` mutate. Available to every genre, not just RPG.
4. **State changes attach to a concrete beat** — a dialogue `line` (fires after it's spoken), a
   menu `choice`, or a use `outcome`. No node-level effects: the timing was ambiguous.
   **Emotion is per-line too, for the same reason:** a `line.emotion` (bounded enum
   neutral/happy/sad/angry/surprised/worried) is the one expressive control the author writes; the
   sprite variants are *derived*, not authored. `ir_assemble` scans the lines to build a
   character's `expressions` map (only emotions actually spoken, so 2 used ≠ 6 generated), the
   asset pipeline img2img's each variant off the neutral base, and `ir_vn` swaps the speaker's
   sprite per line. Bounded enum + derived assets keeps authoring cheap for a small model and
   bounds generation cost; a missing variant degrades to the neutral face, never a broken build.
5. **`goal` (win condition) is optional.** Open-ended games (Stardew-shaped) may have none. A
   definitive ending is a node with `end: {type: end}`; an open world loops via
   `end: {type: return}`. So "the end" is per-node, and not every game has a win.
6. **Assets are ids here; the manifest owns generation.** `asset_manifest.schema.json` holds
   prompts, dimensions, `transparent`, animations, style anchors. Shared id space — every asset id
   the IR references must exist in the manifest (a cross-schema check). Keeps the IR engine- *and*
   renderer-agnostic.
7. **RPG exits are just a `move` action.** A town gate / house edge is an interactable that moves
   you. No separate `edges` list.
8. **`use` is multi-clause, not a success/failure pair.** `clauses: [{requires, outcome}]` is
   evaluated top-down — first clause whose condition holds wins; if none match, the optional
   `fallback` outcome fires. A plain lock is one clause + a fallback, but the same shape scales to
   "key opens it / lockpick opens it if skill high enough / else it's jammed" without a special
   case. Replaced the old `requires`/`success`/`failure` triple before any content locked to it.

## Asset manifest decisions

- **Separated from the IR** so generation concerns never bloat game logic.
- **`kind`** (background / sprite / portrait / item_icon / map / tileset) drives both the IR slot
  it can fill and the generation defaults (a background is 1280×720; a portrait is tighter).
- **Animations are optional and per-state** (idle / walk / talk / attack). A still image omits
  them.
- **`transparent`** flags alpha-background assets (sprites, portraits, item icons).

## Combat (RPG)

Combat is **data, never a loop**. The same projection discipline as `position`: per-ability data
says what an action *does* (cost, targeting intent, stat effects); it never says how time passes.

- **`combat_model`** (turn_based / real_time / auto / none, default none) is the ONE place the loop
  style is named. The engine projection reads it; ability data stays loop-agnostic.
- **Stats are game-defined, not hardcoded.** No baked hp/mp — a `stat` declares an id + `role`
  (resource_depletable / resource_regenerating / modifier / rating) that tells the projection how
  to use it. `combatant`s carry a stat block; `ability`s cost/affect stats via `combat_effect` +
  `formula` (base + optional stat scaling; no RNG/crit in the data).
- **Two effect families, deliberately separate.** Narrative `effect` mutates world state
  (flags/vars/items); combat `combat_effect` operates on stats/statuses. They bridge one way: a
  `combat_effect` with a `world` key fires a narrative effect (e.g. a fight outcome sets a flag).
- **A fight is entered by an action.** `action_start_combat {encounter, requires?}` is one of the
  closed action verbs, so *anything that can hold an action* — a hotspot, an NPC, a chest — can
  start combat, with the same optional `requires` gate as the other verbs. No separate trigger
  system.
- **Combat resolves back into the normal flow** — `encounter.on_victory`/`on_defeat` reuse
  `node_end`, so a fight returns to dialogue/navigation like anything else.
- **Reuse over reinvention** — abilities/encounters reuse `condition` (gates), `position`
  (placement), `node_end` (resolution). The combat layer adds vocabulary, not a parallel system.

Note two intentional divergences to keep straight:
- **Stat clamp is committed (compiler MUST clamp to [min,max] on every mutation); variable clamp
  is advisory.** Combat needs deterministic bounds; story counters don't.
- **`faction` means two different things.** `targeting.faction` (self/ally/enemy/any) is *relative
  to the user*; `combatant`/`encounter` faction (player/ally/enemy/neutral) is the *absolute side*.
  Same word, different enums, by design.

`stats` vs `variables`: use a **variable** for story/narrative counters (trust, days, gold-as-
plot); use a **stat** for anything a `combat_effect` touches or an ability scales on. When in
doubt, if it lives on a combatant and changes mid-fight, it's a stat.

## Still open

- **var min/max clamping** — advisory for now; if it becomes a problem, the compiler enforces it.
  Not a schema constraint.
- **Style consistency** — the manifest stubs *both* a global `style` block and a per-asset
  `style_ref` anchor. Pick one before finalizing.
- **Per-asset model/sampler override** vs global only — global for now.
- **Story-beat → fight entry.** A fight is entered via `action_start_combat` (an interactable).
  Still open: whether a node should *also* flow straight into a fight without a click — a `node_end`
  `{type:"encounter", ref}` variant. Not needed yet; add if a scripted ambush wants it.
- **Is `encounters` the right home?** The encounter def calls itself "a place subtype." It could
  instead extend `places` via a combat `place.kind`. Kept as a separate top-level list because its
  shape (combatants/victory/defeat) shares nothing with a place's interactables. Revisit if it
  feels bolted on.
