# Storyline pivot: from one branching beat-sheet to a graph of linear storylines

## Why

The current `story` module encodes a game's plot as ONE monolithic thing: a required
`central_question` (a values-fork), a single ordered `beats[]` sheet with the crisis forced
last, and a set of `endings[]` each paired with an `ending_paths[].earned_by` prose string
naming the dialogue menu-choice that earns it. This is overfit to a branching visual novel,
and it produces two chronic failures:

1. **VN-overfit.** Most good stories have no single values-fork. Foundation, Hocus Pocus,
   Zombieland, Harry Potter — a theme and a tone carry them, not a central question. Forcing
   one, plus a back-loaded crisis and ≥3 distinct endings, makes the model manufacture
   fake forks, duplicate endings, and flat resolution tails (the whole branching tree has to
   stay coherent inside one beat-sheet).
2. **Open-world gap.** In a world game `scenes._world_end` (`scenes.py:880-891`) flattens the
   linear beat-sheet into a bag of unordered return-to-map conversations, and the only
   dramatic ending path is a single climactic menu whose ≥2 choices all target `story.endings`
   ids. A single-ending open world is structurally unreachable; a main-quest-plus-side-quests
   shape is unrepresentable.

**Decided direction (settled — do NOT re-litigate whether to do this; ground it, detail it,
surface risks):**

- Replace the monolith with a **graph of linear storylines**: one start storyline; branch
  points spin off new, also-linear storylines. Convergence is FREE at the node level (many
  nodes → one target); "storyline" is a **planning/provenance layer over the existing node
  graph + state**, NOT a runtime construct. It compiles away exactly like the per-node `beat`
  stamp does today.
- Beats STAY the linear unit; a storyline groups beats. A storyline is NOT responsible for
  what it branched off to — that kills the "keep a whole branching tree coherent in one
  beat-sheet" burden.
- Branching is **demand-driven** (mirror `inventory`): a branch point spins off a storyline;
  the model authors ONE LINEAR storyline at a time and never plans the whole graph.
- Terminations are **code-guarded**: each storyline terminus is game-ending / hands-back /
  merges; the branch point ENCODES the return; code — not the model — sets the terminus type.
- Per-storyline sizing is **variable** (a 3-beat side quest vs a 15-beat main line): a floor
  is a hard minimum, and a **finished-tool** lets the author declare a storyline done at its
  natural length.
- The spine is **THEME + TONE (+ optional trope)**, NOT a central question. `central_question`
  as a required metric is dropped.
- **Endings = terminal storylines.** Multiplicity is not required (a single-ending open world
  is fine). Distinctness matters only IF there are several (the set can't collapse to one
  identical outcome; clusters of similar endings — four "you died" — are fine).
- This **unifies** VN and open-world: theme+tone spine + storylines (no forced fork, no forced
  branching) is exactly what an open world needs (a main line to the end + side-quest
  storylines that connect back via state). Design them as ONE thing.

## Background (VERIFIED — real file:line paths so an agent can start cold)

### The `story` component today
- Authored as one on-disk JSON component (`component = "story"`). Structural gate `v_story`
  at `src/maestro/modules/story.py:80-102`, wired `schemas = {"story": v_story}` (`story.py:182`).
- Fields: `central_question` (required non-empty string, `story.py:81-82`, written by
  `set_central_question`, `tools.py:516-527`); `beats[]` (each a `{id}` + `_BEAT_FIELDS =
  ("summary","type","purpose","tension")`, validator `v_beat_one` `story.py:63-77`, appended by
  `add_beat` `tools.py:529-549`); `endings[]` (`{id, description}`, `add_ending`
  `tools.py:551-569`); `ending_paths[]` (`{ending, earned_by}`, written paired with the ending
  in the SAME `add_ending` call, `tools.py:567-568`).
- `story_state_schema` is NOT in the component — it is spec-level (`spec.py:63-64`), authored in
  `spec_write.txt`, seeds the runtime bible via `init_story_state` (`story_state.py:26-34`).
- `story_state.py` (continuity bible: `established_facts`/`entity_states`/`open_threads`/
  `recent_events_tail`, merged per-node via `apply_delta`, `story_state.py:43-84`) is a separate
  runtime object carried by the scene loop, NOT the dramatic plan.

### Count floors + per-slot fan-out
- Floors declared in `Story.params()` (`story.py:220-226`): `min_beats: 5`, `min_endings: 3`
  (also `min_characters`, `min_branches`, `each_node_min_lines`, `character_fields` — raised on
  neighbours via param union).
