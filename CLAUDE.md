# Maestro

An AI platform that makes things. The goal is simple: user says "make me a game", a while later a
good game exists. The AI quality is the product — everything else (UI, install, visuals) is
scaffolding.

The north star is **any game + local**: ask for a game you imagine, get a real, playable game, and
every generation runs on a local GPU (a 5090-class card, a ~30B model) — no cloud in the build loop.
"Good" is the constraint we consciously bend today; breadth and local-first come first.

See `docs/roadmap.md` for status, `docs/architecture.md` for the layer above this one (processes,
state, trust boundaries, what pins the control plane to one box), and `docs/experiments.md` for what
each change to the loop actually measured.

---

## How it works

The model writes **real browser game code** — plain HTML/CSS/JavaScript, no framework, no build
step, no engine of ours between it and the screen. Two stages:

1. **Prompt (stage 1, human-gated):** the run stores the request VERBATIM as its prompt, and the
   human reads it in the box it will be sent from. **THE PROMPT IS THE ARTIFACT** — what is on
   screen is byte for byte the build's one user message, so approving it and building it are the
   same act. No inference runs in this stage: a model between the person's words and the build's
   input would mean approving one text and building another. Pressing **Build** stores the edit
   and starts the build, so there is one action and one writer.
2. **Build (stage 2):** a non-LLM **driver** (`maestro/codegen/build_chain.py`) hands the model six
   tools — `list_files`, `read_file`, `write_file`, `edit_file`, `generate_media`, `done` — and a
   running transcript, and lets it write the game. It decides the file layout, the data shapes, the
   systems, and what art gets drawn. It calls `done` when the game is playable. The build is not a
   resident loop: each llm turn is a job on the `llm` queue and its completion drives the next turn,
   so the driver holds no state between turns.

**Why no kit, no contract, no gates.** Measured over a 25-game grid across two local models
(2026-07-27): given the same requests, this shape produced working games in 2–4 minutes, 4/4, while
an interface-first pipeline (declare an architecture → review it → author per function) produced
unplayable ones in 5–37 minutes from the *same model*. The decomposition's own contract was the
failure: `draw_hand` declared and implemented but never called, in all three models, because the
schema had no boot-sequence contract. A local model given the whole problem and a transcript
decomposes it *by domain* and wires it up; given a partial view and a schema, it fills the schema.

**The transcript IS the memory.** There is no context-rebuilding, no per-step minimal window. When
the prompt approaches the context limit the OLDEST WHOLE ROUNDS are dropped and the model is
re-grounded on the current file listing (`build_steps.compact`). Rounds are never split — a `tool`
message whose assistant `tool_calls` is gone is an orphan, and a chat template is entitled to 500
the turn. The re-grounding matters more than the trim: the dropped rounds are where the model
watched itself write the files, so without it the model edits code it no longer remembers.

**A GATE MAY ONLY DETECT BROKEN, NEVER "BAD".** A constraint the model builds to satisfy is only
safe when satisfying it IS the goal. "must not crash" can only be met by not crashing; "the game
must DO X" is legitimately false for some games, so no edit satisfies it and the loop grinds to its
cap while the model contorts the design to appease it. Today the only thing standing between a build
and `built` is **`index.html` exists** — without one there is nothing for a browser to open.
Everything past that is a HUMAN judgement: a game that runs but plays wrong is obvious to a person
and near-impossible for code, so the gap stays VISIBLE rather than filled with a proxy.

`tsc` used to be the contract gate, and it earned its place against `engine.d.ts` — types to check
*against*. Measured on the same 25-game grid after the kit came out: unfiltered `--checkJs` reported
54–94 errors on games that WORK (implicit-any, `getElementById` possibly-null, `let x = []` inferring
`never[]`); filtered to the codes that mean something in untyped JS it found **zero real defects**,
and every hit traced to a global declared in an inline `<script>` that tsc never reads. It is gone.
If output ever moves back to TypeScript it comes back for free and is worth it immediately.

**The FIRST `done` is answered, not accepted.** One nudge back — what is unfinished, what is stubbed
— and the second `done` ends the build. It costs one turn, and it is asked ONCE: a model told twice
that it is not finished starts inventing work. Measured over a 17-cell grid (2026-07-30, see
`docs/experiments.md`): it was best or joint-best on three of four game requests, while every arm
that bought depth by splitting authoring across builds shipped load-blocking defects instead.

