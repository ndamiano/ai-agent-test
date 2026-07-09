# World-First — bible, objectives/quests, residents, ambient dialogue

## Why

Open-world games generate awful because the content model is drama-first (VN order:
premise → cast → beats → scenes, places as backdrop). An open world's defining property is the
inverse: the world exists independent of the player's path through it. Three concrete failures
(all verified on live builds + code recon, 2026-07-09):

1. **Nobody lives anywhere.** Characters exist only because the story needs them (`cast` floors
   off the story premise); a blacksmith shop cannot have a shopkeep unless a story beat happens
   to land there, and a story-less customer NPC is unrepresentable — no module has a reason to
   author them, no dialogue form exists for them, `nodes_entered` (world.py:103) flags their
   conversation as a defect.
2. **No quests, only flags.** A "quest" today is emergent from (talk hotspot → scene → flag →
   gated hotspot). Nothing owns the state machine, so nothing can validate "giver acknowledges
   completion," render a journal, or check chain ordering. The player is never told what to do:
   `ir.goal` is used ONLY as the invisible win-gate (Game.gd:324-330); gated-verb failure says
   "Not yet." without naming the unlock; the only HUD string is the controls hint.
3. **Side content under-generates by design.** Branching is demand-driven; a thin main line
   produces zero side storylines (storyline_pivot.md risk #4, parked there — solved here: quest
   pressure comes from bible tensions, not story branches).

**Decided direction (settled with Nick 2026-07-09 — do not re-litigate):**
- Generation order for world games inverts to world-first:
  **bible → places(+residents) → cast(role-first) → objectives/quests → dialogue**.
- `objectives` = engine module (goal state machine primitive). `quests` = LLM-facing content
  aspect over it. This REPLACES economy/shop as the aspects_and_scale.md A3 proof — prove the
  aspect layer on load-bearing work.
- `story` demotes to the VN spine. World games compose `bible + objectives + quests` instead.
  Composition is the genre selector (no genre string, per CLAUDE.md invariant).
- NPCs come from EITHER a mechanical requirement OR place verisimilitude, never free-form,
  never absent — the furniture-list model applied to people (**residents**).
- Dialogue splits into two registers: **set-piece** (existing `scene_turn_loop`, the rare path)
  and **ambient matrix** (cheap per-resident × quest-state exchanges, the common path).

## Background (VERIFIED file refs from 2026-07-09 recon)

- Module system: `Module` ABC + `Check` list, `src/maestro/modules/module.py`; demand-driven
  worked example `inventory.demanded_items` (inventory.py:96-101, one create per dangling ref,
  `when_clean=True`); count-driven examples cast/story/scenes/world.
- Aspects layer (A1+A2) is IN FLIGHT on a worktree branch (tasks/aspects_and_scale.md):
  `Module.layer` engine/aspect, catalog lists aspects, aspect requires ≥1 engine module.
  THIS FILE DEPENDS ON IT — quests is authored as an aspect.
- Storyline pivot LANDED (13513ae): story = spine{theme,tone,trope} + storylines[] with
  code-guarded termini (game_end/handoff/merge), demand-driven spinoffs, `max_storylines`.
  Beat→node stamping `(storyline, beat)`; provenance stripped at ir_assemble.py:142-ish.
  KEEP ALL OF IT for VN. A live-build slot/stamp bug is being fixed in parallel (duplicate
  slot stamps + model-picked node ids colliding with beat ids).
- World module: places + interactables, `min_places=4`, `min_interactables=3`
  (world.py:729-730); talk hotspot carries `action.node`; dialogue graph entered via
  `nodes_world_entered` (world.py:103-144); spatial gates `_rpg_world_error` (world.py:278-390).
- Runtime: verbs dispatch through `Game.run_action` (Game.gd:290-339); gated-verb failures are
  bare ("Not yet." Game.gd:327); NPC-on-map rendering is a LABEL STRING MATCH against character
  names (overworld.gd:398-415) — fragile, replaced here by id binding.
- State wiring is existence-only (state.py:127-149): producer AND consumer must exist, but
  nothing checks the producer is reachable BEFORE the consumer. Quest transitions give flags
  arc identity, making the ordering check expressible (storyline_pivot.md parked item #1 —
  solved here as an objectives check).
