# Module / Aspect Catalog — Build Out the Mechanic Library

## Why
The platform can only make what its composed modules/aspects can express. Today the catalog is thin
(dialogue, story, world, combat, inventory, wild_encounters + foundation). "Make me a game" is only
as broad as the mechanic library. This workstream is the **ongoing build-out** of that library —
each new mechanic authored as a module/aspect following the established pattern.

**Not covered by `aspects_and_scale.md`.** That file builds the engine/aspect *layer* + the first
proof (economy → shop/upgrades) and reclassifies the existing modules. THIS file is the breadth:
the growing list of new mechanics to author on top of that layer. Distinct concern, so it's its own
file.

## Guardrails (inherit from `aspects_and_scale.md`)
- **Every aspect requires ≥1 engine module; it's a composable adjective, not a genre box.** A single
  aspect that flips 5 modules is the preset-box reborn — reject it. Compose small aspects instead.
- **Reusable across unrelated games** — `affinity` works outside a dating sim; `shop` outside an RPG.
- **Follow the CLAUDE.md "Adding a mechanic-module" recipe exactly** — subclass `Module`, `checks`
  list (detect→fix pairs), `params()` floors, catalog attrs, register, register a projection per
  engine, extend `ir_assemble`/`ir_crossref` + schema for any NEW component shape. No core/loop edit.
- **Quality-gate each addition:** a new mechanic that regresses existing genres isn't done. Run the
  genre battery (per `generalization_mandate`) + the judge (`quality_backlog.md` §1) before shipping.
- **New component shape = the full "adding a shape" checklist** (tools + IR lift + crossref + schema
  + projection), per CLAUDE.md — not just a Module class.

## Background
- The layer this builds on: `aspects_and_scale.md` Workstream A (engine/aspect distinction,
  `layer="aspect"`, `requires` → engine module). **A must land first** — new mechanics should be
  authored as aspects, not raw modules, or they'll need reclassifying later.
- Worked examples to copy: `cast`/`story`/`scenes`/`world`/`combat` (slot-guarded count content),
  `inventory` (demand-driven), `state`/`human` (`build_prompt` escape hatch, author nothing).
- The recipe + registry live in `src/maestro/modules/module.py`; checks primitives in `checks.py`.

## The catalog (candidates — prioritize with owner before building)
Each row becomes its own sub-task using the per-module checklist below. Not a commitment to build
all — a menu to prioritize. (Owner's original ask included a **card game with ante + buy/sell
shops + world exploration** — that seeds the top of the list.)

| Candidate | Kind | Engine module(s) it needs | Notes |
|-----------|------|---------------------------|-------|
| `card_battler` / deckbuilder | engine (new substrate-ish) + aspects | new `cards` engine module | Ante mechanic (win/lose cards), deck/hand/draw. The owner's card-game request. Big — new component shape. |
| `shop` / `trade` | content aspect | `economy` | buy/sell surface; from `aspects_and_scale.md` A3 — build there first, extend here. |
| `upgrades` | content aspect | `economy` | cost-curve + multiplier; A3. |
| `affinity` / relationships | modifier + content aspect | `cast` (+ `dialogue`) | dating/companion mechanics; reusable in any character game. |
| `quests` / objectives | content aspect | a realization module | trackable goals w/ completion + rewards; feeds scale (`aspects_and_scale.md` B). |
| `crafting` / recipes | content aspect | `inventory` + `economy` | item→item transforms; demand-driven like inventory. |
| `needs` / survival | modifier aspect | `state` | hunger/energy meters (source+sink wiring). |
| `stealth` | content/modifier aspect | `world` (+ `combat`?) | detection states on the walkable grid. |
| `puzzle` (beyond PnC) | content aspect | `world`/`scenes` | gated multi-step chains (ties to `quality_backlog.md` §6). |
| `dungeon` / roguelike | content aspect | `world` + `combat` | procedural rooms + encounter tables (leans on existing wild_encounters + map_builder). |
| `dialogue_tree` (deep) | aspect | `scenes`/`dialogue` | branching conversation w/ affinity/flag gates. |
| `factions` / reputation | modifier aspect | `state` (+ `cast`) | standing variables that gate content. |

Add rows as ideas land — this table is the living backlog.

## Per-module checklist (copy for each one you build)
- [ ] Decide engine module vs aspect (aspect if LLM-facing + concrete). Name it; write its one-line
      catalog `description` + `requires`.
- [ ] `checks` list: each error kind = a `Check` (detect → fix: prompt/tools/skeleton/guard). Count
      target → `slot_errors` fan-out + guard; demand-driven → dangling-ref fan-out.
- [ ] `params()` floors for sizing knobs.
- [ ] New component shape? → write tools (`build_tools` + `TOOL_SCHEMAS`), extend `ir_assemble` +
      `ir_crossref`, add a `docs/game_ir.schema.json` fragment (+ rationale in `game_ir_decisions.md`).
- [ ] Register the module + a projection per engine that renders it (`renpy`/`godot` projections).
- [ ] Add it to `spec_write.txt`'s catalog surface (auto via `selectable_catalog()` — verify).
- [ ] **Tests:** picking it resolves to the right engine+projection; its checks fan/fix correctly;
      IR crossrefs resolve; compile doesn't `unprojectable`.
- [ ] **Quality:** genre battery + judge — no regression; the mechanic produces a *good* game, not
      just a valid one.

## Ordering
1. `aspects_and_scale.md` Workstream A (the layer) — prerequisite.
2. `economy` + `shop`/`upgrades` (already in A3) — the first real aspect proof.
3. Then prioritize the table with the owner and build one mechanic at a time (each fully done +
   quality-gated before the next — breadth is worthless if each addition is shallow).

## Parked
- `card_battler` may warrant its own file if it grows into a new substrate (its own execution model),
  not just an aspect. Revisit when scoped.
- Prioritization order of the table — owner's call.
