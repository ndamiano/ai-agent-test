# IR Architecture — Substrates + Composable Mechanic-Modules

How Maestro scales the Game IR toward "encode *any* game" without enumerating genres forever.
This is the scaling rationale; the module mechanics live in `CLAUDE.md` and the code in
`src/maestro/modules/` + the per-engine `projections.py`. Companion: `game_ir_decisions.md` (the
IR contract's *why*).

## The model in one paragraph

A game = **one substrate** (execution model — how time/space work; hand-built by us; the AI never
invents one) + **a composed set of mechanic-modules** (the AI selects them). Composition unions the
modules; the frozen-spec + agent loop fills the holes, validates, fixes, and compiles. It
guarantees **working** (compiles + playable without errors), not yet **good**.

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

A `Module` is **behavior, not a data bag**: the only required method is `get_errors(context) ->
[Error]`, and the core invariant is **detector = fixer** — a module reports an error only if *it*
can fix it. `compose(module_ids)` resolves ids → live instances; the loop reads gating off the
composed set, never a genre string. Two flavors:

- **content** modules author a component (`scenes`→nodes, `world`→places, `card_play`→matches,
  `cast`→characters, `story`→story, `inventory`→items).
- **cross-cutting** modules author none: `state` is the always-on wiring invariant (every declared
  flag/var/item needs a producer **and** a consumer), `human` is the HITL channel.

**Engine projections register separately** (`renpy/projections.py`, `web/projections.py`) keyed
`(engine, module_id)`, because a module's schema is substrate-agnostic while its projection is
per-engine. A `projected` module with no projection for the chosen engine makes the compile
**fail fast** (`unprojectable`) instead of silently dropping content. (Full mechanics: CLAUDE.md.)

### The discrete module roster (as built)

`cast` (characters) · `story` (arc + endings) · `scenes` (nodes — the narrative graph) · `world`
(places) · `assets` (asset_manifest) · `inventory` (items) · `state` (wiring) · `card_play`
(matches — wagering card games) · `human` (HITL). `assets`/`state`/`human` are always-on.

### Module selection (the proposer picks from the catalog — no genre/preset box)

The spec drafter is shown the selectable modules (`id` + `description`) and picks them as a
`{id: reason}` map. `Module.resolve_modules` force-includes the foundation (`human`, `assets`,
`state`), expands each pick's `requires` (`scenes`→`cast`, `card_play`→`world`), validates a
realization module is present (falling back to a VN bundle), and derives the engine = the first
engine that can project the whole set. Typical shapes:

- story-forward → cast + story + scenes → engine renpy
- explorable world → cast + world (+ scenes) → engine renpy
- wagering cards → card_play (+ world, cast) → engine **web** (only web renders cards; the Ren'Py
  build fails fast via `unprojectable`)

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

"Make me a card game where you wander the world and play for ante": the proposer picks `card_play`;
its `requires` pull in `world` (the overworld) and `cast`. A `matches` component declares each
match (`card_model` ∈ {high_card, blackjack}, `opponent`, `ante {var, amount}`, `on_win`/`on_lose`);
an overworld interactable's `play_match` action enters it; it resolves back via `node_end`,
mirroring how combat enters an encounter and returns via `on_victory`. Rules are
**engine-implemented** (Tier-1): the IR only parameterizes stakes/opponent/payout; the web runtime
(`runtime/engine.js` `runMatch`) owns the rules and the opponent. `card_play` registers a **web**
projection and no Ren'Py one — so a card game builds on web, and a Ren'Py build fails fast. That is
the schema-agnostic / projection-per-engine seam made concrete.