- Scene authoring: `scene_turn_loop` (scenes.py:884-1055) — one LLM call per character turn +
  closer; `in_world` makes talks self-contained return-to-map (scenes.py:893-896).

## The model

### `bible` — engine module, the world-game root component

```json
{
  "setting": "a drought-starved river barony, iron-age tech, superstitious",
  "factions": [
    {"id": "fac_guild", "name": "Smith's Guild", "wants": "the baron's levy repealed"},
    {"id": "fac_keep",  "name": "the Baron's men", "wants": "order and the levy paid"}
  ],
  "tensions": [
    {"id": "tension_levy", "summary": "the levy is bleeding the town dry",
     "between": ["fac_guild", "fac_keep"], "scale": "main"},
    {"id": "tension_mine", "summary": "something in the flooded mine kills prospectors",
     "between": ["fac_keep"], "scale": "side"}
  ]
}
```

- Always-on for world compositions (pulled by the `exploration`/`quests` aspects' requires),
  never composed in a plain VN. Blocking-first: authored before places/cast/quests, same tier
  position spine holds in story.
- Checks: setting non-empty (blocking); tensions floor (default 3, `scale=="main"` exactly one);
  every tension's `between` resolves to declared factions; every faction/tension is CONSUMED
  downstream once quests exist (use-it-or-cut-it, state's pattern).
- Everything downstream composes a `bible_block` into its render_context (the pattern of
  `story_block`). Bible ids are citable — a resident's `stance`, a quest's `tension` — and
  crossref'd at the authoring layer (NOT the IR: bible is authoring-state like story,
  stripped/never-lifted at assemble; the IR only ever sees the flags/nodes/places it produced).
- Premise stays what it is: the frozen, human-gated pitch. Bible is build state derived from
  it. Human review of tensions rides the existing auto-pause channel, not the freeze gate.

### `objectives` — engine module, the goal state machine

One `objective` = a small state machine over the existing flag substrate:

```json
{
  "id": "obj_levy",
  "tension": "tension_levy",
  "archetype": "broker",
  "title": "The Levy",
  "steps": [
    {"id": "s1", "summary": "hear the guild's case",   "advance_flag": "levy_heard"},
    {"id": "s2", "summary": "get the ledger from the keep", "advance_flag": "ledger_taken"},
    {"id": "s3", "summary": "choose a side", "resolutions": [
       {"id": "guild", "flag": "sided_guild"}, {"id": "keep", "flag": "sided_keep"}]}
  ],
  "journal": {"offered": "…", "s1": "…", "s2": "…", "s3": "…",
              "resolved.guild": "…", "resolved.keep": "…"},
  "main": true
}
```

- **Code owns the shape** (the storyline-terminus lesson): the model fills an archetype's slots
  (fetch / escort / investigate / broker / moral-fork — a small declared library, each a step
  template); it never invents transition semantics. States compile to ordinary flags
  (`obj_levy.s1` etc. or the declared advance_flags) — the runtime needs NO new condition
  grammar, gates keep using `requires`.
- Checks (all deterministic): every step's advance_flag has a producer in reachable content;
  **producer-before-consumer along the chain** (step N's producer reachable while step N-1
  resolved — `views.shortest_path` over the node/place graph; this is the parked path-aware
  wiring check, now expressible because flags have arc identity); exactly one `main` objective;
  every resolution reachable; journal text present per state.
- Demand: each bible tension fans one objective (`when_clean` demand check, inventory-shaped);
  the `main` tension's objective is the win path — resolving it is the game_end (places.goal
  generalizes to "main objective resolved").
- IR lift: objectives ARE lifted (unlike bible) — the runtime renders the journal from them.
  `ir_assemble` + `ir_crossref` + schema fragment + both projections per the CLAUDE.md
  "adding a shape" checklist. Ren'Py projection = journal screen + objective line; Godot =
  journal panel + HUD objective + gated-verb feedback that names the missing step.

### `quests` — content aspect over objectives (the A3 proof)

`layer="aspect"`, `requires=("objectives", "world", "cast")`. Shapes an objective into the
NPC-given presentation: a **giver** (a resident id), per-state giver dialogue variants
(offer / active nudge / turn-in / per-resolution reaction), and rewards (items/flags via
existing effects). Checks: every objective has a giver who is a placed resident; offer
dialogue reachable from the giver's talk; turn-in acknowledges each resolution (the variant
exists and is gated on the resolution flag). The aspect authors the VARIANTS (via the ambient
register below); the engine module owns the machine.