**"Did it deliver?" is a HUMAN question.** A build ends when the model calls `done` twice (or hits
its step cap) and nothing machine-side judges the result. The human plays it and says what to change:
`python -m maestro.codegen.run --fix <run_id> "<note>"`, which re-enters the same turn machine with
the note as its request. Any automated judge that returns here has to answer the question that
retired the last one: judge-then-fix rounds were measured to spend 208 of one build's 227 steps and
score WORSE in round 2 than round 1, because each fix broke a claim that already worked.

**The game asks for its own art, as it writes the code that uses it.** `generate_media(id, prompt,
kind)` enqueues one render and answers IMMEDIATELY with the path the file will appear at
(`assets/<id>.png`, or `.glb` for `kind: "mesh"`); the model writes that path into the game and draws
its own shape until the file lands. The asset stage is then free: no planning call, no source
rewrite, no static analysis of what the game spawns, and the GPU draws art while the llm turns keep
writing code. `game/assets.json` is written by `request_media`, never by the model: it is the record
the gallery lists, the top-up re-renders from, and the regenerate re-prompts against. One request is
one BATCH, so each asset re-stages as it lands.

The compute budget is the only cap on how much art a build may ask for, and a refused enqueue is
REPORTED to the model as "draw this one with code instead" — a build that cannot have art has to be
told to draw one rather than left waiting for a file that is never coming.

---

## Architecture