- `_d_min_beats` (`story.py:158-161`) / `_d_min_endings` (`story.py:164-167`): `need = floor -
  have`, then `checks.slot_errors(need, ...)`.
- `checks.slot_errors` (`checks.py:36-44`) emits N Errors with distinct `path=f"#{k:03d}"` →
  stable identity, so authoring one item shrinks the set (visible progress, no false stall).
- Guards: `_BEAT_GUARD`/`_ENDING_GUARD` (`story.py:152-155`), each with `cap: _one` (`_one`
  returns 1, `story.py:138-141`) → strictly sequential authoring (each beat sees the full prior
  arc). Contrast `cast` (parallel) and `scenes` (`cap=_parallel_cap`, `scenes.py:346`).
- The cap is consumed by the loop batcher (`agent_loop.py:164-172`); the guard body
  `_create_guard` (`services.py:129-153`) enforces no-overwrite + assigned-slot ordering.
- Remaining story checks are cheap post-authoring repairs: `beat_fields`/`ending_path_fields`
  (each_has), `distinct_beats`/`distinct_endings` (dup-id, `job="fix"`), `endings_planned`
  (`refs_resolve` every ending has a path), and `central_question` (`blocking=True`,
  `story.py:193-195`). Ordering: central_question (blocking) → beats → endings → repairs.

### `render_context` (`story.py:228-242`)
Composes premise + `cast.character_cards` + `CENTRAL QUESTION:` line + `beats_detail_block`
(`story.py:43-51`, full summaries) + `endings_detail_block` (`story.py:54-60`) + tail.
`story_block` (`story.py:19-32`, id-only) is what OTHER modules consume. `self_digest`
(`story.py:244-254`) is the repair view.

### The node graph (`scenes.py`, `views.py`) — where storylines actually live
- A node = `{lines, end, location, beat}`; graph = `nodes.node_ids` (order, `[0]` = entry) +
  `nodes.nodes` (id→node). `_END_TYPES = {"jump","menu","return","end"}` (`scenes.py:21`).
- Edge extraction is centralized in `views.node_targets` (`views.py:15-21`): `jump`→
  `[target]`, `menu`→`[choice.target ...]`, `return`→`[]`, `end`→`[]`. Convergence is
  first-class: `views.references` (`views.py:37-49`) emits every `(source,target,label)` edge;
  the slot builder aggregates parents per target (`scenes.py:259-262`) and switches to a
  convergence brief for multi-parent slots (`_render_slot_focus`, `scenes.py:613-616`).
- End write-policy `end_error` (`scenes.py:176-206`): every menu choice needs a `target`; menu
  capped at `_MAX_MENU_CHOICES = 3` (`scenes.py:97`); a fully-gated menu is rejected (one
  ungated fallback required); a ≥2-choice menu with <2 distinct targets is a rejected
  "fake choice".
- Reachability: `reachable_from_start` (`scenes.py:350-370`, roots = entry + every talk-hotspot
  target `scenes.py:361-366`), `views.reachable` BFS (`views.py:24-34`). Target resolution:
  `node_targets_resolve` (`scenes.py:373-381`).
- Ending termination — VALIDATED: `_d_endings_are_nodes` (`scenes.py:489-493`), `_d_ending_nodes_end`
  (`scenes.py:506-519`), `_d_premature_endings` (`scenes.py:522-552`, uses `views.shortest_path`
  `views.py:52-71` + per-node `beat` index). FORCED (deterministic, no LLM): `_force_ending_end`
  (`scenes.py:496-503`) dispatches `edit_node(nid, end={"type":"end"})` — the docstring records
  the model "re-asserted the same jump 250 steps straight," so this is a hard code override.
- Backward-jump rejection lives in the closer `scene_turn_loop` (`_backward` `scenes.py:1076-1080`,
  `fallback` always forward `scenes.py:1082-1097`).
- Beat→node realization: node carries a `beat` stamp; coverage `unrealized_beats`
  (`scenes.py:417-425`) + `_d_beats_realized` (`scenes.py:468-474`) fan one scene-create per
  uncovered beat; slot→beat assignment `node_view` (`scenes.py:247-293`); ordering `pick_slot`
  (`scenes.py:308-317`, by story beat order); stamp on write `beat_for_new_node`/`_stamp_beat`
  (`scenes.py:320-335`) via `_NODE_GUARD.prepare` (`scenes.py:345-346`). The scene text itself
  is written by `scene_turn_loop` (`scenes.py:894-1119`).

### State / flags — the cross-storyline callback substrate (already runtime-enforced)
- Global flag namespace. Set: effect `{"set_flag": id}` on a node line, a menu choice, or a
  place action outcome. Gate: `requires: {flag|var|item|not|all|any}` on a menu choice
  (`schema:1219`), a move/win action, use-clauses.