### `residents` — on `world`, furniture-for-people

Per-place, error-driven build state (NOT spec-frozen), mirroring the furniture-list design:

- Blocking check per authored place: no residents list → author it (LLM derives who'd
  plausibly be there from setting + place kind: "smith, apprentice, waiting customer";
  density by place kind).
- **Mechanical tier**: a resident who hosts a real interaction (quest giver, set-piece scene,
  shop). Binds to a cast character **by id** — `{"resident": "r_smith", "character":
  "char_yorra"}` — which kills the runtime label string match (overworld.gd:398-415): the
  talk interactable carries the character id, the presenter renders that token.
- **Verisimilitude tier**: exists to make the place read inhabited; gets ambient dialogue
  (below). Ambient ≠ dead — same rule as examine-flavor on furniture.
- Demand into cast: an unbound mechanical resident fans one `add_character` job with the
  place + role as context (cast keeps its story floor for VN; this is a second demand source,
  inventory-shaped). Character card gains `role` + optional `stance` (a tension id + position).
- Every resident emits a token asset stub at creation (couples to the asset-stub redesign).

### Ambient dialogue register — on `scenes`

- A node authored `register: "ambient"`: single cheap LLM call (no turn loop), 2–4 lines,
  small-talk register, `end: return`. Exempt from `each_node_min_lines`, beat/storyline
  stamping, and the narrative-floor checks; still a real node (compile/crossref unchanged).