```
runtime/
  vendor/three.module.js  vendored three.js (MIT, self-contained) + GLTFLoader — copied into every
                         game folder at seed, so a 3D game imports a renderer with no network fetch.
  games/<run_id>/        staged games, served at /play

src/
  maestro/
    codegen/             THE build path:
      build_chain.py     THE build DRIVER — what a finished llm turn does next. A build is a linear
                         chain of `llm` jobs tagged metadata.stage="build"; /worker/complete routes
                         here. advance() runs ALL local work — tool dispatch, staging —
                         synchronously and
                         SUSPENDS only at a real inference (enqueue one llm job + return; the
                         process is free to die). Never more than one turn in flight per run, and
                         advance runs only in the control-plane process (completion handler +
                         reaper), so an in-process lock serializes them. kickoff/pause/resume/stop/
                         is_active/status_of are the API/CLI entry points. `stop` ends a build where
                         it stands and KEEPS what it wrote: playability is judged as it is at the
                         step cap (an index.html), while the builds row records `stopped` — a run
                         ended by hand over a game that runs is not a run that failed. `pause`
                         DEQUEUES: a still-queued turn is cancelled and its step refunded, a claimed
                         one is left to its worker and applied when it lands, and the build parks at
                         the ENQUEUE boundary rather than the top of advance. That boundary is what
                         makes pause cost nothing: parking at the top discarded a turn the GPU had
                         already been paid for, and announced itself on every reaper re-drive (one
                         feed line every 5s, forever). A refused compute budget PREEMPTS the run's
                         still-pending asset jobs before giving up — gameplay beats art.
      build_steps.py     the turn MACHINE: step(spec, run_dir, tools, cursor, result) -> Infer|Done.
                         A `result` of None is NO TURN TO APPLY (a resume, a reaper re-drive) and
                         re-asks from the transcript as it stands; `{}` is a turn that ran and
                         answered with nothing, which the nudge branch handles. Collapsing the two
                         scolded the model for a reply it never sent and burned a turn.
                         Owns the six tool schemas, the transcript, compaction, the DONE-NUDGE
                         (`cursor.done_nudged` — asked once, then the next `done` is taken), and the
                         out-of-output-tokens branch (a cut-off reply saved NOTHING — say so, rather
                         than letting the model believe the file landed). A failing call resent with
                         identical arguments is COUNTED and the count told back; what to do instead
                         is the model's call. That count is keyed PER CALL, not against the previous
                         one: a model stuck on an edit re-reads the file between attempts, and a
                         succeeding read between two identical failures must not clear the ledger
                         (measured 2026-07-29: 12 identical failing edits, each scored as the first).
                         A read reaches the transcript as the FILE
                         (`<file path=…>`), never serialized — a JSON body shows every quote as \"
                         and the model copies that into old_text, where it matches nothing (measured
                         2026-07-28: 17 of one build's 32 turns, resent byte-identical).
      build_state.py     the durable build CURSOR (runs/<id>/build_state.json) — phase, step count,
                         the PAUSE flag, the done-nudge flag, the growing transcript, and the
                         read→edit tool grounding,
                         rehydrated into build_tools each completion (a fresh process would else
                         refuse a resumed edit). Job metadata carries only {stage,run_id,build_id};
                         this file is the single source the completion reloads, advances, rewrites.
                         Pause lives here and not in memory: a control plane that restarts mid-build
                         holds nothing, while its turns keep completing.
      tools.py           list_files / read_file / write_file / edit_file / generate_media — the
                         smallest surface that works, and kept that way. A path is resolved and must
                         land inside the game folder. Every failure is REPORTED to the model as text
                         (a missing argument names itself) and never guessed at: substituting a
                         default for a missing `path` sent every write in a run to one file.
                         `read_file` returns a WINDOW of whole lines from a 1-based `offset`. Both
                         halves are load-bearing: a window cut mid-line is text the model cannot
                         reproduce and it copies the cut into old_text, and without `offset` the
                         tail past the ceiling is unreachable, so an edit there can never land
                         (measured 2026-07-29: 12 byte-identical edits, 248 of 249 chars matching,
                         the 249th the cut). A window that stops short says where the rest is and
                         that a file this size is worth splitting.
      staging.py         where a game lives (runs/<id>/game/) and how it reaches the browser: copy
                         the folder to runtime/games/<slug>/. No bundle, no transform. Also the SEED
                         (seed_vendor): the game folder starts holding the vendored renderer and
                         nothing else, since everything placed there steers the first list_files.
      assets.py          the ASSET stage — `request_media` is what the game's generate_media call
                         runs: enqueue ONE `image` job, record the ask in assets.json, answer with
                         the path. Nothing plans, rewrites or inspects the game's source. `kind:
                         "mesh"` chains image → TRELLIS. A repeated id, an already-rendered file, a
                         blocked prompt and a refused budget are all answered, never retried blind.
                         Also owns start_from_manifest (the TOP-UP: re-render what the record says
                         is still missing) and the single-asset regenerate — the user's text is a
                         CHANGE NOTE merged (one small llm call) with the entry's original prompt,
                         so "give him a red cape" keeps the goblin; img2img seeds from the render.
      asset_chain.py     what a finished asset job does NEXT — the names in its `metadata.then`: a
                         CONTINUATION to enqueue (mesh_from_image), OPERATIONS on this result
                         (save_sprite / decimate), and the batch's FINALIZE. This module owns those
                         names so the queue stays a generic transport that never learns what an
                         asset is.
      prompts/           build.txt
      run.py             create_run / propose_prompt / set_prompt / run_build (CLI: kickoff +
                         block-poll the cursor) / fix_from_note + the CLI. The web build/fix path is
                         fire-and-forget through build_chain.kickoff, not run.py.
    services.py          parse_args — any argument shape a local model returns → a dict.
    tool_calls.py        recovering a tool call the model wrote as TEXT, when the server's own
                         parser didn't claim it. A registry of encodings (Hermes/Qwen, DeepSeek,
                         Mistral, Llama python-tag, two XML forms, named object, arg-shape), tried
                         strictest first. A parser only wins if EVERY call it found names an offered
                         tool and carries that tool's required arguments.
    state.py             RunState — durable per-run dir <working_dir>/runs/<run_id>/ (spec.json,
                         game/ folder). Ownership + charge state live in db/, not the run dir.
  worldgen/              a standalone procedural world generator (heightfield town inside a
                         wilderness ring: forest, POIs, roads, named regions). Currently UNWIRED —
                         it was the one thing that produced real scale, and re-pointing it to emit
                         data the game reads is an open decision, not a dependency.
```

The rest of the platform is build-path-agnostic: `auth/` (identity, bearer sessions, credits, the
/play cookie gate), `db/` (games/builds/events/jobs/workers + the compute budget), `worker/` (the
pull-side GPU worker), `scaler/` (the RunPod autoscaler), `api/` (FastAPI routers), `llm_clients/`,
`tools/`, `config/`, and `frontend/` (the React SPA, served same-origin by the API). For their
contracts see the module docstrings — they are the authority. How the pieces sit as PROCESSES —
the two planes, where state lives, the trust boundaries, and the five things that pin the control
plane to one box — is `docs/architecture.md`, not here.

**Inference runs ONLY inside a build.** There is no conversational surface: `POST /api/games`
takes the prompt the person typed, creates the run, charges it, and starts the build in one call, so
every llm job on the queue belongs to a game that is paying for it.