- Runtime enforcement both engines: Ren'Py `ir_vn.py:58-61,87-88`; Godot `ir.gd:24-63`,
  `vn.gd:41-45` (filters `menu_pick` by `IRCore.eval_cond`).
- Invariants: `state.state_wiring` (`state.py:127`) — every flag/var/item needs a producer AND
  a consumer (use-it-or-cut-it; bare declaration cut deterministically `state.py:185`).
  `scenes.no_dead_gates` (`scenes.py:428-449`) — a gate flag must be raised in a node ≠ the
  gating node.
- MISSING (flagged, not solved by this pivot): the wiring is existence-checked, not
  path/ordering-checked. A flag set only after the gate, or only in an unreachable branch,
  satisfies both checks yet never opens. `views.reachable`/`shortest_path` exist but are not
  applied to link producer→consumer. There is no arc identity for flags today.

### World coupling
- `World` authors `places`; places connect only via `move` actions; `place_view` builds the
  move-graph (`world.py:38-55`). `places.goal` is a single optional `{type:"flag"|"room", id}`
  (`world.py:443-445`) → one generic `"escaped"` ending (`Game.gd:324-330`). world→scenes via
  `talk` hotspots (`nodes_world_entered`, `world.py:103-144`). `scenes._world_end`
  (`scenes.py:880-891`) collapses every conversation to `return` except the climactic
  all-endings menu. story has ZERO awareness of places (`story.py:228-242`).

### Round-trip: assemble / crossref
- `assemble_ir` NEVER reads the `story` component (`ir_assemble.py:126-130` reads
  characters/asset_manifest/nodes/places/items/combat/brief/spec only). It strips authoring
  provenance from nodes at `ir_assemble.py:142` (currently `beat`). `ir_crossref` walks the
  assembled IR (`ir_crossref.py:142-154`) — branch/handoff/merge are already `menu`/`jump`/`end`
  edges it validates. **Consequence: the entire storyline layer is authoring-only data,
  invisible to assembly except one provenance strip; `ir_crossref` needs ZERO changes.**

### The loop's count / demand / finished seams
- Count floor → per-slot creates: `checks.slot_errors` (`checks.py:36-44`), driven one-per-step
  by `_batch`/`_run_fixes` (`agent_loop.py:156-196`).
- Demand-driven (inventory): NO floor, NO param knob. `demanded_items` (`inventory.py:96-101`)
  = referenced − declared; `_d_demanded_items` (`inventory.py:149-155`) emits one error per
  referenced-but-undeclared id keyed on the REAL id (`path=iid`), `when_clean=True`
  (`inventory.py:189`).
- Finished-tool: there is NO author-declares-done path today (`agent_loop.py:7-8,217-219`);
  only human waiver + park exist. The loop map identifies the single seam: a durable
  per-target marker read by the count check's `detect`, short-circuiting to `[]` once
  `have >= floor AND done`. No loop/services/guard change (the loop only reads `get_errors`).
  The note at `agent_loop.py:29-32` already anticipates authoring that spawns downstream
  demand (a changing error SET, not size → no false park).

## The storyline model

A storyline is a **planning/provenance layer over the existing node graph**. At runtime it
does not exist. The mapping (each row already supported in code):

| storyline concept | node-graph realization | supported by |
|---|---|---|
| a linear storyline | a linear run of nodes (jump→jump→…) | `views.node_targets` |
| a branch point | a `menu` end whose choices target other storylines' starts | `_END_TYPES` menu, `end_error` cap 3 |
| optional state gate on a branch | `requires` on that menu choice | `ir_crossref.check_condition`, `ir_vn.py:87`, `vn.gd:41` |
| convergence / merge | many nodes → one `target` | `scenes.py:259-262`, `_render_slot_focus` |
| game_end terminus | last node `end.type=="end"` | `_force_ending_end` `scenes.py:496` |
| handoff terminus | last node jumps to encoded return node Y (`return` in world) | closer / `_world_end` |

### The new `story` component

