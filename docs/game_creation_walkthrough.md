# How a game gets made — an end-to-end walkthrough

This traces one request — *"make me a cozy mystery visual novel"* — from the moment it
arrives to a packaged, launchable project, naming the function at each hop. It is the
route; `src/maestro/README.md` is the map. Read this when you want to know *what actually
runs, in what order*.

The whole thing is two phases separated by a human gate:

```
  PHASE 1 — SPEC (agentic, human-gated)          PHASE 2 — BUILD (non-LLM loop)
  request → draft story + pick modules  ──────▶  FREEZE  ──────▶  AgentLoop → get_errors → package
            → resolve the set in code             (human)         (one fix per step)
```

Nothing in Phase 2 runs until a human freezes the spec. That's the contract: the agent
builds against a frozen target it cannot move.

---

## Phase 1 — Drafting the spec

Entry points: the CLI (`python -m maestro.run "<request>"`, `run._cli`) or the chat agent
(`tools/chat_tools.propose_game_spec`). Both land in
`tools/spec_tools.propose_spec(request, run_id)`.

### 1. Draft the story AND pick the modules — `spec_write.txt`
There is **no genre classifier and no preset box.** One LLM call authors the creative
core *and* selects the mechanics. From `spec_write.txt` the proposer writes the title, a
`concept` hook, the request paragraph, the `story_state_schema`, and optional `sizing`
(named knobs like `scene_length` it may raise, never lower). In the same call it is shown
the **selectable module catalog** (`Module.selectable_catalog()` — every module's `id` +
`description`, minus the always-on foundation) and picks modules as a **`{id: reason}`
map**: each pick must be justified in one sentence against the story it just wrote, so a
module it can't tie to the story is left out.

### 2. Resolve the module set in code — `resolve_modules(picks)`
This is the important inversion: the contract is built from code, not transcribed by the
LLM. `Module.resolve_modules` takes the picks and (1) force-includes the always-on
foundation (`selectable=False` → `human`, `assets`, `state`), (2) expands each pick's
`requires` transitively (`scenes`→`cast`, `card_play`→`world`), (3) validates the set has
a realization module (`scenes`/`world`) and is projectable — falling back to a
visual-novel bundle if not — and (4) derives `spec["engine"]` via `Module.engine_for` =
the first engine that can project the whole set (`card_play`→`web`, everything else→
`renpy`). Sizing is data: each `Module.params()` declares its knob **floors** (int→max,
list→union when composed); `spec_tools` resolves them into `spec.params`.

`spec_tools` also records `spec["module_reasons"]` (the proposer's reason per chosen
module; auto-pulled foundation/deps get an auto note) so the human sees **why** each
module is in the set at the freeze gate. The spec is persisted via `RunState.write_spec`
with `frozen: false`, carrying `modules`, `params`, `module_reasons`, `engine`, and
`substrate: "discrete"`.

For our VN the proposer typically lands on modules `cast, story, scenes` (+ the forced
`assets, state, human`), engine `renpy`. The on-disk components those modules author:

```
story  →  characters  →  nodes        (story = arc + endings; cast = characters; scenes = the node graph)
asset_manifest                         (the cast/background art spec, owned by assets)
```

### 3. The human gate — `freeze_spec(run_id)`
Out-of-band approval: a human reviews the proposed spec, edits if needed, and freezes it.
There is **deliberately no freeze tool** the agent can call. `amend_spec` (reason
mandatory) is the only path to change a frozen spec mid-build, and it *un-freezes*
(pauses for re-approval). Build tools refuse while `frozen` is false.

---

## Phase 2 — Building against the frozen spec

`run.run_build(run_id)` wires everything and hands control to the loop.

### 4. Wire-up — `run_build`
- `compose(spec["modules"])` resolves the module ids → live `Module` instances (the
  always-on `human`/`assets`/`state` are force-included).
- `build_tools(spec, state, modules)` → the bounded tool set + schemas the fixes call
  through.
- `AgentLoop(spec, state, modules, tools, connector, …)` is constructed and `.run()` is
  called. The connector is wrapped in a `LoggingConnector` so every LLM call lands in
  `<run_dir>/llm_calls/`.

### 5. The loop — `AgentLoop.run`
The loop is **not an LLM.** Each iteration:

1. **Checkpoint** — honor a human pause/cancel at a consistent boundary
   (`run_control`).
2. **Collect the to-do** — call every module's `get_errors(context)` and concatenate,
   then subtract the human's waivers. Empty ⇒ **done** (after any open human todos clear).
   The to-do is *recomputed from durable state every step* — never a remembered list.
3. **Prioritize** — sort by error **type** first (`_TYPE_RANK`:
   `HUMAN` → `BUILD` → `FIX`), then by `Module.priority`. So a human todo or a missing
   build artifact is addressed before a content nit.
