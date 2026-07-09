# Aspects + Scale — Work Plan

Two workstreams for the maestro module system. **Do Workstream A (Aspects) first** — it is the
higher-priority slop-fighter and Workstream B (Scale) composes more cleanly on top of it.

This doc is self-contained: it carries the rationale so an agent with no prior conversation can
execute it. Read the "Why" of each workstream before touching code.

---

## Shared background (read once)

The build system: a game = a composed set of **`Module`s** (`src/maestro/modules/`). A `Module` IS a
list of `Check`s, each a `(detect → fix)` pair. The non-LLM loop (`agent_loop.py`) sweeps every
module's `get_errors`, subtracts human waivers, and drives ONE fix per step. **Done = `get_errors`
empty**, never the LLM claiming done. Content is grown one item per step: a count/demand shortfall
fans into per-slot create-errors via `checks.slot_errors`, each with a stable `path` (so completing
one shrinks the set = visible progress). The write tool is slot-guarded (no overwrite, next slot).

Key files:
- `src/maestro/modules/module.py` — `Module` ABC, `Check`, `Error`, `compose`, `resolve_modules`,
  `expand_modules`, `selectable_catalog`, `register_module`, projection registry.
- `src/maestro/modules/checks.py` — check primitives (`slot_errors`, `length`, `each_has`,
  `distinct`, crossref/compile wrappers).
- `src/maestro/modules/cast.py` — worked count-driven example (min_characters → per-person creates).
- `src/maestro/modules/inventory.py` — worked **demand-driven** example (dangling ref → per-item creates).
- `src/tools/spec_tools.py` — `propose_spec` / `amend_spec` / `freeze_spec`; resolves modules +
  params floors into the frozen spec.
- `src/maestro/prompts/spec_write.txt` — the proposer prompt (picks modules from the catalog).

Real APIs you will build on (verified):
- `Check(code, detect, job="author", blocking=False, when_clean=False, prompt=, tools=, skeleton=,
  guard=, context=, build_prompt=, run=, max_tokens=)`.
- `checks.slot_errors(n, *, type, code, component, noun) -> [Error]`.
- `Module.params() -> Dict` — knob FLOORS: int knobs take `max` when composed, list knobs the union.
  Resolved into `spec["params"]`; a check reads it via `ctx.param(name, default)`.
- `Module.requires: Tuple[str, ...]` — modules auto-pulled when this is chosen (expanded
  transitively by `expand_modules`).
- `Module.selectable: bool` — `False` = always-on foundation, hidden from `selectable_catalog()`.
- `resolve_modules(ids) -> (modules, engine)` — force foundation, expand requires, pick engine.

---

# WORKSTREAM A — Aspects (nouns-primary)

## Why
Modules are **generic** (`economy`, `world`) — the LLM picking "economy" gives no signal for HOW the
economy works (pokemon shops vs cookie-clicker idle production), so the shaping falls to freeform
prompt text, which is where slop lives. We add a **specificity layer**: the LLM picks concrete,
shaping-carrying **aspects** (`shop`, `idle_production`, `needs`, `affinity`) that RESOLVE to the
generic engine modules and add their own specific checks + params. Generic modules become internal
implementation the LLM never names directly (**nouns-primary**, not hybrid).

Two decisions already made (do not re-litigate):
- **Nouns-primary, not hybrid.** A generic module picked without an aspect is an underspecified
  state (freefall → slop). Making picking aspect-only makes that state unrepresentable.
- **An aspect is a Module.** The machinery already exists. An aspect = a `Module` with
  `layer="aspect"`, a concrete LLM-facing `description`, `requires` pointing at its engine module(s),
  its own specific `checks`, and `params()`. Engine modules become `layer="engine"` (internal, hidden
  from the catalog). `requires` transitivity already pulls the engine module in for free.

Two aspect shapes (both are just `Module`s):
- **Content aspect** — authors items via `slot_errors`/demand (`shop`, `deck`, `needs`, `quests`).
  Instances (blacksmith, a card, a hunger meter) are ITEMS grown one-per-step — the existing
  machinery, NOT a new tier.