- State-varied: a resident may carry several ambient nodes gated on quest-state flags (the
  quests aspect's giver variants are authored through this same register). Selection at
  runtime: the talk interactable lists `[{node, requires}]` variants, first match wins —
  small IR addition to the talk action, both engines.

### Composition

- World game: `exploration` (aspect) → world + bible + objectives; `quests` aspect; cast,
  scenes, state, assets, human as today; combat optional.
- VN: `narrative`/`dialogue` aspects → story + scenes + cast, exactly as after the pivot.
  `story` and `objectives` never compose together (nothing forbids it structurally, but no
  aspect requires both; revisit if a hybrid wants it).
- Kill `scenes.in_world` special-casing where superseded: a world talk is a quest variant or
  an ambient node by construction, not a flattened beat-chain.

## Guardrails

- **REJECT the model choosing transition semantics.** Archetypes + code-guarded steps only —
  the storyline-terminus rule generalized.
- **REJECT bible in the IR.** Authoring-state only, like story. If ir_crossref needs bible
  ids, you've leaked the layer.
- **REJECT a story↔objectives dependency.** story is VN-only; objectives is world-only-by-
  composition. The unification already happened at the node/flag layer.
- **REJECT free-form NPC authoring.** A character exists via story floor (VN) or resident
  demand (world). No third path.
- **REJECT prompt-only ordering.** Producer-before-consumer along quest chains is a
  deterministic check, not an instruction.
- Small-model rules apply throughout: one item per step (one tension, one resident, one
  objective step, one ambient node), skeleton-first prompts, crafted context blocks.

## Tasks

### W1 — bible module
- [x] `src/maestro/modules/bible.py`: component + validators + checks (blocking setting,
      tension floor/scale, faction refs), `set_bible` slice tools in tools.py, `bible_block`
      presentation, params (`min_tensions: 3`). Register; NO projection (authoring-only).
- [x] Prompts: `bible_write.txt` (setting+factions), `bible_tension_add.txt` (one per step).
- [x] Tests: check fan-out, tension/faction ref validation, block renders.

W1 landed notes (2026-07-09):
  - Slice tools: `set_bible(setting, factions?)`, `add_faction(faction_id, content)`,
    `add_tension(tension_id, content)` — each validates at write time against `v_faction` /
    `v_tension`; `add_tension` refuses a SECOND `scale=="main"` (exactly-one enforced at the
    write boundary, at-least-one by the `one_main_tension` check). Guard `_TENSION_GUARD`
    (cap=1) makes tensions sequential like story beats — each sees the priors, main first.
  - Checks: `setting` (blocking) → `tension_floor` (slot_errors, BUILD) → `one_main_tension`
    (FIX, suppressed until tensions exist) → `faction_refs` (FIX, demand-driven, one add_faction
    job per dangling id). `v_bible` is the DONE-shape schemas-validator (exactly one main + refs
    resolve) gating a full write_component overwrite.
  - **DEFERRED — downstream consumption check.** The design's "every faction/tension is CONSUMED
    downstream once quests exist (use-it-or-cut-it)" check is NOT added: quests/objectives don't
    exist yet, so there is nothing to consume a tension. Add it with W2/W3 (objectives fans one
    objective per tension → an unconsumed tension is then detectable), mirroring state's
    producer-AND-consumer pattern.
  - Built on the pre-aspects branch: `Bible.layer="engine"` is set on the class (harmless until
    the aspects layer lands); `selectable=False` keeps it out of the catalog today, and it will
    be pulled in by the `exploration`/`quests` aspects' `requires` at W3/W4. Nothing requires it
    yet — `compose(["bible"])` still resolves cleanly and needs no engine projection.

### W2 — objectives engine module + IR
- [ ] `src/maestro/modules/objectives.py`: archetype library (step templates), demand-from-
      tensions check, per-step producer/ordering checks (reuse `views.shortest_path`),
      journal-completeness, exactly-one-main.
- [ ] Tools: `add_objective(archetype, ...)` + `edit_objective_step`; slot-guarded, code fills
      ids. IR: `ir_assemble` lift + `ir_crossref` (flag refs) + `docs/game_ir.schema.json`
      fragment + `game_ir_decisions.md` rationale.
- [ ] Projections: godot journal panel + HUD objective line + named gated-verb feedback;
      renpy journal screen (VN-side render optional if objectives never composes with renpy —
      decide via engine_for; combat precedent says godot-only is fine and auto-routes).
- [ ] Tests: state machine validation, ordering check catches producer-after-gate, IR round-trip.

### W3 — quests aspect (A3 proof)
- [ ] `quests` aspect module: giver binding, variant checks, rewards. Catalog description.
- [ ] Tests: aspect resolves engine set; giver-less objective emits; turn-in variant gated.

### W4 — residents on world
- [ ] Residents list check chain (blocking author → tiers → cast demand → token stubs);
      talk-variant IR addition (`[{node, requires}]`); presenter id-binding replaces the
      string match (overworld.gd + overworld3d.gd).
- [ ] Tests: resident demand fans, cast binding, variant selection, presenter binding.

### W5 — ambient register on scenes
- [ ] `register` field on nodes; ambient author path (single call, no turn loop); floor/stamp
      exemptions; quests' variants authored through it.
- [ ] Tests: ambient node exempt from min_lines/beat stamps; still compiles + crossrefs.

### W6 — runtime quest surfacing
- [ ] Godot: journal UI (Esc menu tab or J), HUD current-objective line, gated-verb feedback
      names the missing requirement (data already in `requires`).
- [ ] Playable check: a world build where the player can always answer "what do I do next."

### W7 — gold game (runs FIRST, in parallel with W1)
- [ ] Hand-author one gold open-world game directly in IR + a hand-written objectives
      component: ~5 places, ~8 NPCs (mechanical + ambient), 3 quests off one tension web.
      Playtest until fun; it defines the runtime contract W6 builds and becomes the world-game
      eval gold (the One Last LAN of world games). Home: docs/examples/world_game.json.

## Open questions / risks
1. **Archetype library size** — start with 5; too few makes samey quests, too many confuses a
   small model. Revisit after the first generated batch.
2. **Ambient volume** — residents × states multiplies nodes; density caps + the ambient
   register's cheapness keep it bounded, but watch build time (parallel_fixes helps).
3. **Does the main quest need dramatic beats on top?** Lean NO (set-piece scenes at quest
   transitions suffice); revisit if generated main quests feel flat.
4. **story_state continuity for ambient nodes** — ambient calls skip the closer/delta path;
   they read the bible + quest state instead. Verify no continuity regression.

## Parked
- Scale/coverage demands (aspects_and_scale.md Workstream B) — residents + per-tension quests
  ARE the first per-parent demands; the general `per_parent_errors` primitive lands with B.
- Map/rasterizer redesign (asset_quality.md T3 + map-gen memory) — orthogonal; a coherent
  inhabited world on ugly maps beats pretty empty maps.
- Faction reputation as a variable (`factions` aspect) — after quests prove out.