4. **Ask the owning module for a fix** — `module.get_fix(error) → Fix`. There is one
   shape: a single correction step. A count-driven target isn't special — its `get_errors`
   fans the shortfall into one **per-slot create-error** each (`checks.slot_errors`), and
   the loop authors them one at a time. Each create step's write tool is slot-guarded
   (`_create_guard`, installed from the module's `create_guards`).
5. **Run the fix through `Services`** — the bounded gateway (connector + tool dispatch +
   pause/cancel checkpoint + **per-fix step budget**). `BudgetExhausted` is a
   `BaseException`, so a fix physically cannot churn past its cap. The module owns the
   SHAPE of the fix; `Services` owns the LIMITS.
6. **Stall + milestones** — a cross-fix stall guard breaks loops where fixes undo each
   other; a component whose errors just cleared fires `on_milestone` (and can auto-pause
   if the human armed it).

The core invariant is **detector = fixer**: a module reports an error only if *it* can
fix it. There is no "ownership" registry and no second-class status — a module is just a
set of `(error → fix)` over the shared components. `state` is the always-on wiring
invariant: it authors nothing but enforces that every declared flag/variable/item has a
producer **and** a consumer (use-it-or-cut-it), fixing by editing the host node/place.

### 6. Minimal context, rebuilt every step — `context.py`
Each step builds a fresh `Context` snapshot from durable on-disk state only, and
`render_dict` turns it into the dict the per-module prompts consume: the spec, the
recomputed to-do, the scratchpad, the story-state snapshot, the last result, and compact
graph **views** (`views.py` — `node_view`/`place_view`). The transcript and the growing
artifact are **never** in here — that's what keeps context ~constant as the game grows.
Kill the loop after any step and the next step reconstructs everything from disk.

### 7. Slot-driven node authoring (why scenes connect)
As `scenes` works through its per-slot node create-errors, a new node must fill an **open
slot** — a dangling target a written node already points at (`_create_guard`). The graph
grows only along declared edges, so every scene is reachable, has a known parent, and is
shown the synopsis breadcrumb of the path leading to it. The `story` component (arc +
endings) gives that arc its shape, so scenes realize a planned structure rather than
improvise.

### 8. Assemble → cross-ref → project → lint
The per-step `crossref` done-condition and the final packaging both call
`compile_for(spec["engine"])`:
1. `ir_assemble.assemble_ir` lifts the decomposed on-disk components (characters +
   asset_manifest + nodes [+ places/items/story]) into one engine-neutral IR dict.
2. `ir_crossref` hard-gates that every id reference resolves (`crossref_records`:
   structured `{path, ref, kind}` routed to the module that can fix it). It runs **twice**
   — as a cheap per-step `crossref` done-condition emitted by the realization module, and
   again inside the engine compile as a backstop.
3. The engine projects: Ren'Py → `ir_vn`/`ir_pnc` → `script.rpy`; web → `game.json` + the
   static runtime.
4. The engine's lint / JSON-Schema gate runs; lint errors are attributed back to the
   owning component.

Because the agent emits **JSON IR, never engine source**, whole error classes (quote
escaping, speaker format, menu indentation, dangling jumps) are impossible by construction
and validation is a data walk, not regex over engine source.

### 9. Finalize — back in `run_build`
When the loop completes ok:
1. `renpy.fns.generate_images(artifact, run_dir)` — two-pass art (neutral sprite base,
   then img2img expression variants; ComfyUI when up, placeholders otherwise).
2. `renpy.fns.generate_voices(artifact, run_dir)` — best-effort per-line TTS (local TTS
   server; silent `.wav` placeholders backfill any clip that fails).
3. `compile_for(spec["engine"])(run_dir, distribute=True)` — package the launchable
   project.

Steps 1–2 are wrapped so art/voice failure never fails delivery. VRAM is freed for the
art/voice passes via the shared `vram_bracket` (evicts the LLM, reloads it after). The CLI
prints `ok / steps / elapsed` and the project path (`<run_dir>/game_output`).

---

## The human is in the loop, not watching it

Throughout Phase 2 the human can pause/resume/cancel (`run_control.RunControl`, checked at
every step boundary), hand-edit a node and recompile, add **human todos** that block
completion (the build parks in `awaiting_human`), and **waive** a machine error they
accept (the `human` module holds the todo/waiver store). So the real completion rule is:

```
done  ⇔  effective errors empty
         where effective errors = (Σ get_errors) − waivers + open human todos
```

---

## One-screen recap

| # | Function | What it does |
|---|----------|--------------|
| 1 | `propose_spec` / `spec_write.txt` | one LLM call: write the story **and** pick modules (`{id: reason}`) |
| 2 | `resolve_modules` | force foundation, expand `requires`, derive engine — contract built in code |
| 3 | `freeze_spec` | human approval (no agent tool) |
| 4 | `run_build` | `compose` modules + `build_tools` + construct `AgentLoop` |
| 5 | `AgentLoop.run` | the loop: collect `get_errors` → `get_fix` → run through `Services` |
| 6 | `context.py` / `render_dict` | minimal context rebuilt from disk each step |
| 7 | `checks.slot_errors` / `_create_guard` | per-slot create-errors, slot-guarded authoring |
| 8 | `assemble_ir` → `ir_crossref` → project → lint | JSON IR → engine |
| 9 | `generate_images` + `generate_voices` + `compile_for(distribute=True)` | art + voice + package |

If any of this stops matching the code, it's drift — fix it here and in
`src/maestro/README.md` in the same change.