- **Modifier aspect** — instanceless config: sets `params` + maybe one global check, authors nothing
  (`affinity`, `permadeath`). Already supported — `state`/`human` author nothing.

Instances do NOT add check LOGIC. An aspect owns ONE check machine; each instance declares params and
the shared check fires against that instance's target (one fixable error per instance). If an
instance needs genuinely new check logic → it's a different aspect, not an instance.

## Guardrails (enforce; reject violations)
- **Every aspect requires ≥1 engine module.** An aspect with no owning engine module is illegal.
- **An aspect shapes ONE engine module (or minimally few) and is reusable across unrelated games.**
  `affinity` works in a non-pokemon fighter; `shop` works in an RPG with no combat. A single aspect
  that flips 5 modules is the genre-preset box reborn — reject it. Composition of small aspects is
  fine; one fat bundle is not.
- Keep `CLAUDE.md`'s "no genre/preset box" invariant. Aspects are composable adjectives, not genres.
- No backwards-compat shims. Reclassify and move call sites (per `CLAUDE.md` code standards).

## Tasks

### A1 — Introduce the engine/aspect distinction ✅ DONE
- [x] Add `Module.layer: str = "engine"` (default) in `module.py`. `layer == "aspect"` = LLM-facing.
- [x] Change `selectable_catalog()` to list `layer == "aspect"` modules (keep hiding `selectable=False`
      foundation). The catalog the proposer sees is now aspects only.
- [x] Add validation in `resolve_modules` / a registration check: an `aspect` module MUST declare
      `requires` with ≥1 `engine` module, else raise at register time (fail fast, not at build).
      → enforced in `register_module`; aspects import LAST so their engine deps are already registered.
- [x] `_REALIZATION` gate still works: an aspect's required engine module (`scenes`/`world`) satisfies
      "has a realization module". Verified — `resolve_modules(["dialogue"])` finds `scenes` + engine.
- [x] Tests: an aspect resolves to its engine modules; catalog shows aspects not engines; an aspect
      with no engine `requires` is rejected. (`tests/test_module_resolution.py`)

### A2 — Reclassify the existing catalog ✅ DONE (with NO-RENAME deviation)
**DEVIATION (owner decision):** did NOT rename any files or engine module ids. `scenes.py` stays
`scenes.py` with `id="scenes"`; `world`/`combat`/`story` keep their ids. The thin aspects are ADDED
in a new `maestro/modules/aspects.py` and wrap the engine modules by `requires` — no file/id churn,
no prompt renames. Aspect ids are kept DISTINCT from the engine ids they wrap (registry is keyed by
id): `turn_combat` (not `combat`), `roaming_encounters` (not `wild_encounters`).
- [x] Mark current machinery modules `layer="engine"` (internal): `cast`, `story`, `combat`, `world`,
      `scenes`, `inventory`, `wild_encounters`, plus foundation `state`/`assets`/`human`
      (already `selectable=False`, default layer engine).