**Inference path (build) — build-as-jobs:** `build_chain.advance` runs one `build_steps` turn,
enqueues it on the `llm` queue tagged `metadata.stage="build"`, and RETURNS (the process may die). A
worker runs the turn; `/worker/complete` → `build_chain.on_completion` reloads the durable cursor
(`build_state.json`), applies the result, and advances. Crash recovery: a build with no turn in
flight and not done is re-advanced by the reaper (the per-run advance lock prevents a double-drive).

**The human-note fix** (`fix_from_note` / `kickoff(kind="fix")`) re-enters the SAME turn machine with
the note as its request. The model lists and reads the files itself, so there is nothing to hand it
up front.

**Adding a capability.** The default answer is a line in `prompts/build.txt`, not code. The order is
**prompt line → a snippet in the repo → a primitive we own**, and a primitive only after a prompt
line has failed twice, across two models. Open ledger from the 2026-07-27 grid, none yet earning
more than a prompt line: 3D scenes lit near-black (2/4, both models), fixed canvas with no window
scaling (every 2D game), silent games (all four arcade + the deck-builder), arrow-keys-only input.

`generate_media` entered UNMEASURED (2026-07-28) — art is the one capability no `write_file` can
stand in for. What settles it is a battery run against the manifest prompt line: whether the model
calls it, uses the returned path verbatim, and still draws a fallback shape. An
unused schema costs every turn of every build, so a tool that fails that comes back out.

**Adding a build STAGE** (beyond build/asset): register a driver keyed on `metadata.stage` in the
`/worker/complete` dispatch — the queue stays a generic transport.

---

## Settings & running

**Settings:** `src/config/settings.json` (gitignored). Copy from `settings.example.json`.
- `llm.model`, `llm.n_ctx`, `llm.max_tokens`, `llm.reasoning`. No endpoint: LLM inference rides the
  queue, so an `llm` worker must be running or every call times out. `n_ctx` is what the INPUT
  budget is computed from — MessageBuilder trims the transcript to
  `max(n_ctx − 16k, n_ctx/3) × 3.5` chars, where the 16k is the build turn's own output cap
  (`build_steps.MAX_TOKENS`) and 3.5 chars/token was measured on live code-heavy payloads, not the
  4:1 prose heuristic. The local router never reports its window, so set `n_ctx` to the server's
  `-c`: too large and nothing trims until the prompt has already overflowed. `llm.max_tokens` is
  only the connector's default ceiling — the build path passes its own.
- The WIRE format is the WORKER's, not a setting here: the control plane enqueues a CANONICAL chat
  request and the worker translates it for whatever its own target serves (`worker.agent --api
  chat|responses`, default `chat`; see `llm_clients/wire.py`). `responses` is the only local
  dialect that honors `reasoning.effort`; `chat` is the universal one, for engines that serve only
  it (ninfer). On `chat` the worker sends `presence_penalty: 0` explicitly — ninfer defaults it to
  1.0, which degrades long structured output — and turns `reasoning: "none"` into the
  `enable_thinking` template switch. Adding an engine is a branch in `worker/handlers.llm`, never a
  change here.
