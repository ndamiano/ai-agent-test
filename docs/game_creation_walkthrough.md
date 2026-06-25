# How a game gets made — an end-to-end walkthrough

This traces one request — *"make me a cozy mystery visual novel"* — from the moment it
arrives to a packaged, launchable project, naming the function at each hop. It is the
route; `src/maestro/README.md` is the map. Read this when you want to know *what actually
runs, in what order*.

The whole thing is two phases separated by a human gate:

```
  PHASE 1 — SPEC (agentic, human-gated)          PHASE 2 — BUILD (non-LLM loop)
  request → classify → draft story → build  ──▶  FREEZE  ──▶  executor loop → validate → package
            contract from modules → review        (human)      (one tool call per step)
```

Nothing in Phase 2 runs until a human freezes the spec. That's the contract: the agent
builds against a frozen target it cannot move.

---

## Phase 1 — Drafting the spec

Entry points: the CLI (`python -m maestro.run "<request>"`, `run._cli`) or the chat agent
(`chat_tools.propose_game_spec`). Both land in `spec_tools.propose_spec(request, run_id)`.

### 1. Pick the preset — `_classify_genre(request)`
Keyword match first (cheap, deterministic): "blackjack/wager/…" → `card_ante`;
"escape room/point-and-click/…" → `point_and_click`. Only an ambiguous request costs a
classification LLM call (`classify_genre.txt`). Anything uncertain falls back to `vn` —
the human freeze gate catches a wrong guess. A **preset** is just a named module set.

### 2. Draft ONLY the story — `propose_spec.txt`
The proposer LLM is creative-only. It writes the title, the request paragraph, the
`story_state_schema`, and an optional `sizing` dict (named knobs like `scene_length` it
may raise). It does **not** author the component contract — that boilerplate was the
model's job to transcribe and the code already guarantees it.

### 3. Build the contract in code — `_spec_components(modules)`
This is the important inversion. `compose(modules)` (`modules.py`) unions the active
modules into one bundle, and the components — their ids, build-order `deps`, and baseline
**done-conditions** — are read straight off that bundle. The modules' baseline *is* the
contract. Then `_apply_sizing` raises any mins the proposer asked for (never below floor),
and `_normalize_spec` re-asserts the engine baseline so the human reviews the *real*
contract, not a thin one the LLM happened to draft.

The result is persisted via `RunState.write_spec` with `frozen: false`. For our VN the
preset expands to substrate `discrete_state` + modules `cast, assets, outline, dialogue,
economy`, engine `renpy`, and components roughly:

```
premise  →  outline  →  nodes        (deps: nodes depends on outline depends on premise)
asset_manifest                       (the cast/background art spec)
```

Each component carries done-conditions like `count(nodes) ≥ N`, `each_node_min_lines`,
`reachable_from_start`, `refs_resolve(premise.endings → outline.ending_paths)`,
`crossref`, `compiles`.

### 4. The human gate — `freeze_spec(run_id)`
Out-of-band approval: a human reviews the proposed spec, edits if needed, and freezes it.
There is **deliberately no freeze tool** the agent can call. `amend_spec` is the only path
to change a frozen spec mid-build, and it *un-freezes* (pauses for re-approval). Build
tools refuse while `frozen` is false (`Executor.run` raises `SpecNotFrozenError`).

---

## Phase 2 — Building against the frozen spec

`run.run_build(run_id)` wires everything and hands control to the executor.