```json
{
  "spine": {
    "theme": "loyalty tested by scarcity",
    "tone":  "wry and warm, occasionally bleak",
    "trope": "heist"
  },
  "start_storyline": "sl_main",
  "storylines": [
    {
      "id": "sl_main",
      "kind": "main",
      "premise": "the crew plans the vault job and it goes sideways",
      "theme": null, "tone": null,
      "target_beats": 8,
      "beats": [
        {"id": "beat_01", "summary": "…", "type": "plot",     "purpose": "setup",      "tension": "none"},
        {"id": "beat_03", "summary": "the alarm trips — split up or hold", "type": "plot", "purpose": "crisis", "tension": "capture"}
      ],
      "branches": [
        {"id": "br_split", "from_beat": "beat_03", "choice": "split up and run",
         "spinoff": "sl_solo_escape", "return_to_beat": null,        "requires": null},
        {"id": "br_cover", "from_beat": "beat_03", "choice": "hold together and cover Vale",
         "spinoff": "sl_cover_vale",  "return_to_beat": "beat_04",    "requires": {"flag": "trusts_vale"}}
      ],
      "terminus": {"type": "game_end",
        "ending": {"id": "ending_caught", "description": "the crew is taken at the door as the sirens close in"}},
      "done": true
    },
    {
      "id": "sl_cover_vale", "kind": "side",
      "premise": "buy Vale time in the stairwell",
      "target_beats": 2,
      "beats": [ {"id": "beat_01", "summary": "…", "type": "friction", "purpose": "escalation", "tension": "outnumbered"} ],
      "branches": [],
      "terminus": {"type": "handoff"},
      "done": true
    }
  ]
}
```

Field spec:
- **`spine`** replaces `central_question`. `{theme, tone, trope?}`; `theme`+`tone` required
  non-empty strings; `trope` optional (free string / null). No values-fork.
- **`start_storyline`** — id of the single entry storyline.
- **`storylines[]`** non-empty. Each: `id`, `kind ∈ {main, side}`, `premise`, optional
  per-line `theme`/`tone` overrides (null = inherit spine), `target_beats` (author-chosen,
  ≥ floor — the variable-length knob), `beats[]` (SAME shape as today, `v_beat_one` verbatim;
  ids unique within the storyline), `branches[]`, exactly one `terminus`, `done` (finished-tool
  flag).

### Terminus types (three, code-guarded)
1. **`game_end`** — terminal; these ARE the endings (`endings[]`/`ending_paths[]` deleted).
   Carries `ending:{id, description}` (`description` is the concrete final scene, the old
   `SKEL_ENDING_ONE` contract). Node lift: last-beat node forced `end.type=="end"` via
   `_force_ending_end` (planned set = every game_end `ending.id`).
2. **`handoff`** — hands control back to the branching storyline. Carries NO return of its own
   — the return is **encoded at the branch point** (`branch.return_to_beat`), because a small
   model reliably gets "end vs hand back" wrong if asked to remember it at the terminus. Node
   lift: last node becomes a `jump` to the node realizing `<source>/<return_to_beat>` (or
   `{"type":"return"}` in a world game).
3. **`merge`** — flows into another storyline mid-stream. Carries `{into, at_beat}`. Node lift:
   last node jumps to the node realizing `into/at_beat` — free convergence.

### Branch points + encoded return + state gate
A branch is authored **on the source storyline** and is the demand signal that spins off a new
storyline (mirror inventory). Entry: `{id, from_beat, choice, spinoff, return_to_beat, requires?}`.
`return_to_beat` null ⇒ spinoff is terminal; non-null ⇒ spinoff's `handoff` terminus resumes
this storyline at that beat's node. `requires` is the full condition grammar — this is where
cross-storyline state gating lands (a branch that only appears once storyline B set a flag).
Node lift of a branch: the `from_beat` node becomes a `menu`; one choice continues the source
line, each branch adds a choice with `target` = spinoff start + `requires` = the gate. The
existing `end_error` guards apply unchanged.

The old `ending_paths[].earned_by` (free prose naming one menu choice) is **gone**. An ending
is now earned structurally by the path of branches (each with its optional `requires`) reaching
the `game_end` storyline — exactly what the world-story-gap needs (ending earned by accumulated
state, not one prose-named fork).

### Node stamping: `beat` → `(storyline, beat)`
A node carries `beat` + `storyline` (beat ids unique only within a storyline). Everything that
reads `beat` gets the storyline qualifier: `node_view` beat math (`scenes.py:263-281`),
`pick_slot` ordering (`scenes.py:308-317`, order by (storyline spawn order, beat-index)),
`_stamp_beat`→`_stamp_storyline` (`scenes.py:333-335`), `unrealized_beats`/`_d_beats_realized`
(fan one scene per uncovered `(storyline, beat)`). Both stamps are stripped at
`ir_assemble.py:142`.

### Completion invariant (the new done-condition)
1. `start_storyline` is declared. 2. Every storyline reachable from it via `branches.spinoff`
/ `merge.into` (no orphans). 3. Every storyline has a valid terminus; every `handoff` line is
the spinoff of exactly one branch whose `return_to_beat` is non-null; every `merge.into/at_beat`
resolves. 4. Every `branch.spinoff` resolves to a declared storyline (demand-driven closes
this). 5. **≥1 reachable `game_end` terminus.** 6. If ≥2 game_end endings, they are distinct.

