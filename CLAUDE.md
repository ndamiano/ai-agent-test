# Maestro

An AI platform that makes things. The goal is simple: user says "make me a game", a while later a
good game exists. The AI quality is the product — everything else (UI, install, visuals) is
scaffolding.

The north star is **any game + local**: ask for a game you imagine, get a real, playable game, and
every generation runs on a local GPU (a 5090-class card, a ~30B model) — no cloud in the build loop.
"Good" is the constraint we consciously bend today (see `docs/codegen_rebuild_plan.md`); breadth and
local-first come first.

See `docs/ROADMAP.md` for status and `docs/codegen_rebuild_plan.md` for the plan.

---

## How it works — codegen against a fat kit

The model writes **real TypeScript game code**, not an intermediate representation. Two stages:

1. **Spec (stage 1, human-gated):** the chat model drafts a small design SPEC from the request
   (title / genre / entities / controls / mechanics / win-lose). The human reviews and **freezes** it.
2. **Build (stage 2):** a non-LLM **executor** (`maestro/agent_loop.py`) drives the local model to
   author and patch a **folder of TypeScript modules** (`game/main.ts` + system files, a `manifest.json`
   contract) **against the primitive kit** until the local gates pass. Simple games are one file;
   complex ones decompose by system (the model plans the file list first, authors one per step).

**The fat-kit thesis:** breadth comes from the model COMPOSING primitives, not from N per-genre
generators. Every hard/ambiguous mechanic (physics, collision, tilemaps, pathfinding, 3D) is a kit
call, so generation is composition-of-primitives in small chunks — where the gap between a 30B and a
frontier model collapses. Widen the kit to absorb a hard mechanic; never hope the model hand-rolls it.

**Sim/render split (load-bearing law):** `update(dt, input, kit)` mutates plain state and never
draws; `draw(g, kit)` reads state and never mutates. So the sim runs **headless in pure Node, zero
deps** — the local gradient — and render is the only engine-specific layer. This carries 2D → 3D
with no change to the gradient. Never violate it.

**The local gradient (no frontier critic):** the gates decide "done", not the model, in order:
- **typecheck** (`tsc --noEmit`) — the CONTRACT gate. Catches cross-file/type bugs (missing exports,
  wrong data shapes, bad arg counts, kit misuse) BEFORE the game runs, with file:line attribution.
  Games are checked against `runtime/engine.d.ts` (ambient kit types). This is the deterministic fix
  for a whole class of silent cross-file bugs that no runtime gate can see.
- **headless** — bundle (esbuild) then step the sim N frames, catch crashes/divergence.
- **probe** — generic correctness invariants (controls actually DO something — move an entity, spawn
  one, or mutate non-positional state, so turn/card games count; no entity rests in a solid tile; no
  Math.random/Date.now — determinism is what makes the gates' diffs meaningful), each violation an
  actionable, units-aware diagnosis.
- **render** — call `draw()` against a recording mock: catch draw-time crashes + blank screens.
- **scroll** — a world bigger than the screen must be followed by a panning camera.

"Done" = the artifact passes the gates, never the model claiming done. Each loop step rebuilds a
minimal context from durable on-disk state (the frozen spec + the failing file + the failing check's
message), so context stays ~constant and the transcript is never used as memory. A gate fix is a
bounded read→write **subloop** (the sanctioned multi-call Check.run): the model reads whatever sibling
bodies it needs on demand — exposing a CROSS-FILE mismatch the signatures can't show (e.g. main.ts
assumes world.ts spawns the player but none does) — then writes ONE complete file it self-selects.
The reads live in an EPHEMERAL transcript confined to that one fix; the outer loop stays stateless and
re-gates after. Bounded by the Services budget + a turn cap; on cross-fix stall the read tool is
dropped so the fix must ACT. Still ONE file out (bounded output), so fixing one system can't drop another.

---

## Architecture

