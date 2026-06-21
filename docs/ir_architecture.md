# IR Architecture — Substrates + Composable Mechanic-Modules

How Maestro scales the Game IR toward "encode *any* game" without enumerating genres forever.
This is the design rationale; the code lives in `src/maestro/modules.py`, `src/maestro/discrete/`,
and the per-engine `projections.py`. Companion: `game_ir_decisions.md` (the IR contract's *why*).

## The model in one paragraph

A game = **one substrate** (execution model — how time/space work; hand-built by us; the AI never
invents one) + **a composed set of mechanic-modules** (the AI selects them). Each module
contributes a *data-shape* (schema fragment + skeleton + done-conditions + deps) and *one
projection per engine it supports*. Composition merges the data-shapes into a single spec; the
existing frozen-spec + executor loop fills the holes, validates, fixes, and compiles. It
guarantees **working** (compiles + playable without errors), not yet **good**. This reorganizes
*where component-shapes come from* (previously hardcoded per `genre`), not the executor.

## Layer 1 — Substrates (two, hand-built, the AI picks one)

> **Substrate test:** does success/survival depend on **sub-second timing or positioning**? Yes →
> `real_time_sim`. No (you could pause indefinitely between meaningful inputs) → `discrete_state`.
> This is the player's control/stakes loop — NOT the engine's internal update rate, NOT whether
> the avatar moves smoothly.

| substrate | validation | covers |
|---|---|---|
| **discrete_state** *(built)* | static **data-walk** (reachability + done-conditions) | VN, point-and-click, RPG, deckbuilder, tactics, untimed puzzle, 4X, IF, idle (clock-accrual mechanic), coarse-tick MMO |
| **real_time_sim** *(designed, not built — no 3D/engine infra yet)* | in-engine **playtest** (scripted agent playthrough) | action, shooter, platformer, ARPG, racing, Factorio, FF14, timed puzzle |

Internal sim ticks don't make a substrate (Factorio at 60 UPS is real-time because aiming is
sub-second; RuneScape at 600ms is discrete). **Presentation/engine is a separate axis** (2D-DOM /
2D-canvas / 3D): discrete logic can target any presentation (the Witness = discrete puzzles in 3D);
validation tracks the *logic* substrate, not the presentation. **Multiplayer/authority** is an
orthogonal dimension, not a substrate.

**One substrate per game** is an accepted limitation: a real-time overworld with turn-based
battles would need per-scene substrates (out of scope). Most such cases collapse to one substrate +
a mechanic (timed inputs in turn combat = discrete + a quick-time-event mechanic). The safe
asymmetry: discrete logic runs on a real-time engine, but real-time logic cannot run on a discrete
engine — so a mixed game picks real-time and degrades discrete parts to scenes, never the reverse.

## Layer 2 — Mechanic-modules (many, composable; the AI selects + fills)

A `Module` (`maestro/modules.py`) bundles what used to be scattered, hardcoded-by-`genre`
registries:

```
components   on-disk component ids it OWNS (a *vocabulary* module owns none)
schemas      structural validators (component_id -> validator)
skeletons    authoring shapes shown to the agent
baseline     code-enforced done-conditions (component_id -> [check, ...])
deps         intrinsic build order
checks       custom validate check types (registered into maestro.validate)
tool_names   decider tools this module contributes
assemble/crossref   IR slice + reference checks (today the discrete lifts are presence-driven in
                    ir_assemble/ir_crossref, mirroring how combat lives there)
sub_runner   stateful build sub-loop (dialogue->nodes, navigation->places)
projector    executor view fn
action_verbs verbs this module adds to navigation's verb set
projected    True if it needs an engine renderer beyond plain IR assembly
```

`compose(module_ids)` unions the active modules into one bundle every former genre-keyed lookup
now reads. Baselines merge **per component** (union of checks; raise `min`; union `each_has`
fields) — which is why a visual novel's `premise` carries the branching-cast contract while a
point-and-click's doesn't: the `dialogue` *spine* module layers those checks on, the `cast`
module alone doesn't. Two module *flavors*: **content** modules own a component (+ maybe a
sub-loop); **vocabulary** modules own none (e.g. `economy` — flags/variables/items + effect/
condition vocab ride inside other modules' beats).

**Engine projections register separately** (`renpy/projections.py`, `web/projections.py`) keyed
`(engine, module_id)`, because a module's schema is substrate-agnostic while its projection is
per-engine. A `projected` module with no projection for the chosen engine makes the compile
**fail fast** (`unprojectable`) instead of silently dropping content.

### The discrete module roster (as built)

`cast` (premise) · `assets` (asset_manifest) · `dialogue` (nodes, story spine) · `dialogue_npc`
(nodes, supporting barks) · `navigation` (places) · `economy` (vocabulary) · `card_play`
(matches — wagering card games).

### Presets (what the classifier emits; `genre` survives as the preset name)

- `vn` = cast + assets + dialogue + economy → engine renpy
- `point_and_click` = cast + assets + dialogue_npc + navigation + economy → engine renpy
- `card_ante` = cast + assets + dialogue_npc + navigation + economy + card_play → engine **web**

## Unique mechanics — the completeness valve

A game is encodable ⟺ its mechanics ∈ `closure(module library)` under composition. A genuinely
novel mechanic is handled two-tier: **Tier 1 (preferred)** make it a new bounded module (schema +
per-engine projection) — it joins the library; "missing mechanic" means *build the primitive*,
never "impossible," so the frontier moves outward rather than being a wall. **Tier 2 (escape
hatch)** an agent-authored engine-script module gated by playtest instead of data-walk — trades
the can't-be-malformed guarantee for coverage on the novel slice, which is what makes the system
*universal*.

**Decomposition discipline:** if a candidate "mechanic" name evokes a *genre*, it's too broad —
break it to primitives ("automation" = placement + logistics + production-recipe + power). Same
rule that keeps substrates few keeps modules sharp.

## card_play — the worked example

"Make me a card game where you wander the world and play for ante" → `card_ante`. Wander = the
navigation overworld; ante = economy (a `gold` variable + payout effects); the match = the new
`card_play` module. A `matches` component declares each match (`card_model` ∈ {high_card,
blackjack}, `opponent`, `ante {var, amount}`, `on_win`/`on_lose`); an overworld interactable's
`play_match` action enters it; it resolves back via `node_end`, mirroring how combat enters an
encounter and returns via `on_victory`. Rules are **engine-implemented** (Tier-1): the IR only
parameterizes stakes/opponent/payout; the web runtime (`runtime/engine.js` `runMatch`) owns the
rules and the opponent. `card_play` registers a **web** projection and no Ren'Py one — so a card
game builds on web, and a Ren'Py build fails fast with a clear message. That is seam #1 (schema
agnostic, projection per-engine) made concrete.