## Guardrails (reject-if rules keeping scope honest)

- **REJECT any change to `ir_crossref.py`.** By construction it cannot see storylines; every
  branch/handoff/merge is already an edge it validates. If you're editing it, you've modeled a
  runtime construct instead of a provenance layer — stop.
- **REJECT a new IR field for storylines.** `spine`/`storylines`/`branches`/`terminus`/`done`
  are authoring-only, discarded at `ir_assemble.py:142` alongside the stamps. `assemble_ir`
  must still never read the `story` component.
- **REJECT the model picking a terminus type.** Code sets it from `kind`/`return_to_beat`. The
  model authors linear beats + declares branches; it never decides "end vs hand back."
- **REJECT keeping `central_question`, `endings[]`, `ending_paths[]`, `add_ending`,
  `min_endings`, or the old `_world_end` menu-sniffing.** This is a full replacement — no
  backwards-compat shims (per CLAUDE.md: delete the old way, move the call sites).
- **REJECT a hard distinctness gate.** Multiplicity is not required; a single-ending game is
  valid. Distinctness fails ONLY if ≥2 game_end endings collapse to one identical outcome.
- **REJECT lowering the beat floor.** The finished-tool can only push a count UP (author may
  raise, never lower — same rule `spec_tools` enforces for params). A `done` line under the
  floor still emits beats.
- **REJECT a `story`↔`places` data dependency.** Story stays place-blind; the branch/spawn
  point lives at the scene/world layer (a menu choice / hotspot), which already sees places.
- **REJECT unbounded storyline spawning.** A storyline can spin a storyline (unlike items); a
  `max_storylines` (and/or spin-depth) cap is mandatory or a small model runs away.

## Tasks

### T1 — Story component schema + validators
- [ ] In `story.py:80-102` REPLACE `v_story`: validate `spine{theme,tone,trope?}` +
      `start_storyline` + `storylines[]` (each: `kind`, non-empty `beats` via `v_beat_one`,
      `branches[]`, exactly one valid `terminus`, `target_beats`, `done`). Add `v_terminus`,
      `v_branch`, `v_storyline` helpers.
- [ ] KEEP `v_beat_one` + `_BEAT_FIELDS` + `SKEL_BEAT_ONE` (`story.py:63-77,105-117`) verbatim.
- [ ] Add a `v_storylines` structural gate: exactly one `kind=="main"`, ≥1 `game_end` terminus.
- [ ] Update `story_view` (`story.py:128-135`) to expose `storyline_ids` + a per-storyline
      `beat_ids` map for the guards.
- [ ] Verify: `cd src && python -m pytest ../tests/test_core.py -q` plus a new
      `test_v_story_storylines` asserting a valid graph passes and each malformation
      (no main, no game_end, dup beat within a storyline, missing terminus) is rejected.