### 5. Wire-up — `run_build`
- `register_ir_checks()` makes engine done-conditions (`reachable_from_start`, `crossref`, …) available to `validate`.
- `compose(modules_for(spec))` → the bundle. From it: `mode_tools` (which tools each component-mode may call), `mode_prompts`, per-component **skeleton guides** (each authoring step sees ONLY its own component's shape), **projectors** (compact graph views), **upstream views** (trim a settled component for downstream injection), and **sub_runners** (the iterate-until-done loops).
- `make_llm_decider(...)` is the agent: given a context, return `{"tool", "args"}`.
- `Executor(spec, state, tools, decider, …)` is constructed and `.run()` is called.

### 6. The loop — `Executor.run`
Each iteration:

1. **Checkpoint** (`_checkpoint`) — honor a human pause/cancel at a consistent boundary.
2. **Recompute the to-do** — `_machine_failures()` = `validate(spec, state)` minus human waivers. Empty against a frozen spec ⇒ **done** (after any open human todos clear in `_await_human`).
3. **Route** — pick `mode` = the failing component lowest in `spec.dep_order()`. The *executor* decides this, not the agent; routing stays non-LLM. So `premise` is built before `outline` before `nodes`.
4. **Build context** (`build_context`) — a fresh minimal dict from durable state only:
   - the spec (titles/descriptions/done-conditions),
   - the `todo` (the failure list),
   - the active `mode`,
   - `upstream`: every *locked* (passing) component, trimmed by its `upstream_view` (e.g. `asset_manifest` → ids only, dropping image-gen prose the node author doesn't need),
   - `active_view`: a compact projection of the component in play (e.g. the node graph),
   - `scratchpad`, `story_state`, `last_result`, `last_read`, `stalled`.
   The transcript and the growing artifact are **never** in here — that's what keeps context ~constant as the game grows.
5. **Act**:
   - **Simple component** (premise/asset): the decider returns one tool call; `_dispatch` runs it.
   - **Sub-loop component** (nodes): `pick_target` selects the single highest-priority failing check (`_CHECK_PRIORITY`: `count` → per-node `lines`/`location` → wiring `reachability`/`branches`/`refs` → content → `compiles` LAST), and the module's `make_subloop` runs a bounded tool conversation with its own working memory that drives **that one check** to green, reporting each node as it lands. Why `compiles` last: structure checks are cheap text walks that don't need a clean build, so one stubborn lint error must not starve node creation.
6. **Recompute + milestones** — `validate` again; any component that just went green fires `on_milestone` (a check-in event; can auto-pause if the human armed it).

The loop is **stateless per step** by design: kill it after any step and the next step
reconstructs everything from disk. The agent never has to remember what's left — `validate`
tells it, every step.

### 7. Slot-driven node authoring (why scenes connect)
While the sub-loop drives the node `count`, a new node must fill an **open slot** — a
dangling target a written node already points at (`_create_guard`). The graph grows only
along declared edges, so every scene is reachable, has a known parent, and is shown the
synopsis breadcrumb of the path leading to it. The author continues the arc instead of
re-treading a sibling. The `outline` upstream (beat sheet: premise → outline → nodes)
gives that arc its shape, so scenes realize a planned structure rather than improvise.

### 8. `validate` — the steering signal in detail
`validate.py` runs each component's typed done-conditions against the assembled artifact
and returns `{component_id, check, detail}` per failure. The subtle part is **attribution**:
a check may route its failure to the component that can *fix* it, not the one that *declared*
it. The `crossref` check is the example — a dangling jump routes to `nodes` (repoint to
fix), but a missing meta declaration stays on the spine (declare to fix). This is what keeps
a cross-component error from dead-ending in a mode without the right tools.

### 9. Assemble → cross-ref → project → lint
The `compiles` check (and the final packaging) call `compile_for(spec.engine)`:
1. `ir_assemble.assemble_ir` lifts the decomposed on-disk components (premise + asset_manifest + nodes [+ places]) into one engine-neutral IR dict.
2. `ir_crossref` hard-gates that every id reference resolves.
3. The engine projects: Ren'Py → `ir_vn`/`ir_pnc` → `script.rpy`; web → `game.json` + the static runtime.
4. The engine's lint/JSON-Schema gate runs; lint errors are attributed back to the owning component.

Because the agent emits **JSON IR, never engine source**, whole error classes (quote
escaping, speaker format, menu indentation, dangling jumps) are impossible by construction
and validation is a data walk, not regex over engine source.

### 10. Finalize — back in `run_build`
When `result.ok`:
1. `renpy.fns.generate_images(artifact, run_dir)` — two-pass art (neutral sprite base, then img2img expression variants; ComfyUI when up, placeholders otherwise). Wrapped so art failure never fails delivery.
2. `compile_for(spec.engine)(run_dir, distribute=True)` — package the launchable project.

The CLI prints `ok / steps / elapsed` and the project path (`<run_dir>/game_output`).

---

## The human is in the loop, not watching it

Throughout Phase 2 the human can pause/resume/cancel (`run_control.RunControl`, checked at
every step boundary), hand-edit a node and recompile, add **human todos** that block
completion (the build parks in `awaiting_human`), and **waive** a red machine check they
accept (`hitl.py`). So the real completion rule is:

```
done  ⇔  effective_failures empty
         where effective_failures = validate − waivers + open human todos
```

---

## One-screen recap

| # | Function | What it does |
|---|----------|--------------|
| 1 | `_classify_genre` | request → preset (keyword, else LLM, else `vn`) |
| 2 | `propose_spec.txt` | LLM writes story only |
| 3 | `_spec_components` / `compose` | contract built in code from modules |
| 4 | `freeze_spec` | human approval (no agent tool) |
| 5 | `run_build` | wire decider + sub-runners + executor |
| 6 | `Executor.run` | the loop: context → decide → dispatch → validate |
| 7 | `make_subloop` / `_create_guard` | slot-driven node authoring |
| 8 | `validate` | typed checks → attributed to-do |
| 9 | `assemble_ir` → `ir_crossref` → project → lint | JSON IR → engine |
| 10 | `generate_images` + `compile_for(distribute=True)` | art + package |

If any of this stops matching the code, it's drift — fix it here and in
`src/maestro/README.md` in the same change.