- [x] Thin degenerate aspects (in `aspects.py`) so the vocabulary stays uniform (LLM always picks
      aspects): `dialogue`→`scenes`, `narrative`→`story`, `exploration`→`world`,
      `turn_combat`→`combat`, `items`→`inventory`, `roaming_encounters`→`wild_encounters`.
      (Added `items`/`roaming_encounters` beyond the doc's four so no pick capability is lost.)
- [x] `cast`/`assets`/`state`/`human` stay foundation (auto-pulled), no aspect needed.
- [x] Tests: composing the `dialogue` aspect yields the same ENGINE module set as picking `scenes`
      does today (behavior parity for the VN path — the aspect id itself is inert).

### A3 — First real proof: economy + its aspects
This is the proof the layer earns its keep. Build the engine module thin (wiring only) and put the
shaping in aspects (the "thin-wiring over distributed content" decision — aspects are mostly
wiring/sufficiency checks over content the engine modules already grow).
- [ ] New engine module `economy.py` (`layer="engine"`, `selectable=False` or engine-only): owns the
      currency/quantity primitive + the wiring invariant (every currency has an earner and a sink —
      lean on `state`'s use-it-or-cut-it pattern). Authors little itself.
- [ ] Content aspect `shop` (`requires=("economy", <a realization>)`): a **purchasable** is
      (cost, currency, effect) gated on affordability. Checks (demand-driven, inventory-shaped):
      every purchasable has a reachable seller; **every currency a shop takes is earnable** (else
      softlock); every vendor surface has stock. Instances = vendors, grown one-per-step.
- [ ] Content aspect `upgrades` (`requires=("economy",)`): buy-node with a cost curve + a multiplier
      effect. Distinct from `shop` (permanent multiplier vs item exchange) but same purchasable
      primitive — reuse it.
- [ ] Note in code the surface decision: shop/upgrade/world-unlock are PRESENTATIONS of the same
      `purchasable`; the selling surface is placement, not a separate mechanic. Do not mint a `shops`
      vs `upgrades` split at the primitive level — split only the check/curve.
- [ ] **`purchasable`/`currency` is a NEW component shape — the full "adding a shape" checklist
      applies (per `CLAUDE.md`), not just a module class.** All of:
  - Write tools in `src/maestro/tools.py` (`build_tools` + `TOOL_SCHEMAS`, scoped per aspect):
    `add_purchasable` / `add_vendor` / `set_currency` (whatever the slot-guarded creates need). The
    guard's `count_tool` must name a real tool. Respect the tool-granularity policy in
    `docs/tool_granularity.md` (a granular tool only if the small model can't one-shot the component).
  - IR: extend `src/maestro/ir_assemble.py` to lift the new component into the IR dict, and
    `src/maestro/ir_crossref.py` so purchasable→item/currency refs resolve (kind routing).
  - Schema: a fragment in `docs/game_ir.schema.json` (+ rationale in `docs/game_ir_decisions.md`).
  - **Projection per engine that renders it** — `renpy/projections.py` and/or `godot/projections.py`,
    keyed `(engine, aspect_id)`. A `projected` aspect with no projection for the chosen engine fails
    the compile fast (`unprojectable`). Decide: does `shop` render in renpy (VN menu) AND godot
    (walkable vendor), or only one? That choice drives `engine_for`.
- [ ] Tests: a spec picking `shop` pulls `economy` + a realization; an unearnable-currency shop emits
      exactly one fixable error; purchasables are grown one per step; the assembled IR crossrefs
      resolve; compile does not fail `unprojectable`.

### A4 — Rewrite spec authoring to nouns-primary ✅ DONE (the machinery-facing parts)
- [x] Rewrite `src/maestro/prompts/spec_write.txt`: the `MODULES` section becomes an `ASPECTS`
      section. The proposer picks aspects from `selectable_catalog()` (now aspects), still as a
      `{id: reason}` map with one-sentence justification each. Kept the "smallest set / justify each /
      leave out what you can't tie to the story" rules; updated realization + PRESENTATION prose to
      name `dialogue`/`exploration` (not `scenes`/`world`).
- [x] `spec_tools._spec_prompt_ctx` feeds `selectable_catalog()` — now surfaces aspects. `resolve_modules`
      expands `requires`, so aspect picks resolve to engine modules automatically. `module_reasons`
      keeps the proposer's per-aspect reason (engine/foundation get the auto note).
- [x] Params floors: aspect `params()` compose the same way (`_param_floors` union) — degenerate
      aspects declare none, so floors still come from the composed engine modules.
- [x] **`amend_spec` path**: aspect ids in `changes["modules"]` re-resolve via `resolve_modules`
      (aspect→engine expansion + engine re-derivation hold; `module_reasons` re-derived).
      Test: `test_amend_with_aspect_ids_reresolves`.
- [x] Tests: `propose_spec` on a "pokemon-like" request picks aspects (`dialogue`, `exploration`,
      `turn_combat`) and resolves to a buildable module+engine set (godot).
      Test: `test_propose_with_aspect_picks_resolves_to_engine_and_godot`.

### A5 — Optional decompose spec authoring (nicety, only if catalog bloat bites)
- [ ] If the aspect catalog grows large enough to distract the small model, decompose picking into an
      `add_aspect(id, reason)` chat tool call (human can block per-aspect — fits the existing freeze
      gate). Retrieve/surface only aspects relevant to the story-so-far instead of dumping the whole
      catalog. Park unless needed.

### A6 — Docs
- [ ] Update `CLAUDE.md`: the module architecture section gains the engine/aspect layering (aspects =
      LLM-facing modules that `require` engine modules + add specific checks/params; picking is
      nouns-primary). Keep the "no genre box" invariant wording.
- [ ] Update `docs/ROADMAP.md` if it tracks this.

---

# WORKSTREAM B — Scale (declared requirement space)

## Why
Demand-driven done-conditions fill to **"sufficient" and stop** — and "sufficient" is tiny (nothing
dangles in a 3-shop world either). The instrumentation guarantees **coherence**, never **magnitude**.
So "build Skyrim" → 5 maps smaller than whiterun and the loop is *satisfied* (it hit done; done was
small). **Closure has a floor, no ceiling.**

Fix: scale cannot be hoped-for emergence. It must be **declared as a requirement space** the closure
loop is forced to fill — so `get_errors` only empties AT scale. Two levers:
- **Breadth (coverage grids):** enumerate a space, demand every cell. Scale lives in the PRODUCT of
  dimensions: `vendor_type × settlement × region`. Each uncovered cell fans into a create-error.
- **Depth (recursion):** a settlement is not a leaf node — it's a space that demands its own interior
  content tree (buildings → NPCs → dialogue → local quests). Richness = depth of the demand tree
  under each cell. ("Smaller than whiterun" is a DEPTH complaint; too-few-towns is a BREADTH one.)

The existing `slot_errors` machinery is exactly the cell-filler. What's missing is the **space
declaration** — today floors are flat constants (`min_characters=4`); scale needs floors that are
**functions of the artifact** (per-settlement: N vendors; per-region: N settlements).

## Costs (be honest; bake into the design)
- **Compute is linear in scale.** N cells × one small-model call per cell = thousands of calls; a big
  space is a long build. `parallel_fixes` (settings, current default 4) is the only relief — scale is
  a parallelization problem once coherence is solved. Add a **declared scale budget / target** the
  loop fills toward with visible progress, and a runaway guard.
- **The density trap.** Coverage guarantees PRESENCE, not DISTINCTIVENESS. N cells filled by a small
  model = N interchangeable cells = map-marker slop, not Skyrim. Each cell needs a
  **distinctiveness demand** (distinct proprietor / signature stock / tied to local story), or vast
  comes out hollow — a downgrade, not the goal.

## Ordering note
Do this AFTER Workstream A lands to a mergeable point. Coverage/recursion are natural **aspects**
themselves (an `open_world` aspect that declares the multiplicative demand; a `settlements` aspect
that declares the recursive tree). Building scale as aspects reuses A's layer instead of hardcoding
scale into engine modules.

## Tasks

### B1 — New check archetypes (the space-fillers)
In `src/maestro/modules/checks.py`, add primitives that fan demand SCALED by the artifact (each
returns `slot_errors`-shaped per-cell create-errors with stable paths):
- [ ] `coverage_errors(present_keys, required_set, *, type, code, component, noun)` — one create-error
      per member of `required_set` not in `present_keys` (breadth over an enumerated dimension).
- [ ] `per_parent_errors(parents, per_parent_target, present_by_parent, ...)` — for each parent item,
      demand `per_parent_target` children; fan the shortfall per parent (multiplicative demand:
      per settlement demand a vendor set; per region demand settlements). Stable path
      `parent_id/child_slot` so per-cell progress is visible and no false stall.
- [ ] `recursive` child-demand helper — a parent of a given kind (e.g. `place.kind == "settlement"`)
      demands child content (interiors → NPCs → quests), each child itself a parent at the next level.
      Depth-bounded by declared params.
- [ ] Tests: each archetype fans the right count; completing one cell shrinks the set; distinct paths.

### B2 — Declare the requirement space in the spec
- [ ] Extend sizing beyond scalar floors: allow **coverage sets** (a list a module must cover, e.g.
      `vendor_types`) and **multiplicative sizing** (`regions`, `settlements_per_region`,
      `vendors_per_settlement`) in `params()` / `spec["params"]`. `_param_floors` already unions list
      knobs — extend `_resolve_params` if richer merge is needed.
- [ ] Update `spec_write.txt` (or, per ordering, the relevant scale-aspect's picking prose): let the
      proposer declare the space size for the story it wrote (a sprawling world raises regions /
      settlements / coverage; a small one keeps the floor). Reuse the existing SIZING mechanic.

### B3 — Apply depth: a settlement is a recursive space
- [ ] In `world` (engine) or a new `settlements` aspect: a settlement place demands a child content
      tree (interiors, NPCs, local quests) via `per_parent_errors` / recursive demand — not a leaf.
      This is the "make whiterun rich" lever.
- [ ] Wire child content through the modules that already own those shapes (NPCs via `cast`,
      dialogue via `dialogue`, quests via a `quests` aspect) — distributed, thin-wiring, not a fat
      settlement blob that re-authors places/NPCs the other modules grow.

### B4 — Apply breadth: multiplicative coverage
- [ ] A coverage aspect (e.g. `open_world`): per qualifying settlement, demand coverage of the core
      set (vendor types, service types); settlements themselves demand coverage over regions. The grid
      multiplies via `per_parent_errors` chained across levels.
- [ ] Combine with A3's `shop`: "every vendor type represented per major settlement" = `shop`'s
      coverage check reading the multiplicative sizing.

### B5 — Density (fight the presence-not-distinctiveness trap)
- [ ] Add per-cell distinctiveness demands: a generated cell must carry distinct identifying fields
      (unique proprietor, signature stock, a tie to local story/story_state) — a check that fires when
      a cell is generic/duplicative. Reuse the `distinct` pattern but on richer keys than `id`.
- [ ] Feed each cell's authoring call its LOCAL context (its region/settlement, neighboring cells,
      local story threads) so the small model writes something specific, not a template.

### B6 — Compute + runaway safety
- [ ] Verify `parallel_fixes` batches scaled create-errors correctly (a nodes-style view caps the
      batch at real open slots). Consider raising the cap for scale builds.
- [ ] Add a **declared scale budget / target** with visible progress (X/N cells filled) so a big
      build reports magnitude and the human can see it working, not a black box.
- [ ] Runaway guard: a hard ceiling on total create-errors a space can fan (log what's dropped if
      capped — per `CLAUDE.md`, no silent truncation).

### B7 — Docs
- [ ] Update `CLAUDE.md`/`docs/ROADMAP.md`: scale = declared requirement space (coverage grids +
      recursive depth) filled by the existing one-item-per-step loop; magnitude is spec-declared
      structure, not emergent; compute is the ceiling, parallelization the relief.

---

## Parked concerns (not for this pass — logged so they aren't lost)
- **Aspect selection bias on short prompts.** A terse request ("make an RPG") will almost always pick
  `open_world`/`settlements` (and every plausible aspect) — over-picking, because a short prompt
  under-constrains. This is a spec-DRAFTING quality problem (prompt + justification discipline +
  maybe a "defend the scope you chose" pass), NOT an aspect-machinery problem. Revisit after A/B land.
  The `{id: reason}` justification map already fights this; short prompts defeat it. Possible later
  lever: a scope-interview turn in chat before drafting (ties to A5).

## Coordination between workstreams (both agents read)
- **Shared files** both touch: `checks.py`, `spec_write.txt`, `spec_tools.py`, `module.py`,
  `params()`. Expect merge conflicts. Prefer running each workstream in its own git worktree.
- **Order:** land A to a mergeable point first (the engine/aspect distinction + reclassification are
  the foundation B's scale-aspects build on). B's coverage/recursion are cleanest expressed AS aspects
  (`open_world`, `settlements`), so A's layer should exist before B wires them in.
- **Tests before done** (`cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`).
  Self-review the diff for scope creep + missing tests per `CLAUDE.md`. Keep `CLAUDE.md`/`docs/ROADMAP.md`
  in sync in the same commit as the code. Do NOT commit unless explicitly asked.