- The worker-pull queue is the ONLY transport to a GPU — llm, sprite/mesh images (queue `image`)
  and TRELLIS meshes (queue `mesh`) alike. There is no `enabled` flag and no endpoint setting on
  this side: the control plane touches no GPU at all, and a queue with no worker means every job on
  it times out. `workqueue.token` is the worker bearer secret. One worker per queue, and a queue
  owns its card:
  `python -m worker.agent --server <cp>:8000 --token <token> --queue image --target localhost:8188`
  (defaults: server localhost:8000, target localhost:1234, queue llm). exec_seconds are debited to
  the owning game by the `game_id` on the job row, and a job with none is neither metered nor gated.
  The build and asset enqueues pass it directly; the one BLOCKING path (`queue_client.run_job`, what
  the connector's `generate_with_tools` rides) reads it off the `run_scope` contextvar instead, so a
  call there outside a scope spends GPU nobody is charged for.
- `data_dir` (env `MAESTRO_DATA_DIR`, default `<repo>/data`) — where platform.db + auth.db live;
  control-plane state, deliberately not under `working_directory`.
- `runpod.*` — the autoscaler (see `src/scaler/` + docs/deploy.md): `enabled`, `api_key`,
  `network_volume_id`, `cp_url` (the pod-reachable control-plane URL) and per-queue `queues.<name>`
  scaling blocks (template_id, gpu_type_ids, max_workers, thresholds, idle_exit_seconds). The
  `queues` dict in settings.json replaces the default wholesale — carry complete blocks.
- **Model categories** `large`/`medium`/`small` carry one knob, `message_budget_chars`, and it is
  only the FALLBACK: when `llm.n_ctx` is set the input budget is derived from the window instead
  (above), so the category decides nothing on a configured box.

**Run a build (CLI):** `cd src && python -m maestro.codegen.run "<request>"` (the request is the
prompt → build). `--new "<request>"` stops with the prompt on disk so it can be edited first,
`--build <run_id>` builds that prompt, `--fix <run_id> "<note>"` applies a playtest note, and
`--assets <run_id>` re-renders the art the game asked for and never got.
**Play a build:** open `runtime/games/<run_id>/index.html` — a game is plain browser files, but a
3D one needs http, not file:// (`<script type="module">` is CORS-blocked from a file origin).
**Run backend:** `source venv/bin/activate && python run.py`  •  **Frontend:** `cd frontend && npm run dev`
**Run tests:** `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`  •  frontend:
`cd frontend && npm test` (vitest)
**Run the llm worker's target:** `llama-server` needs
`--chat-template-kwargs '{"enable_thinking":false}'` — load-bearing, and `--reasoning-budget 0`
alone is a no-op: without it Qwen3.6 thinks in `content` and authoring turns truncate at the output
cap before the tool call. Full invocation + the other local services: `tasks/nicknotes.md`.

---

## Code standards

### No half measures, no backwards compatibility
When a full fix is available, take it — never the partial patch that leaves the root cause in place.
We are both producer and consumer: no external callers, no published API, no old data to migrate. So
never add backwards-compat shims, deprecation paths, or "keep the old way too" code. Delete the old
way and move the call sites. Surgical means *small and complete*, not *small and half-done*.

### Minimal and surgical
Edit only what the task requires. No cleanup/refactoring/"while I'm here" changes unless asked. Three
similar lines beats a premature abstraction. No feature flags, compat shims, or half-finished stubs.

### Tests
Write tests for every non-trivial change — test behaviour and contracts, not implementation details.
Run tests before reporting done. Fix failures first. Integration tests in `tests/integration/` need
live services — skip unless testing connectors.

### Code review
After any non-trivial change, self-review the diff: security, unintended scope creep, missing tests,
regressions. Do this before declaring done.

### Documentation
Keep CLAUDE.md and docs/roadmap.md in sync with reality, in the same commit as the code.

### Comments
Default: none. Only when the WHY is non-obvious (hidden constraint, workaround, subtle invariant).
Never comment WHAT the code does.

### Error handling
Only validate at system boundaries (user input, external APIs, tool results). No defensive fallbacks
for things that can't happen.

---

## Keeping prompts hill-climbable
1. **One `.txt` file per LLM call.** Never inline prompt strings in Python. Each call gets its own
   file under `maestro/codegen/prompts/`. There is one: `build.txt`.
2. **Load-bearing system prompts belong in a `.txt` too**, not a hardcoded string.
3. **A prompt fix states a general law; examples only illustrate.** Never encode the game that
   triggered it, and validate on the battery rather than the motivating case.
4. `build.txt` is the whole of what the model is told about how to make a game. Every line in it
   must earn its place — it is read on every turn of every build.

## Small model strategy
Small models aren't dumb — they're easily distracted, following the most recent, most concrete
instruction. Two things that DON'T follow from that, both measured:
- **Do not decompose the work for them.** Given the whole problem and a transcript they decompose it
  by domain and wire it up; given a partial view and a schema they fill the schema and never call
  it. See "Why no kit" above.
- **Do not hand them a rule they cannot satisfy.** A gate that is usually right costs more than no
  gate: when it is wrong it burns a hundred steps and never says it is unsure.

What does help:
1. **Output skeleton before field descriptions** — show exact structure first, let the model fill it.
2. **Say what is true, not what is forbidden.** "Bind WASD and the arrows" beats "don't use only
   arrows".
3. **Give them the whole window.** The input budget rides `llm.n_ctx`, not the model category, and
   a build's input IS its transcript — set `n_ctx` to what the server was launched with and the
   trim lands where it should.
4. **Reasoning off by default** — `reasoning: "none"` is the floor for every build turn. Passing
   None instead reaches the connector as "let the model pick", which skips the enable_thinking
   switch; a measured turn then spent 16000 tokens reasoning and never called a tool. Some local
   models only honor on/off — graded efforts collapse to the same budget.