### T2 — Tools (spine, per-storyline beats, storyline/branch authoring, finished-tool)
- [ ] `tools.py:516-527` REPLACE `set_central_question` → `set_spine(theme, tone, trope=None)`.
- [ ] `tools.py:529-549` UPDATE `add_beat` → `add_beat(storyline_id, beat_id, content)`
      (append into that storyline's `beats`; refuse dup id WITHIN the storyline).
- [ ] `tools.py:551-569` DELETE `add_ending`. ADD `add_storyline(storyline_id, content)`
      (`content = {kind, premise, terminus, target_beats}`), `add_branch(storyline_id, branch)`,
      and `finish_storyline(storyline_id)` (sets `done=True`, durable on the component — no new
      state store). Ensure none appear in `TOOL_SCHEMAS` with a human-only `force`.
- [ ] Verify: unit-test each tool round-trips onto the component and refuses dups; `add_branch`
      copies `return_to_beat` onto the spinoff's terminus record so the terminus guard reads it
      without re-walking.

### T3 — Theme+tone spine replaces central_question
- [ ] `story.py:193-195` REPLACE the `central_question` blocking check with a `spine` blocking
      check (same tier position — spine exists before any storyline).
- [ ] `story.py:220-226` REPLACE `params()`: drop `min_endings`; `min_beats: 5` →
      `min_beats_floor: 3` (per-storyline). Keep `min_characters`/`each_node_min_lines`/
      `character_fields`. Add `max_storylines` (default e.g. 8).
- [ ] Prompts: `story_question.txt` → `story_spine.txt`; retire `story_write.txt` (the story is
      no longer authored whole). Thread `tone` into `render_beat` briefs (`story.py:35-40`) and
      the scene closer (`scenes.py:921-927`). `spec_write.txt` drops the central-question
      framing; sizing block: `min_beats`/`min_endings` → per-storyline `target_beats` +
      `min_beats_floor`.
- [ ] Verify: build a run (`cd src && python -m maestro.run "<request>"`) reaches a frozen spec
      with a `spine` and no `central_question`; grep the frozen spec + story component.

### T4 — Demand-driven branching generation (mirror inventory)
- [ ] Add `demanded_storylines(artifact) = {b.spinoff for every branch} − {declared ids}` on
      the `story` module, mirroring `inventory.demanded_items` (`inventory.py:96-101`).
- [ ] Add check `_d_demanded_storylines` (mirror `_d_demanded_items` `inventory.py:149-155`):
      one `add_storyline` job per undeclared spinoff, keyed on the REAL spinoff id
      (`path=sl_id`), `when_clean=True`. Guard `_STORYLINE_GUARD` (count_tool `add_storyline`,
      cap=1, id from the demand like `_CAST_GUARD`).
- [ ] Bootstrap the start storyline like scenes' first node: a `start_storyline` check emits one
      `add_storyline` for the `kind:"main"` line when `storylines` is empty.
- [ ] Enforce `max_storylines`: once hit, the spinning site rejects new spinoffs (choices must
      `merge`/`handback` to existing lines).
- [ ] `render_context` for `add_storyline` composes the branch point that spun it (source beat +
      choice + `return_to_beat`) + spine + storylines-so-far index — mirror `inventory._item_usage_block`.
- [ ] Prompts: new `story_storyline_add.txt` (author ONE linear storyline from spine + branch
      point + storylines-so-far); `story_beats_add.txt` context scoped to the target storyline.
- [ ] Verify: unit-test that declaring a branch with an undeclared spinoff makes
      `get_errors` emit exactly one `add_storyline` job keyed on that id, and that a run of
      `add_storyline` calls drains it without false-parking (spin-then-drain across steps).

### T5 — Per-storyline beat floor + finished-tool coupling
- [ ] `story.py:158-161` REPLACE `_d_min_beats` with a per-storyline loop: for each storyline,
      `floor = ctx.param("min_beats_floor", 3)`; `if sl.done and have >= floor: continue`;
      `target = max(floor, sl.target_beats or floor)`; `checks.slot_errors(target - have, ...,
      component=f"story/{sl.id}")`. DELETE `_d_min_endings` (`story.py:164-167`).
- [ ] `_BEAT_GUARD` (`story.py:152-155`) gains `storyline_id` in its id-key set; `_create_guard`
      (`services.py:136-152`) is unchanged (keys "no overwrite" off the id-list-key, now
      per-storyline). Keep `cap=_one` (arc is a sequence).
- [ ] Verify: unit-test a 3-beat side line + a 15-beat main line coexist; `finish_storyline` on
      a line ≥ floor stops its beat emission, a `done` line BELOW floor still emits.

### T6 — Termination + completion guards (code-guarded, deterministic)
- [ ] Add `views.storyline_of(nodes)` + `views.termini(node_ids, nodes, sl)` helpers next to
      `node_targets`/`reachable` (`views.py`).
- [ ] `scenes.py` Guard A `storyline_terminus_typed` (`job="fix"`, DETERMINISTIC, generalizes
      `ending_nodes_end`+`_force_ending_end` `scenes.py:496-519`): `main`+ending-id node → force
      `end`; `side` node with `end.type=="end"` → rewrite to the branch-encoded handoff
      (`{"type":"return"}` in world, `{"type":"jump","target":return_to}` in VN) — the inverse
      of `_force_ending_end`, so the model can never end the game from a side quest; `merge` →
      no-op.
- [ ] `scenes.py` Guard B `storyline_handoff_resolves` (`job="fix"`, reuse `node_targets_resolve`
      `scenes.py:373-381`): every `handoff` side line's `return_to` resolves to a real node (VN:
      last node jumps to it; world: reachable from map re-entry via talk-hotspot roots).
- [ ] `scenes.py` Guard C1 `terminal_storyline_reachable` (`when_clean=True`, reuse
      `views.reachable`): ≥1 reachable `game_end`/`main`-terminal node exists — closes the
      reachability gap `endings_are_nodes` (existence-only) left.
- [ ] `story.py` Guard C2 `no_orphan_storylines` (`when_clean=True`): every storyline is
      reachable via `branches.spinoff`/`merge.into` and has ≥1 terminus node (stranded-on-entry
      + no-exit shapes, reusing the `reachable` set).