```
runtime/                 The primitive KIT (hand/frontier-authored offline, run local)
  engine.js              kit v1: rng, vec math, entities/spawn/cull, integrate(+3), aabb,
                         tilemap, walk/jump/physics, camera, input, run() (browser 2D),
                         simulate() (headless sim), probe() (invariants + dead-mouse-in-non-fp).
                         integrate3 + z for 3D; 3D steering (seek3/flee3/wander3/patrol3 +
                         avoidRects building collision). DEPTH primitives: talkOpen/talkStep/talkHud
                         (the whole dialogue/shop loop), kit.quest (add/complete/log — milestones
                         that do NOT end the game; win/lose reserved for the spec's ending),
                         kit.notify (engine-drawn toasts). run() preloads the game's assets.json
                         sprites; kit.sprite(id) → the loaded image or null (null headless ⇒ the
                         game draws its shape).
  engine3d.js            run3d — three.js renderer; the model writes NO three.js, only pure 3D
                         sim + shape tags (box/sphere/ground) + an optional camera(cam,kit) hook.
                         Hemisphere light + distance fog (config.fog), procedural walk-bob on moving
                         entities, {kind:"marker"} HUD items projected to screen waypoints, toasts.
                         Preloads assets.json meshes; an entity's `mesh` id → the loaded GLB
                         (recentered + scaled to its box), else the primitive shape. Fills the window.
  vendor/three.module.js vendored three.js (MIT, self-contained) + GLTFLoader.js (+BufferGeometryUtils)
  engine.d.ts            ambient TypeScript types for the kit (Kit/GameObject/Entity/World/Input/
                         DrawApi/…) — games are type-checked against these; precise on the kit surface
                         + module boundaries, entity FIELDS left open.
  kit_api.md             the injected 2D kit surface (load-bearing prompt input)
  kit_api_3d.md          the injected 3D kit surface
  headless.mjs / probe.mjs / render.mjs / scroll.mjs   node runners for the four runtime gates
  index.html             browser harness (loads games/<slug>/main.js bundle; 3D → run3d else run)
  games/, specs/         sample games + specs (fixtures/reference)
  node_modules/          runtime toolchain (typescript + esbuild; gitignored)

src/
  maestro/
    codegen/             THE build path (replaces the deleted IR):
      gates.py           typecheck (tsc → per-file errors) · build_bundle (esbuild main.ts → main.js
                         + inline sourcemap) · run_headless/probe/render/scroll (build then run the
                         bundle with --enable-source-maps, so a crash stack names the .ts source).
      tools.py           write(code, file) / edit(file, old, new) / read_file (whole file, or
                         offset/limit line window; per-file .ts, path-safe — a slice does NOT ground
                         an edit). The 9-16KB kit doc rides in the fix loop's SYSTEM prompt (uncounted
                         by the char budget), not a user turn, so file reads aren't demolished to fit.
      module.py          CodegenModule = planned → authored → typechecks → runs → plays → renders →
                         scrolls (blocking where noted). AUTHORING = a whole-body Check.run: ONE raw
                         fenced-```ts completion per file. GATE FIXES route through dispatch_fix →
                         a FIX CLASS (fix_classes.py), then a read→write subloop (_read_write_loop_fix):
                         read_file any sibling on demand (tool-calls via MessageBuilder, which
                         dedups superseded reads), then write/edit ONE self-selected file.
                         Ephemeral per-fix transcript; outer loop re-gates.
      fix_classes.py     the error-class → fixer MAP (codegen analog of IR's per-check owner). A GATE
                         detects a raw failure; a FIX CLASS resolves it — chosen by matching the Error
                         (its `kind` for our gates, the TS code in its message for tsc). A class owns
                         the AUTHORITY it injects (the on-disk context that biases toward the correct
                         ROOT CAUSE, not any tsc-greening edit — e.g. a type's real members + which
                         names dominate) + a DIRECTIVE + an optional DETERMINISTIC pre-pass. tsc pins
                         the SITE but underdetermines the REPAIR, so ownership is by AUTHORITY not code.
                         contract-mismatch (field/export/shape) reconciles the CALLER to what exists
                         (its deterministic pass runs reconcile_types include_fields=False, so a field
                         mismatch is NOT laundered into types.ts — it goes to the authority LLM).
                         `default` matches everything + adds no steering = today's generic loop, so an
                         unclassified failure degrades to the status quo, never worse.
      prompts/           spec_draft · plan_game · author_file · fix_file · fix_loop · triage_fix .txt +
                         fix_kinds/<class>.txt (per-fix-class root-cause directives)
      reskin.py          the ASSETS stage (skin the shapes), mode-dispatched: 2D → plan sprites →
                         rewrite draw to prefer kit.sprite(id) w/ shape fallback → render (ComfyUI);
                         3D → plan meshes → tag entities `mesh:"id"` → render image (ComfyUI) → GLB
                         (TRELLIS). Both re-gate then write game/assets/ + assets.json. Additive: no
                         asset ⇒ still passes gates, renders as shapes. CLI `--assets <run_id>`.
      worldgen_bridge.py the WORLD pre-seed: when the frozen spec sets `world`, run_build seeds
                         world.ts from src/worldgen before authoring — the town (heightfield +
                         buildings) inside a WILDERNESS RING (forest trees, 3 POIs w/ set dressing,
                         roads out of the gate, named regions) so the game is a place, not a room.
                         WORLD exports buildings/plaza/gate/grass/pois/regions/road + heightAt.
      run.py             create_run / draft_spec / freeze / run_build / fix_from_note + CLI
                         `python -m maestro.codegen.run [--yes] "<request>"` and
                         `--fix <run_id> "<what's wrong>"` (the human-note fix path)
    agent_loop.py        AgentLoop — the non-LLM executor that DRIVES the module(s): collect each
                         module's get_errors, subtract human waivers, pick the most urgent (error
                         TYPE human>build>fix, then priority, then check rank), ask the module for a
                         Fix, run it through a per-fix Services budget. Keeps completion + cross-fix
                         stall/parking. Engine-agnostic — survived the IR removal verbatim.
    services.py          Services — the bounded gateway a Fix calls through (connector + tool
                         dispatch + pause checkpoint + per-fix step budget; BudgetExhausted is a
                         BaseException so a fix can't churn past its cap). A Check.run fix must call
                         services._report(...) so the loop step counter advances (max_steps bound).
    modules/
      module.py          the Module / Check / Error / ErrorType / CorrectionPrompt ABC (behavior,
                         not a data bag): a module IS a list of Checks (detect → fix); the base
                         sweeps them (get_errors), builds the fix (get_fix / get_correction_prompt).
                         No registry/engine/projection machinery — a module is instantiated directly.
      context.py         Context (durable per-step snapshot) + build_context + render_dict.
    state.py             RunState — durable per-run dir <working_dir>/runs/<run_id>/ (spec.json,
                         game/ folder, waivers); the source of truth each step rebuilds from.
                         Ownership + charge state live in db/, not the run dir.
    run_control.py       cross-thread pause/resume signal channel.
    templating.py        render_template ({{include}} partials + {key} subst) — engine-neutral.
  agents/                MainAgent (chat persona) + agent_store, config/agents/chat.json
  auth/                  identity + access (sqlite at <data_dir>/auth.db): store.py
                         (users + bearer sessions + credit ledger, pbkdf2, token stored as a hash +
                         TTL), deps.py (header-only bearer gate on /api + /auth; static SPA served
                         in the clear), ratelimit.py (per-handle login throttle), router.py (login/
                         logout, NO signup), billing.py (cost(spec), flat 1), credits.py (provider-
                         agnostic top-up seam; default refuses every event), cli.py (manual
                         create/grant/refund). Games are owned (games.user_id in db/; cross-user =
                         403); the WS authenticates via token query param. Credits gate builds: a
                         game is charged ONCE on first enqueue (credits_spent on its games row,
                         which also grants seconds_granted = credits × SECONDS_PER_CREDIT), never
                         re-deducted, never auto-refunded (refunds are a manual admin action). No
                         self-serve signup.
  db/                    platform datastore (sqlite at <data_dir>/platform.db, WAL — data_dir is
                         control-plane state, deliberately NOT under working_directory):
                         store.py — games (ownership, title/mode/status mirror of spec.json,
                         credits_spent + seconds_granted/used compute budget), builds (one row per
                         build/fix/assets attempt: status, steps, queued/started/finished), events
                         (append-only build/spec lifecycle log; GET /games/{id}/events replays it),
                         jobs + workers (the worker-pull inference queue: atomic claim w/ lease,
                         complete debits games.seconds_used + worker busy_seconds in one txn;
                         lapsed lease ⇒ silent requeue, stale completion dropped; workers carry
                         pod_id + terminated_at for the scaler, and queue_stats/live_workers/
                         stale_workers feed it; result binaries never land in the row — the
                         workqueue router decodes a mesh's glb_b64 and each image entry's b64
                         to <data_dir>/blobs/ and stores paths (glb_file / file), today local
                         disk, the S3 seam later). Plain
                         parameterized SQL, short-lived connections — the run dir stays the source
                         of truth for spec + artifacts; rows index, never duplicate.
                         queue_client.py — the enqueue side every producer shares (run_job: land a
                         jobs row, wait for a worker, hand back the row; a timeout comes back as a
                         failed job). LLM, image and mesh all go through it.
  worker/                agent.py — the pull-side worker (python -m worker.agent): long-poll
                         /worker/claim → run the payload through handlers.py → /worker/complete
                         with the result + measured exec_seconds; heartbeats during long jobs,
                         SIGTERM finishes in-flight then exits. Completions ship on a background
                         uploader thread (order-preserving) so the GPU claims the next job while
                         the previous result (a 20MB GLB) is still uploading; the drain runs
                         before deregister so the reaper can't kill a pod mid-upload. Dials OUT only — identical on the
                         home box and a RunPod pod. Auth: the shared workqueue token (never
                         forwarded to the inference target). handlers.py = one handler per payload
                         `kind`, ONE worker process per queue: llm (verbatim forward to llama.cpp),
                         image (the ComfyUI submit → poll /history → fetch /view flow, images back
                         inline as base64), mesh (one TRELLIS POST → glb base64, retry-once).
                         A queue owns its GPU. --idle-exit-seconds (env IDLE_EXIT_SECONDS) is the
                         worker's scale-down decision: the value rides the claim body as the
                         long-poll window, so a null claim MEANS "queue empty that long" →
                         deregister + exit 0 (0 = never, the home-box default). Exit alone never
                         ends a pod — RunPod restarts exited containers and keeps billing — so the
                         entrypoints follow a clean exit with a best-effort in-pod pod DELETE, and
                         the scaler's reaper is the billing guarantee.
  scaler/                the RunPod autoscaler (started by api/app.py when runpod.enabled +
                         api_key + workqueue.enabled). OWNERSHIP SPLIT: workers own scale-DOWN
                         (the idle self-exit above — queue-agnostic, ports to SQS unchanged); the
                         control plane owns scale-UP + pod reaping. stats.py is the SQS seam:
                         QueueStats/WorkerInfo + a StatsSource Protocol, the ONLY scaler module
                         importing db.store — a later SQS move swaps this one source. policy.py =
                         pure decide(), no I/O no clock: reap first (deregistered/stale-worker
                         pods, never-registered pods past boot_deadline), then at most one
                         StartPod per tick — scale-from-zero on ANY pending job (no cooldown),
                         depth ÷ effective workers with booting pods counted (a slow boot can't
                         add-forever), an oldest-pending-age starvation trigger, cooldown +
                         max_workers cap. Only ever touches maestro-<queue>-* pods.
                         runpod_client.py = plain-requests REST (create/list/terminate pod, 404 =
                         success); autoscaler.py = the daemon-thread tick loop (pod age via
                         first-seen tracking; errors logged, never fatal).
  api/                   FastAPI routers (chat, games, agents, system, websocket, billing,
                         workqueue) + build_queue.py (single-GPU FIFO build serializer). The
                         workqueue router (/worker/claim|heartbeat|complete|deregister, mounted
                         OUTSIDE the
                         user gate) is the pull side of the inference queue — token-gated
                         (settings workqueue.token, fail-closed when unset). WS events route
                         per-user server-side (event_bus resolves run → owner). The games router is
                         codegen-only: list/detail/freeze/build/pause/resume/auto-pause/fix/assets,
                         all against maestro.codegen.run; freeze→freeze_spec, build→build_queue→
                         codegen run_build, fix→fix_from_note, assets→reskin.add_assets. Chat drafts
                         specs via tools/chat_tools.py (propose_game_spec/amend_game_spec →
                         codegen.propose_spec/amend_spec). Build progress + spec events emit through
                         tools/build_events.py (_emit → db events log + event_bus). A built run is staged to
                         runtime/games/<id>/ and served at /play (StaticFiles mount) for the SPA.
  config/                settings_schema.py (Pydantic), settings_manager.py (singleton)
  llm_clients/           connector_selector.py, openai_compatible_connector.py, message_builder.py,
                         inference.py (strip_fences). The
                         connector speaks ONLY the OpenAI-compatible Responses API (/v1/responses) —
                         the one local endpoint that honors reasoning.effort. It translates the
                         chat-shaped messages/tools callers pass into Responses input/tools and
                         normalizes the response back to chat shape.
  tools/                 tool_manager, system_tools, comfyui_tools (image backend), file_tools,
                         execution_context (resolve_base_path → the run root).
```

**Inference path (chat / spec draft):** `MainAgent` / `draft_spec` → `MessageBuilder` →
`get_connector()` → `OpenAICompatibleConnector`.
**Inference path (build):** `AgentLoop` → `Module.get_fix` → `Services.infer` → connector. AUTHORING
goes THROUGH the `write` tool (`_author_via_write`, one whole file per call as the `code` arg) — the
tool boundary is what keeps the model from treating the block as a scratchpad (chatter comments, a
second "rewritten" copy of a function that redeclares an export); it falls back to salvaging a tool
call, then a fenced block, so a model that ignores the tool still lands. GATE FIXES run the
read→edit/write subloop where read/edit/write are all real tool calls — a whole quote-heavy file
round-trips fine as a `write` `code` arg (verified). The fix loop drops EDIT once edits keep missing
(force a decisive overwrite) and drops READ once it has read enough without writing
(`_READS_BEFORE_FORCE_ACT`) so a big/corrupt file can't eat every turn in reads while none writes.

**Adding a mechanic:** widen the KIT (`runtime/engine.js` + a `kit_api*.md` section + a worked
example in the prompt + a probe invariant). Generation just composes the new primitive. Adding a
whole game FAMILY = a new primitive family (pathfinding, grid/turn, particles, 3D physics).

**Adding a build capability that isn't a kit primitive:** a new tool in `maestro/codegen/tools.py`
(the fix dispatches it) and/or a new `Check` on `CodegenModule` (a new gate + how to fix it).

---

## Settings & running

**Settings:** `src/config/settings.json` (gitignored). Copy from `settings.example.json`.
- `connector_type: lmstudio`, `lmstudio.base_url` (the local llama.cpp router), `lmstudio.model`.
- `workqueue.enabled` routes ALL GPU work through the worker-pull queue (enqueue a jobs row, wait
  for a worker) instead of calling a backend directly: LLM inference (QueueConnector), sprite/mesh
  images (queue `image`) and TRELLIS meshes (queue `mesh`). `workqueue.token` is the worker bearer
  secret. One worker per queue, and a queue owns its card:
  `python -m worker.agent --server <cp>:8000 --token <token> --queue image --target localhost:8188`
  (defaults: server localhost:8000, target localhost:1234, queue llm). With the queue on, the
  control plane touches no GPU at all. exec_seconds are debited to the owning game via the
  run_scope contextvar set around run_build/fix_from_note.
- `data_dir` (env `MAESTRO_DATA_DIR`, default `<repo>/data`) — where platform.db + auth.db live;
  control-plane state, deliberately not under `working_directory`.
- `runpod.*` — the autoscaler (see `src/scaler/` + docs/DEPLOY.md): `enabled`, `api_key`,
  `network_volume_id`, `cp_url` (the pod-reachable control-plane URL) and per-queue `queues.<name>`
  scaling blocks (template_id, gpu_type_ids, max_workers, thresholds, idle_exit_seconds). The
  `queues` dict in settings.json replaces the default wholesale — carry complete blocks.
- **Model categories** `large`/`medium`/`small` control `message_budget_chars`, `max_iterations`,
  `use_json_mode`. Use `small` for local models.

**Run a build (CLI):** `cd src && python -m maestro.codegen.run "<request>"` (draft → freeze → build).
**Play a build:** open `runtime/index.html?game=<path-or-slug>` in a browser (2D or 3D auto-routed).
**Run backend:** `source venv/bin/activate && python run.py`  •  **Frontend:** `cd frontend && npm run dev`
**Run tests:** `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`

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
Keep CLAUDE.md and docs/ROADMAP.md in sync with reality, in the same commit as the code.

### Comments
Default: none. Only when the WHY is non-obvious (hidden constraint, workaround, subtle invariant).
Never comment WHAT the code does.

### Error handling
Only validate at system boundaries (user input, external APIs, tool results). No defensive fallbacks
for things that can't happen.

---

## Keeping prompts hill-climbable
1. **One `.txt` file per LLM call.** Never inline prompt strings in Python. Each call gets its own
   file under a `prompts/` dir (`maestro/codegen/prompts/`).
2. **Load-bearing system prompts belong in a `.txt` too**, not a hardcoded string.
3. The **kit_api*.md** files are the injected primitive surface — treat them as climbable prompt
   inputs, not docs.

## Small model strategy
Small models aren't dumb — they're easily distracted, following the most recent, most concrete
instruction. The architecture helps: the executor rebuilds a minimal context each step and keeps the
transcript out of the window. Within per-call prompts:
1. **Decompose over one-shot** where a target is large; author + patch beats regenerate-from-scratch.
2. **Output skeleton before field descriptions** — show exact structure first, let the model fill it.
3. **Crafted context per step** — exactly what the call needs (kit surface + spec + current code +
   the failure), never the full transcript.
4. **`model_category: "small"`** — tighter budget, JSON mode.
5. **Reasoning off by default, escalate on stall** — `reasoning: "none"` as the floor; the loop
   flips a stalled fix to `high` (Services escalate) for the rest of that target. Some local models
   only honor on/off — graded efforts collapse to the same budget.