- [ ] Add a `blocking` `nodes_all_stamped` sanity check (every node has a `storyline`), above A/C
      (mirror how `central_question` was blocking above beats).
- [ ] Re-scope `_d_premature_endings` (`scenes.py:522-552`) to the `main` storyline only (a side
      line legitimately ends short); its `shortest_path`/beat-index logic unchanged.
- [ ] Verify: unit-tests — a side node with `end:end` is deterministically rewritten to handoff;
      a handoff with a nonexistent `return_to` emits Guard B until fixed; a graph with no
      reachable game_end emits C1; an orphan storyline emits C2.

### T7 — Open-world unification (delete the `in_world` fork)
- [ ] `scenes._world_end` (`scenes.py:880-891`) + the `in_world` branch in the closer
      (`scenes.py:906,1088-1093`): REPLACE the ≥2-ending-menu sniffing with a terminus read —
      look up the node's storyline, apply its declared `terminus` (`handback→return`,
      `ending→end` forced, `merge→jump`). A single-ending open world becomes legal.
- [ ] `scenes._backward` (`scenes.py:1076-1109`): beat-index comparison scopes WITHIN a
      storyline; a choice that spins a new storyline is a forward branch, exempt from
      fallback-collapse.
- [ ] `scenes._d_min_branches` (`scenes.py:555-561`): reframe to "every declared branch is
      realized as a menu" or leave skipped — branching is demand-driven now, not a floor.
- [ ] Verify: build a `world`+`combat` run and a plain VN run; confirm both compile
      (`compile_godot`/`compile_renpy`) and that the world run reaches a `game_end` node without
      a forced ≥2-choice climax; walk the goblin-spare→flag→gated-callback path end-to-end.

### T8 — Beat→storyline node mapping (the heaviest code risk)
- [ ] `scenes.py` UPDATE `node_view` (`scenes.py:247-293`): beat math per-storyline; a menu-child
      slot (a spinoff target) is stamped with the FIRST beat of the storyline that choice spins
      (NOT parent+1) — needs the choice→spinoff map.
- [ ] `pick_slot` (`scenes.py:308-317`): order by `(storyline spawn order, beat-index within
      storyline)`.
- [ ] `beat_for_new_node`/`_stamp_beat` (`scenes.py:320-335`): add `_stamp_storyline`;
      `_NODE_GUARD.prepare` (`scenes.py:345-346`) stamps both.
- [ ] `unrealized_beats`/`_d_beats_realized` (`scenes.py:417-425,468-474`): iterate the flattened
      `(storyline, beat)` set.
- [ ] Verify: targeted `node_view` tests — a menu-child is stamped the spun line's first beat
      (not parent+1); a main-line child is parent+1; two storylines build in spawn order.

### T9 — Round-trip + provenance strip
- [ ] `ir_assemble.py:142` strip `storyline` alongside `beat` (and any choice-level `spins`/
      branch provenance). Confirm `assemble_ir` still never reads the `story` component.
- [ ] `ir_crossref.py` — UNTOUCHED (guardrail); add a regression test that a storyline-authored
      run's assembled IR carries no `storyline`/`spine` keys and crossref passes unchanged.
- [ ] Verify: `cd src && python -c "from renpy.compiler import compile_renpy; ..."` and the
      godot equivalent on a storyline run both succeed; grep `game.json`/`script.rpy` for leaked
      storyline provenance (should be none).

### T10 — Reframed eval rubric
- [ ] REPLACE `eval/rubrics/story.json`: rewrite the top-level `note` for the storyline-graph
      artifact. Criteria: `central_question_real` → `theme_and_tone` (w 2.0; a theme WORD is now
      fine); `beats_escalate` → `beats_progress` (w 2.5, load-bearing, per-storyline linear
      coherence, delete-a-beat test, variable length OK); `endings_diverge` (2.5 gate) →
      `terminal_distinctness` (1.5, NO gate, single-ending valid, fails only on total collapse);
      `register_variety` → `tone_consistency` (1.5, inverted to CONSISTENCY — uniform tone tops);
      DROP `endings_earned`. Total weight 9.0 → 7.5, `display_multiplier: 25` kept.
- [ ] Verify: `eval/cli.py score game` runs on a storyline run; the rubric must NOT ship before
      the component + `self_digest`/detail-blocks expose storyline identity + terminus (else the
      grader collapses to one global chain). Re-grade any prior calibration reads — the
      variety→consistency flip inverts old examples.

### T11 — Story render/digest/detail blocks
- [ ] UPDATE `render_context`/`self_digest`/`story_block`/`beats_detail_block`/
      `endings_detail_block` (`story.py:19-60,228-254`) to render per-storyline, show `spine`
      instead of `central_question`, and surface each storyline's terminus type (so the grader
      and downstream prompts can see storyline grouping + terminus).
- [ ] Verify: dump `self_digest` on a storyline run; confirm each storyline's beats + terminus
      are visible and grouped.

## Open questions / risks

**User-raised #1 — can a small model manage multiple storylines?**
Mitigation: it never manages the graph. Demand-driven branching (T4) means the model authors
ONE linear storyline at a time with only its branch point + spine + storylines-so-far in
context (mirror inventory's `_item_usage_block`); the loop drains the demand set one item per
step. Beats stay cap=1 sequential. Story `priority=30` < scenes `priority=50`, so story authors
a spinoff's beats before scenes realize them — a scene that spins a storyline changes the error
SET, which the loop already handles without false-parking (`agent_loop.py:29-32`).

**User-raised #2 — will it cap storylines vs actually ending the game?**
Mitigation: terminations are code-guarded (T6), never model-chosen. Guard A deterministically
rewrites a side storyline's `end:end` into the branch-encoded handoff (the inverse of the
existing `_force_ending_end` hard override — the map records the model re-asserting a rejected
jump 250 steps straight, which is why this is code, not prompt). `max_storylines` + spin-depth
cap (T3/T4) forces convergence once hit. The completion invariant requires ≥1 reachable
`game_end` terminus, so a graph that never ends cannot complete.

**Other risks:**
1. **Distinctness has no structural guarantee.** Deleting `min_endings`/`distinct_endings`
   leaves nothing but authoring pressure against endings collapsing to one outcome (Nick
   distrusts LLM judges for exactly this hill-climb reason). Recommendation: no hard check; lean
   on each terminal storyline being spun from a distinct branch with a distinct premise, and a
   `story_storyline_add.txt` instruction. Weakest guarantee in the design.
2. **Cross-storyline gate ordering is existence-only, not path-aware.** A branch `requires` a
   flag set in another storyline, but nothing verifies the producing storyline is reachable
   BEFORE the gate. The storyline layer makes a path-aware producer-before-consumer check
   (`views.shortest_path`) newly EXPRESSIBLE (the flag now has arc identity) but does not add
   it. Natural companion PR; required for the goblin-callback to reliably fire.
3. **`story_state_schema` frozen at spec time.** A demand-driven storyline authored mid-build
   may introduce entities the frozen schema lacks. Recommendation: instruct the spec drafter to
   write a generous schema, or let `add_storyline` extend it.
4. **Demand-driven sides can UNDER-generate.** Inventory only authors what's referenced — if the
   main line never branches, zero side storylines exist (correct for a linear VN, but an "open
   world" with a thin main line produces no side content). The pressure to author branch points
   must come from somewhere (a world-level quest-giver-hotspot floor, or a spec instruction).
   This is the biggest open gap: the unification makes side quests representable + validated but
   does not by itself make the model WANT to author them.
5. **The beat→storyline stamping rework (T8) is the real code risk.** `beat` threads coverage,
   slot ordering, backward-detection, premature-ending across four `scenes.py` functions; a
   menu-child mis-stamped parent+1 instead of the spun line's first beat silently mis-orders the
   graph. Land it behind targeted `node_view` tests.
6. **`ending_paths.earned_by` prose loss.** The human-readable "how is this ending earned"
   justification at the freeze gate disappears (earning is now reachability+gates). Keep an
   optional `terminus.earned_by` prose field for the review UI even though it's no longer
   load-bearing.
7. **Merge into mid-beat ordering.** A `merge` into `at_beat` mid-storyline needs that target
   beat's node realized when the merge line's last node is authored — an ordering constraint the
   slot-picker must respect (author the merge-target's beats up to `at_beat` first). Verify the
   demand ordering can express this.

## Parked

- **Path-aware cross-storyline gate check** (risk #2) — a real new structural analysis
  (`shortest_path` per gated flag, producer-storyline precedes gate-storyline). Independent of
  this pivot; land after the storyline layer gives flags arc identity.
- **Demand generator for side quests in sparse open worlds** (risk #4) — a world-level
  "≥N quest-giver hotspots" floor or spec-drafter instruction. Needed for open worlds to feel
  populated; not required for the mechanism to be correct.
- **`story_state_schema` extension mid-build** (risk #3) — let `add_storyline` grow the schema
  rather than relying on a generous spec-time schema.
- **HD-2D / presentation** — untouched; `_resolve_presentation` and the godot 3D path are
  orthogonal to the storyline layer.
