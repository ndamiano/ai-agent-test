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
2. **Build (stage 2):** a non-LLM **driver** (`maestro/codegen/build_chain.py`) drives the local model to
   author and patch a **folder of TypeScript modules** (`game/main.ts` + system files, a `manifest.json`
   contract) **against the primitive kit** until the local gates pass. Simple games are one file;
   complex ones decompose by system (the model plans the file list first, authors one per step). The
   build is not a resident loop: each llm turn is a job on the `llm` queue, and its completion drives
   the next turn (see **build-as-jobs** below), so the executor holds no state between turns.

**The fat-kit thesis:** breadth comes from the model COMPOSING primitives, not from N per-genre
generators. Every hard/ambiguous mechanic (physics, collision, tilemaps, pathfinding, 3D) is a kit
call, so generation is composition-of-primitives in small chunks — where the gap between a 30B and a
frontier model collapses. Widen the kit to absorb a hard mechanic; never hope the model hand-rolls it.

**Sim/render split (load-bearing law):** the game writes ONLY the sim. `update(dt, input, kit)`
mutates plain state; `hud(kit)` returns overlay DATA. THE GAME HAS NO DRAW HOOK in either mode —
the engine renders `state.world`, drawing each entity from its own `sprite`/`shape`/`color`/`parts`
(`layer` orders, `state.cam` scrolls, `state.tilemap` and `config.backdrop` sit behind). So the sim
runs **headless in pure Node, zero deps** — the local gradient — and render is entirely ours. 2D and
3D are now the SAME contract. Never violate it.

**The local gradient (no frontier critic):** the gates decide "done", not the model, in order:
- **typecheck** (`tsc --noEmit`) — the CONTRACT gate. Catches cross-file/type bugs (missing exports,
  wrong data shapes, bad arg counts, kit misuse) BEFORE the game runs, with file:line attribution.
  Games are checked against `runtime/engine.d.ts` (ambient kit types). This is the deterministic fix
  for a whole class of silent cross-file bugs that no runtime gate can see.
- **headless** — bundle (esbuild) then step the sim N frames, catch crashes/divergence.
- **render** — run the engine's scene render over `state.world` + call `hud()` against a recording
  mock: catch screen-space crashes, malformed HUD data, and a game that defines a draw hook.

**THE GATES DETECT BROKEN, NEVER "BAD"** (the law that decides what may become a gate). A constraint
the model builds to satisfy is only safe when satisfying it IS the goal: "must not crash" can only be
met by not crashing. A constraint of the form "the game must DO X" is not — X is legitimately false
for some games, so no edit satisfies it and the loop grinds to its cap while the model contorts the
design to appease it. The `probe` (dead_action / unbound_control / dead_movement / premature_end /
solid_overlap / player_not_in_world) and the `scroll` camera gate were exactly that shape and are
GONE: measured over 278 build attempts they cost 13% of all build steps and killed 8 builds at the
cap, in exchange for a benefit never once verified — nobody ever played the games they blocked. The
render gate's `missing_draw`/`draw_blank` halves went with them (an idle/menu/text game legitimately
paints no world, and the 3D scene was never exercised by that mock anyway). What is left detects
crashes and contradictions. Playability is a HUMAN judgement — "the character doesn't move" is
obvious to a person and near-impossible for code — so the gap stays visible rather than filled with a
proxy. The spec-vs-code audit is the closest thing to a "done" signal: it judges claims the SPEC
made, not invariants we invented.

"Done" = the artifact passes the gates, never the model claiming done. Each gate sweep rebuilds a
minimal context from durable on-disk state (the frozen spec + the failing file + the failing check's
message), so context stays ~constant and the transcript is never used as memory. A gate fix is a
bounded read→edit **subloop** (a `build_steps` shape, one turn per llm job): the model reads whatever
sibling bodies it needs on demand — exposing a CROSS-FILE mismatch the signatures can't show (e.g.
main.ts assumes world.ts spawns the player but none does) — then lands atomic multi-hunk `edit`s it
self-selects (a signature change ships with its call-site hunks in the same completion). The reads
live in the fix's transcript (in the durable cursor, confined to that one fix); the outer loop
re-gates after. Bounded by a per-shape turn cap + the global step cap; on cross-fix stall the read
tool is dropped so the fix must ACT. Overwrites after creation are refused (`write` is create-only): whole-file rewrites were
the fix loop's dominant failure mode — destabilizing previously-correct code — so a fix is grounded
and local by construction.

---

## Architecture

```
runtime/                 The primitive KIT (hand/frontier-authored offline, run local)
  engine.js              kit v1: clamp, entities/spawn/cull, integrate(+3), aabb,
                         tilemap, walk/jump/physics, collideWorld (the ONE 2D solid pass: tile
                         pushout + solid-pair separation; entities tag `solid: true`),
                         register/bindings (named key-press actions — fired on the pressed edge
                         after update), camera, input, run() (browser 2D),
                         simulate() (headless sim).
                         integrate3 + z for 3D; 3D steering (seek3/flee3/wander3/patrol3 +
                         avoidRects building collision); wallsFromTilemap (a walled 3D level —
                         ground + wall boxes + the collision rects — in ONE call, so a dungeon is
                         never half-built). DEPTH primitives: talkOpen/talkStep/talkHud
                         (the whole dialogue/shop loop; `state.talk` is TYPED, so a game inventing a
                         member of it fails typecheck instead of painting "undefined" on screen),
                         kit.quest (add/complete/log — milestones
                         that do NOT end the game; win/lose reserved for the spec's ending),
                         kit.notify (engine-drawn toasts). focus/facing — WHAT the ONE `activate`
                         key acts on (facing cone + reach + nearest, on either plane): entities opt
                         in with `action`/`label`, the engine draws the prompt naming the REGISTERED
                         key, and the game writes only the verb's effect (onActivate). Targeting was
                         hand-written per verb before, and a build let the farmer tend a cow standing
                         behind him. Boxed HUD items (panel/menu) STACK per anchor — placing each by
                         anchor alone drew a choice menu over the line it was answering. run() preloads the game's assets.json
                         sprites. renderScene() draws the 2D world (entities by `layer`, the
                         tilemap, config.backdrop) — the game has NO draw surface at all: no draw
                         hook, no DrawApi, no sprite call. An entity is visible because it is in
                         state.world and carries its own look; screen art is a {kind:"icon"} HUD
                         item. A draw-any-image-anywhere call carries no intent, so a wrong id
                         painted nothing and no gate could see it — measured: one build shipped a
                         black screen with 1303 no-op draw calls per frame.
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
  kit_catalog.md         the SPEC-level kit surface (the only prompt input `spec_draft` gets): the
                         control schemes, behaviors and depth blocks a spec may compose — plus the
                         CANNOT list, which is the only thing standing between a request and a spec
                         that promises what the runtime has no way to deliver. A live request asked
                         for beat-synced scoring and a synthwave soundtrack, and the kit answered
                         with a no-op `audio.play` stub: the whole game was unbuildable and nothing
                         downstream could see it. The stub is GONE — there is no audio call to make,
                         so a game reaching for one fails typecheck instead of shipping silence it
                         believes is sound. Widening the kit means widening this too, or specs
                         never reach it.
  headless.mjs / render.mjs   node runners for the two runtime gates
  index.html             browser harness (loads games/<slug>/main.js bundle; 3D → run3d else run)
  games/, specs/         sample games + specs (fixtures/reference)
  node_modules/          runtime toolchain (typescript + esbuild; gitignored)

src/
  maestro/
    codegen/             THE build path (replaces the deleted IR):
      gates.py           typecheck (tsc → per-file errors) · build_bundle (esbuild main.ts → main.js
                         + inline sourcemap) · run_headless/run_render (build then run the bundle
                         with --enable-source-maps, so a crash stack names the .ts source).
      tools.py           write(code, file — CREATE-ONLY, an existing non-empty file refuses with its
                         body so the turn converts to an edit) / edit(file, edits) — ATOMIC multi-hunk
                         (every hunk validated against the original body: found, unique, no overlap —
                         all land or none, one version bump; an exact-anchor miss falls back to
                         whitespace-tolerant line matching — measured 668 misses were mostly
                         indentation drift on otherwise-correct hunks — splicing at the file's REAL
                         text, still unique-or-fail) / read_file (whole file, or offset/limit
                         line window; per-file .ts, path-safe — a slice does NOT ground an edit).
                         edit refuses any file whose first line starts `// GENERATED` (the control
                         scaffold, data.ts, worldgen's world.ts), pointing at the owning source. The
                         fix loop pastes NO reference material: the kit surface (engine.d.ts), the
                         state contract (state.ts) and the data tables (data.ts) are FILES, and the
                         fix reads the ones it decides it needs.
      interfaces.py      THE INTERFACE CONTRACT (runs/<id>/interfaces.json) — the architecture the
                         MODEL declares for itself before any code exists: every state field with its
                         LIFETIME (run|session|level|turn) + the ONE function that may REPLACE it +
                         who may mutate it in place, every function with its file/signature/reads/
                         writes/calls, and the whole-game invariants. manifest.json is DERIVED from
                         it (functions grouped by their `file`; GENERATED files dropped, the entry
                         hook appended with the scaffold's hooks) — nothing plans the file list.
                         The state table is ALSO emitted as GENERATED `game/state.ts` (one
                         `GameState` interface, lifetime+owner as doc comments), so tsc enforces the
                         architecture natively — the same move data.ts makes for the data manifest.
                         Without it the declaration is advisory: a measured build authored room.ts
                         correctly against the state table, then authored game.ts LAST with its own
                         invented GameState, and burned 44 steps on a mismatch neither file owned.
                         An empty architecture is never written (it declares no game), so
                         `interfaced` stays red and the turn re-runs. THE KIT'S LAW: the entry hook
                         signatures are not the architecture's to choose — main.ts is GENERATED and
                         calls them with fixed arity, and there is no rendering function to declare
                         at all. So `hooks_block` injects the hook signatures + the kit's type
                         vocabulary (both PARSED from engine.d.ts, the same types tsc checks
                         against) into the design turn, and `enforce_kit_contract` — run inside
                         `save`, so no path can bypass it — overwrites a hook signature the model
                         invented and rewrites DOM draw types to DrawApi. Without it a measured
                         build declared `draw(state, ctx: CanvasRenderingContext2D)`, authored
                         render.ts faithfully to it, and burned its whole step cap on tsc blaming
                         game.ts — the one file that was right. It also APPENDS the state the seeded main.ts asserts or
                         dereferences unguarded (`scaffold.unguarded_state_fields` reads it off the
                         file on disk — today `player: Entity`, owned by init; `state.world ?? []`
                         and `if (state.ground)` are optional by construction and never forced).
                         Without it a measured build declared no `player`, so GENERATED state.ts had
                         none, every file invented its own local widening of GameState, and the build
                         died at its step cap on TS2339 in the one sibling whose widening was
                         missing it. `review_log.jsonl` records
                         what each round found + the ops it landed/rejected — the review patches in
                         place, so nothing else can answer "which contradiction did it fix?". Owns
                         the artifact, the review's PATCH OPS (replace/delete/add state|function +
                         set_invariants — re-emitting a 28KB architecture to change one entry blows
                         the token cap), the review's function-slicing, and the prompt renderings
                         (state table + signatures) authoring/fix turns read.
      conform.py         the CONFORMANCE gate: does the code obey the contracts the MODEL declared?
                         Every rule comes out of interfaces.json — no game knowledge, so a violation
                         is only ever the model contradicting itself. OWNERSHIP (a function replaces
                         a field it doesn't own) · LIFETIME (a run-lifetime field replaced, so
                         progression is destroyed) · ELEMTYPE (a value out of a T[] pushed into a U[]
                         — silent at runtime: the reader gets undefined and draws nothing) · MISSING
                         (a declared function nothing implements) · UNEXPORTED (a declared function
                         implemented WITHOUT `export`, so the file the architecture says calls it
                         cannot import it — MISSING alone is satisfied by a local, and the gap only
                         shows up as tsc blaming the CALLER for a name the callee owns). tsc sees
                         none of the others: every one
                         is type-correct code that wires the game wrong. Only REPLACEMENT of a
                         CONTAINER counts — `x = x.filter(…)` is an update, and scalars are
                         recomputed constantly. GENERATED files are skipped.
      module.py          CodegenModule = the GATE LIST (detection only): interfaced → reviewed →
                         data → authored → contracted → typechecks → conforms → single_mover → runs →
                         renders (blocking where noted; single_mover is a STATIC
                         check — a scaffolded game re-driving the player from input double-moves it,
                         and no runtime gate can see that). Authoring order keys entry-last on
                         game.ts. Each error's FIX is owned by the DRIVER, routed by `Error.code` —
                         the checks carry no `run`. Also holds the shared prompt-building helpers +
                         tool schemas build_steps imports.
      build_chain.py     THE build DRIVER — what a finished build llm turn does next (codegen analog of
                         asset_chain). A build is a linear chain of `llm` jobs, each tagged
                         metadata.stage="build"; /worker/complete routes here. Owns the two-level state
                         machine the old resident AgentLoop was: OUTER (rebuild context from disk, sweep
                         CodegenModule's gates, cross-fix stall/park bookkeeping, pick the top error,
                         START its fix — and when the gates are GREEN, run the spec-vs-code audit
                         (audit.py) before finalizing, fixing failed claims one per iteration) and FIX
                         (a build_steps shape; apply the turn, enqueue the next or return to outer).
                         The outer sweep also fires the EARLY asset lane (reskin.start_assets_early,
                         once the data lands — batch id on the cursor), an ok finalize absorbs
                         mid-build meshes (box-fit + rebundle) then fires the GREEN lane
                         (reskin.auto_skin on a thread), and a build turn refused for compute
                         PREEMPTS the run's still-pending asset jobs before giving up — gameplay
                         beats skin. advance() runs ALL local work — gates, tool dispatch,
                         deterministic fix passes — synchronously and SUSPENDS only at a real inference
                         (enqueue one llm job + return; the process is free to die). Never more than one
                         build turn in flight per run, and advance runs only in the control-plane
                         process (completion handler + reaper), so an in-process lock serializes them.
                         kickoff/start_build/resume/is_active/status_of are the API/CLI entry points;
                         a refused compute budget or the step cap finalizes (stage_for_play + build_done).
      build_state.py     the durable build CURSOR (runs/<id>/build_state.json) — everything AgentLoop
                         held in memory, on disk: phase (outer|fix|done), step count, the cross-fix
                         stall/park snapshots, the current fix's shape + growing transcript, and the
                         read→edit tool grounding (versions/seen) rehydrated into build_codegen_tools
                         each completion (a fresh process would else refuse a resumed edit). Job
                         metadata carries only {stage,run_id,build_id}; this file is the single source
                         the completion reloads, advances, rewrites.
      build_steps.py     the per-shape fix MACHINES — the old synchronous fix bodies re-expressed as
                         resumable steps: step(spec, run_dir, tools, fix_cursor, result) -> Infer|Done.
                         interfaces / review / data / author / amend / read_write (the read→edit
                         subloop). The control flow is inverted from "call infer + use the
                         return" to "return the request, resume with the result". The read_write
                         shape runs the fix class's DETERMINISTIC pre-pass (via build_chain) before
                         its first turn.
                         INTERFACES = one turn declares the architecture (interfaces.py), and the
                         manifest falls out of it deterministically.
                         REVIEW = the model reviews the declarations it just wrote, before any code
                         exists: rounds of (find over function slices) → (patch by op, one retry on
                         unapplicable ops). Converges when a round reports nothing NEW — problem keys
                         dedupe across rounds, since each round re-reads the same architecture. The
                         round cap is the backstop for a reviewer that keeps inventing work.
                         AMEND = a conformance violation is the code disagreeing with a contract
                         written before the code existed, so one turn RULES on which side is wrong:
                         `contract` patches interfaces.json and the fix is over, `code` falls through
                         to the read→edit subloop. Without it `conforms` would be exactly the
                         unsatisfiable gate the model can neither fix nor argue with. It is also
                         where a STALLED read→edit fix is routed (once per error identity, after the
                         deterministic pre-pass): any gate can be failing because a file was authored
                         FAITHFULLY to a wrong declaration, and editing that file forever cannot fix
                         it — amend is the only shape allowed to say the contract is the bug.
                         DATA = the model designs per-game datasets ONCE (design_data.txt; EVERY
                         game owes at least one — an empty design leaves the gate red and re-runs the
                         turn with the rejection quoted back. Rows are what make content editable,
                         typed and DETERMINISTICALLY skinnable: their `look` prompts are the art
                         plan, so a data-less game paid two model calls and a re-gate for art it
                         could have had free), then deterministic row
                         validation + typed data.ts regeneration (data_files.py); authoring/fix prompts
                         carry a GAME DATA summary (schema + ONE example row), never the rows.
                         AUDIT = the terminal shape: one llm turn judges the source against the frozen
                         spec's claims (see audit.py).
      audit.py           the spec-vs-code AUDIT — the gates prove a game RUNS, not that its declared
                         mechanics exist, so a build ends on SPEC-EXHAUSTED, not errors-zero. Claims
                         are enumerated MECHANICALLY from the spec (controls/mechanics/win/lose;
                         movement controls excluded — scaffold law). `render` is NOT a claim: it is a
                         look description the skin stage rewrites, so "delivered" is a taste verdict.
                         Nor is a mechanic RESTATING the win/lose field — 66% of measured specs
                         re-promise an ending inside `mechanics`, and one promise judged twice yields
                         two verdicts free to contradict, after which every fix for one breaks the
                         other; the dedicated field is the authority (ending vocabulary AND ≥0.5 Dice
                         overlap on stemmed significant words, so a mechanic that merely mentions an
                         ending while adding an uncovered rule survives).
                         Each claim is judged by a bounded READ→VERDICT subloop (the
                         fix loop's grounding transplanted): the judge reads the files it needs via
                         read_file and must cite the traced path — single-shot judging over pasted
                         sources was measured wrong BOTH ways on the same code (unanimous "broken"
                         on a working mechanic; delivered swinging 11/12→5/12 between sweeps) and
                         tracing corrected both. Read cap forces the verdict (empty toolset); a
                         claim with no verdict inside its turn cap is SKIPPED, never a finding.
                         Verdicts append to runs/<id>/audit_verdicts.jsonl (durable — "which claim
                         failed?" must never be unanswerable). ANCHORING: delivered claims carry
                         into later rounds and across builds (audit_delivered on the cursor); an
                         anchored claim only flips with cited regression evidence, so rounds ratchet
                         (measured: unanchored 5→7→3→2, anchored 4→4→6→9). Failed claims become
                         human-note-shaped fixes on the fix_from_note lane, then re-gate → re-audit
                         until a round returns zero findings (round + step caps as backstops). Every
                         failure path FAILS OPEN to a finished build — a game that never finishes is
                         worse than an incomplete one that ships. CLI: run.py --audit <run_id>.
      fix_classes.py     the error-class → fixer MAP (codegen analog of IR's per-check owner). A GATE
                         detects a raw failure; a FIX CLASS resolves it — chosen by matching the Error
                         (its `kind` for our gates, the TS code in its message for tsc). A class owns
                         the AUTHORITY it injects (the on-disk context that biases toward the correct
                         ROOT CAUSE, not any tsc-greening edit — e.g. a type's real members + which
                         names dominate) + a DIRECTIVE + an optional DETERMINISTIC pre-pass. tsc pins
                         the SITE but underdetermines the REPAIR, so ownership is by AUTHORITY not code.
                         contract-mismatch (field/export/shape) reconciles the CALLER to what exists
                         (its deterministic pass runs reconcile_types include_fields=False, so a field
                         mismatch is NOT laundered into types.ts — it goes to the authority LLM;
                         EXCEPT the dominance-gated append: a field used ≥2× on a game-owned type
                         with no near-miss member is the types.ts-authored-first-couldn't-predict-it
                         shape — the top measured TS2339 class — and is appended deterministically).
                         missing-name (TS2304/TS2552, the top measured tsc code ×299) — deterministic
                         import insertion when exactly ONE sibling exports the name; authority block
                         distinguishes import-it vs thread-the-hook-parameter vs define-it-locally.
                         contract-assert (append the pipeline's own known assertion line) and
                         single-mover (strip the redundant input-driven mover) are deterministic-only
                         — a class whose repair the pipeline can compute spends no LLM call.
                         `default` matches everything + adds no steering = today's generic loop, so an
                         unclassified failure degrades to the status quo, never worse.
      data_files.py      the DATA-FILE substrate: game/data/manifest.json declares per-game
                         datasets (field vocab number/string/boolean/arrays/ref:<dataset>, "?" =
                         optional; every row carries an implicit envelope id/name?/look?/presence?/
                         size?/shape?/color?/parts? — pipeline fields, nullable so no-asset still
                         renders as shapes. size/shape/color/parts + the id ARE the row's whole
                         VISUAL: kit.spawnData builds the entity from them and binds the row id as
                         its asset id, so the skin stage needs no source rewrite; `parts` keeps a
                         COMPOUND look (hull+fin, eyes) in data, so hand-drawn art stays skinnable),
                         game/data/<name>.json holds flat rows. Owns validate_data (type/id/ref/
                         envelope violations as FIX errors → one-shot rows rewrite via fix_data.txt),
                         the GENERATED typed game/data.ts (marker-protected like world.ts; games
                         import it, tsc typechecks content natively), the GAME DATA prompt summary,
                         and the deterministic sprite/mesh plan from rows.
      prompts/           spec_draft · design_interfaces · review_find · review_patch ·
                         amend_contract · author_file · fix_file · fix_loop · triage_fix ·
                         design_data · fix_data .txt +
                         fix_kinds/<class>.txt (per-fix-class root-cause directives)
      reskin.py          the ASSETS stage (skin the shapes) — NO CLICK: assets start the moment
                         their inputs exist. EARLY lane (start_assets_early, fired from the build's
                         outer sweep once the data rows land): the rows' `look` prompts ARE the
                         plan, so the GPU renders sprites/meshes DURING the build; renders key on
                         row ids so art rendered before the source binds it is never wasted.
                         GREEN lane (auto_skin, fired by every ok build finalize): no early batch →
                         the classic full skin below; early batch → only what's missing — WIRING
                         (unbound hand-drawn/hand-spawned games get the rewrite) + a 3D top-up of
                         mesh ids tagged during authoring. A per-run guard (AlreadySkinning)
                         serializes all skin entry points. Mode-dispatched: 2D → plan sprites →
                         rewrite draw onto kit.drawEntity (shape fallback is the kit's) → render (ComfyUI);
                         3D → plan meshes → tag entities `mesh:"id"` → render image (ComfyUI) → GLB
                         (TRELLIS). The TAGGING rewrite is skipped entirely when the plan came from
                         data AND the source binds through the kit (_binds_data_assets: spawnData,
                         plus drawEntity in 2D) — a data-driven skin
                         spends ZERO LLM calls; a hand-drawn/hand-spawned game still gets the
                         rewrite or its art would be orphaned.
                         The plan is DETERMINISTIC whenever any data row carries `look`
                         (sprite_plan_from_data: 2D all look rows, 3D look + presence world/both;
                         prompts from look, sizes from size — no 3-8 sprite cap); the LLM plan is
                         the data-less fallback. Both re-gate, then write assets.json (a pure
                         function of the plan) and START THE CHAIN: start_asset_chain enqueues
                         EVERY image job at once as one batch and returns. Nothing waits on a GPU.
                         Additive: no asset ⇒ still passes gates, renders as shapes. CLI
                         `--assets <run_id>` (which does block — a CLI has no socket to report on).
      asset_chain.py     what a finished asset job does NEXT — the names in its `metadata.then`:
                         a CONTINUATION to enqueue (mesh_from_image: the PNG a TRELLIS job turns
                         into a GLB), OPERATIONS on this result (save_sprite / decimate), and the
                         batch's FINALIZE (fit_building_boxes + bundle + stage_for_play +
                         assets_done). This module owns those names so the queue stays a generic
                         transport that never learns what an asset is. WHY: the stage used to be a
                         thread parked on a 250ms poll for the whole render — queue depth never
                         exceeded 1, so the scaler's depth rule could never fire and a worker
                         idle-exited with the next job seconds away.
      scaffold.py        the CONTROL SCAFFOLD pre-seed: for EVERY game, run_build seeds a
                         GENERATED game/main.ts from scaffold_templates/<scheme>.ts.tmpl before the
                         loop. The scaffold owns config (2D size defaults; 3D controls: name +
                         background — sky for a world game, since background also tints the 3D fog),
                         the scheme's ONE-correct-realization movement (moveTopDown /
                         walk+jump+physics / gridMove-on-pressed w/ state.passable / kit.drive — run
                         BEFORE the hook update so gameplay adjusts after, never re-wires), the
                         state.player init assert, the solid-collision pass (kit.collideWorld AFTER
                         the hook update: movement → gameplay → collide; 2D templates only), the 3D
                         per-frame passes state declares in init (state.walls → kit.avoidRects,
                         state.ground → stand the player on that height fn) — and on a WORLD game
                         main.ts imports world.ts and sets BOTH itself (heightAt / WORLD.buildings),
                         since world.ts is the pipeline's own file with exactly one correct wiring:
                         a measured build used the whole WORLD API correctly yet never assigned
                         them, so the player walked through buildings in mid-air and no gate could
                         see it. A hook that sets its own wins (the scaffold only fills a blank).
                         And — when the spec has any verb aimed at a THING (a depth block, or a
                         control matching controls.ACTIVATE_WHAT) — the ONE `activate` key: kit.focus
                         each frame, the kit talk loop (talkStep update-side, passed the activate key
                         so a conversation is advanced by the key that opened it), and a registered
                         "activate" action bound to the SPEC's key, not a hardcoded E, registered
                         BEFORE the hook init so a game registering its own replaces the default
                         (register replaces by name; a measured build bound interact to space
                         correctly and the scaffold's post-init E register clobbered it, silently
                         unbinding the spec's own key). It DISPATCHES: a talkable target opens
                         itself, everything else goes to the game's onActivate(state, target, kit)
                         with the target already chosen; choice → state.talkPick. WHY one key: the
                         verb the player wants is unambiguous from what they are facing, and a spec
                         spending four keys on talk/use/tend/sleep gave the player four things to
                         remember, the build four bindings to get right, and one of them (Tab) the
                         browser takes anyway. The model
                         authors the hooks in game.ts (createState/init/update/hud [+ onActivate]). WHY:
                         two live builds shipped dead controls out of model-authored glue (one never
                         read a movement key, one zeroed the wired movement every frame). `mode` (not
                         the scheme name) decides 3D-ness, so an unknown scheme on a 3D spec lands on
                         orbital-3d rather than the 2D default. Seeded only when main.ts is absent.
      scaffold_templates/ the per-scheme scaffold sources (top-down/platformer/grid-turn/
                         orbital-3d/vehicle-3d/first-person-3d/follow-3d + default + the activate
                         partials, one pair for both modes since kit.focus works out the plane
                         itself) — real TypeScript we own, hill-climbable like prompts.
      worldgen_bridge.py the WORLD pre-seed: when the frozen spec sets `world`, run_build seeds
                         world.ts from src/worldgen before authoring — the town (heightfield +
                         buildings) inside a WILDERNESS RING (forest trees, 3 POIs w/ set dressing,
                         roads out of the gate, named regions) so the game is a place, not a room.
                         WORLD exports buildings/plaza/gate/grass/pois/regions/road + heightAt.
                         generate_best's candidate seeds are OFFSET BY run_id — it keeps the first
                         of any scoring tie, so a fixed candidate list handed every same-size/biomes
                         game the identical village (two live runs: byte-identical world.ts).
                         Keyed on the run, not a clock, so it stays reproducible from the run dir.
                         CONTENT only: it owns the PLACE, the scaffold owns the CONTROLS, and the
                         model authors game.ts on top of both. Seeded only when world.ts is absent.
      controls.py        the spec's CONTROL VOCABULARY: normalize_controls maps every control name
                         onto a key the runtime can BIND — single lowercase chars (`pressed` matches
                         the keymap exactly, so "W" is a control nothing can press), Arrow*,
                         Escape/Shift/Enter — NOT Tab, which the browser spends on focus, so a spec
                         binding it ships a key that opens the address bar (both runners also
                         preventDefault space/arrows/Tab). Word forms (Spacebar/ESC/Up Arrow), gamepad names
                         (stick→W/A/S/D, right stick→Mouse, A/B/X/Y→e/q/f/r), alternates
                         ("Right Click / Key Q"→q) and movement aggregates (WASD, W/Up, Arrow Keys →
                         the W/A/S/D sentinel, since movement is the scaffold's). The mouse is
                         SCHEME-AWARE: first-person-3d/orbital-3d own it and keep the Mouse
                         sentinel, every other scheme has no mouse input at all so the action lands
                         on a free key — and since there is ONE pointer, a second mouse action takes
                         a key too rather than overwriting the first. An unbindable control cannot
                         be wired to anything and prose cannot fix it — measured over 112 real
                         specs, 52% of non-movement entries named a key that does not exist, 45 of
                         them mouse entries two hard spec_draft rules already forbade. So map the
                         vocabulary, never forbid it and never DROP an entry — ANY collision rehomes
                         to a free key (two entries resolving to one key used to leave the last
                         writer holding it and the other verb gone), except the movement/mouse
                         sentinels, where several spellings MEAN one binding.
                         Then _collapse_activate MERGES every control matching ACTIVATE_WHAT onto ONE
                         key: kit.focus already decides which verb applies from what the player
                         faces, so the spec only ever needed one — and the merged text keeps every
                         promise, so the audit still judges them all. A self-verb (jump/shoot/dash/
                         cycle tool) has no target and keeps its own key. Called at the SPEC
                         boundary (draft + freeze) so the human review, the prompts and the scaffold
                         all read the same key.
      run.py             create_run / draft_spec / freeze / run_build (CLI: kickoff + block-poll the
                         cursor) / fix_from_note + CLI `python -m maestro.codegen.run "<request>"`
                         and `--fix <run_id> "<what's wrong>"` (the human-note fix path). The web
                         build/fix path is fire-and-forget through build_chain.kickoff, not run.py.
    services.py          the two LLM tool-call PARSERS the fix shapes share: parse_args (any argument
                         shape → dict) + salvage_tool_call (rebuild a call from content JSON when it
                         uniquely fits one offered tool). The old Services gateway + AgentLoop that
                         owned the synchronous fix loop are gone — the build is a chain of llm jobs
                         driven by build_chain, so nothing dispatches "through Services" any more.
    modules/
      module.py          the Module / Check / Error / ErrorType ABC (DETECTION contract): a module IS
                         a list of Checks; the base sweeps them (get_errors), honouring blocking + a
                         when_clean terminal tier. The FIX for each error is owned by the build driver
                         (build_chain → build_steps), keyed off Error.code — the Check carries only
                         detect + sweep flags. No registry/engine/projection machinery.
      context.py         Context (durable per-sweep snapshot) + build_context.
    state.py             RunState — durable per-run dir <working_dir>/runs/<run_id>/ (spec.json,
                         game/ folder, waivers); the source of truth each step rebuilds from.
                         Ownership + charge state live in db/, not the run dir.
    run_control.py       cross-thread pause/resume signal channel.
    templating.py        render_template ({{include}} partials + {key} subst) — engine-neutral.
  agents/                MainAgent (chat persona) + agent_store, config/agents/chat.json
  auth/                  identity + access (sqlite at <data_dir>/auth.db): store.py
                         (users + bearer sessions + credit ledger, pbkdf2, token stored as a hash +
                         TTL), deps.py (header-only bearer gate on /api + /auth; static SPA served
                         in the clear; /play cookie-gated — the static game harness can't attach a
                         header to its <script>/<img> sub-resource fetches, so login mints a
                         `maestro_play` httponly cookie scoped Path=/play (never touches the API's
                         header-only model), and the gate ownership-checks /play/games/<id>/*;
                         require_credits = that gate PLUS a positive balance, for
                         inference that is never charged but must not be free to everyone),
                         ratelimit.py (per-handle login throttle), router.py (login/
                         logout, NO signup), billing.py (cost(spec), flat 1), credits.py (provider-
                         agnostic top-up seam; default refuses every event), cli.py (manual
                         create/grant/refund). Games are owned (games.user_id in db/; cross-user =
                         403); the WS authenticates via token query param. Credits gate builds: a
                         game is charged ONCE on first enqueue (credits_spent on its games row,
                         which also grants seconds_granted = credits × SECONDS_PER_CREDIT), never
                         re-deducted, never auto-refunded (refunds are a manual admin action).
                         CHAT is charged NOTHING — it runs before a game exists to bill, and
                         metering it would make an abandoned conversation cost real money — but
                         POST /api/chat rides require_credits, so a zero balance can't draft specs
                         it could never build. DELETE (clear session) stays ungated: housekeeping
                         must work when broke. No self-serve signup.
  db/                    platform datastore (sqlite at <data_dir>/platform.db, WAL — data_dir is
                         control-plane state, deliberately NOT under working_directory):
                         store.py — games (ownership, title/mode/status mirror of spec.json,
                         credits_spent + seconds_granted/used compute budget), builds (one row per
                         build/fix/assets attempt: status, steps, queued/started/finished), events
                         (append-only build/spec lifecycle log; GET /games/{id}/events replays it),
                         jobs + workers (the worker-pull inference queue: atomic claim w/ lease,
                         complete debits games.seconds_used + worker busy_seconds in one txn;
                         ONLY DELIVERED WORK IS BILLED — a failed job, a lapsed-lease duplicate and
                         an abandoned job all leave seconds_used untouched, while busy_seconds
                         moves in every case (it measures the GPU time WE pay for, real whether or
                         not the user got anything);
                         lapsed lease ⇒ silent requeue, stale completion dropped; workers carry
                         pod_id + terminated_at for the scaler, and queue_stats/live_workers/
                         stale_workers feed it; result binaries never land in the row — the
                         workqueue router decodes a mesh's glb_b64 and each image entry's b64
                         to <data_dir>/blobs/ and stores paths (glb_file / file), today local
                         disk, the S3 seam later). THE COMPUTE BUDGET lives on enqueue_job: a job
                         with a game_id is ADMITTED against grant − seconds_used − the est_seconds
                         of that game's pending/claimed jobs, all in ONE write txn (BEGIN
                         IMMEDIATE), else InsufficientCompute. Reserving the estimate is the whole
                         point — a build enqueues far faster than workers finish, so measured spend
                         alone reads near-zero right up to the moment a hundred queued jobs land.
                         abandon_job releases a reservation the enqueuer stopped waiting on (a
                         late completion is then dropped like a lapsed lease). Overdraw is possible
                         by design: a job that runs longer than its estimate is never killed
                         mid-flight, the reservation only bounds how far. A job also CHAINS: its
                         `metadata` (control-plane only, never handed to a worker) carries the
                         follow-up job + the ops + the batch finalize, and complete_job lands the
                         follow-up INSIDE the completion txn — inheriting game_id/build_id/batch_id,
                         so a continuation nothing enqueued in a run_scope still debits the right
                         game. It then counts the batch's remaining pending/claimed AFTER that
                         insert (inverted, the last image completion would finalize a batch whose
                         mesh jobs don't exist yet) and reports batch_complete to exactly one
                         completer. Plain
                         parameterized SQL, short-lived connections — the run dir stays the source
                         of truth for spec + artifacts; rows index, never duplicate.
                         estimates.py — per-queue estimated seconds, the admission input (a
                         hill-climbable constant, not a measurement).
                         reaper.py — the housekeeping daemon (5s tick, started unconditionally by
                         api/app.py): requeue lapsed leases even when no claim arrives to trigger
                         it, fail never-claimed pending jobs to release their reservations (this
                         replaces the enqueuer timeout for chained jobs — no clock starts until a
                         job is real work), finalize any batch whose live completion was lost
                         to a restart, and RE-ADVANCE any stuck build (status building, no build llm
                         turn pending/claimed, its last turn terminal past a grace) — the backstop for
                         a build driver that died between a completion and the next enqueue, or a turn
                         that failed as stale-pending so no /worker/complete ever advanced the chain
                         (advance's per-run lock makes the re-drive a no-op if a live completion beat
                         it). NOT in the scaler: that only exists when runpod is configured, and
                         scaler/stats.py is deliberately the only scaler module touching db.store.
                         queue_client.py — the enqueue side every producer shares (run_job: land a
                         jobs row, wait for a worker, hand back the row; a refused budget or a
                         timeout comes back as a failed job, so callers branch on one shape). LLM,
                         image and mesh all go through it — it is the ONLY transport to a GPU, with
                         no direct-call fallback, which is what makes one gate sufficient. A job's
                         game comes from the run_scope contextvar; jobs with no game (chat, spec
                         drafting) are platform cost — neither metered nor budget-gated here, so
                         the balance check that fronts them lives at the API edge instead
                         (auth.deps.require_credits on POST /api/chat).
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
                         inline as base64), mesh (one TRELLIS POST → glb base64, retry-once; the
                         reply's stage/load/warmup/generate headers ride back on the job row,
                         since a pod's stdout is unreachable and cold starts would otherwise only
                         ever be inferred).
                         A queue owns its GPU. --idle-exit-seconds (env IDLE_EXIT_SECONDS) is the
                         worker's scale-down decision: the value rides the claim body as the
                         long-poll window, so a null claim MEANS "queue empty that long" →
                         deregister + exit 0 (0 = never, the home-box default). Exit alone never
                         ends a pod — RunPod restarts exited containers and keeps billing — so the
                         entrypoints follow a clean exit with a best-effort in-pod pod DELETE, and
                         the scaler's reaper is the billing guarantee.
  scaler/                the RunPod autoscaler (started by api/app.py when runpod.enabled +
                         api_key). OWNERSHIP SPLIT: workers own scale-DOWN
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
                         workqueue). There is NO build queue any more — a build is a chain of llm
                         jobs on the shared `llm` queue (build_chain), so builds no longer serialize
                         behind one another on a single thread; they only contend for llm workers,
                         which autoscale. The
                         workqueue router (/worker/claim|heartbeat|complete|deregister, mounted
                         OUTSIDE the
                         user gate) is the pull side of the inference queue — token-gated
                         (settings workqueue.token, fail-closed when unset). /complete is
                         where every CHAIN advances, dispatched on metadata.stage: an ASSET job
                         offloads its blobs, builds the follow-up from the parent's `then`, lands both
                         in one txn, and fires the ops + batch finalize; a BUILD turn hands off to
                         build_chain.on_completion (reload cursor → advance → enqueue the next turn or
                         finalize). Both run FORGOTTEN off the event loop (to_thread — bounded CPU for
                         assets, the whole next gate-sweep for a build; the worker's response must not
                         wait, and a bare create_task would stall every other completion). WS events
                         route
                         per-user server-side (event_bus resolves run → owner). The games router is
                         codegen-only: list/detail/freeze/build/pause/resume/auto-pause/fix/assets
                         (+ asset manifest/blob GETs and per-asset regenerate);
                         freeze→freeze_spec, build/fix→build_chain.kickoff (fire-and-forget, off the
                         event loop), resume→build_chain.resume (re-drive the durable cursor),
                         assets→reskin.add_assets (a MANUAL re-skin — builds
                         now skin themselves via the early/green lanes; its own
                         thread, now only for the plan+gate half — the render outlives it on the
                         queue, so assets_done and build_finished are the FINALIZE's job, and a
                         second skin is refused by has_active_batch as well as the in-process key,
                         or a double-click pays for a second full set of image/mesh jobs).
                         POST assets/<id>/regenerate→reskin.regenerate_asset: re-render ONE asset
                         as a one-job `image` batch reusing the `skin` finalize
                         (save_sprite/mesh_from_image → stage_for_play + assets_done), no whole-game
                         re-skin — the source already references the id. The user's text is a
                         CHANGE NOTE, not the finished prompt: it is merged (one small llm call)
                         with the asset's ORIGINAL prompt — assets.json entries persist `prompt`,
                         data rows' `look` is the pre-persistence fallback — so "give him a red
                         cape" keeps the goblin. mode="img2img" seeds the render from the existing
                         image (partial denoise, keeps composition): the sprite itself in 2D, the
                         mesh's source render in 3D (asset_chain saves it as <id>.src.png), falling
                         back to a full render when no init exists. The init image rides the
                         payload as an upload the worker lands on ComfyUI /upload/image first.
                         build/resume/fix/assets
                         all pass _require_compute FIRST: enqueue enforces the same budget per job,
                         but a broke run must not win the GPU slot and then thrash on refused jobs
                         until its step cap. build charges before checking, since charging grants.
                         Chat drafts
                         specs via tools/chat_tools.py (propose_game_spec/amend_game_spec →
                         codegen.propose_spec/amend_spec). Build progress + spec events emit through
                         tools/build_events.py (_emit → db events log + event_bus). A built run is staged to
                         runtime/games/<id>/ and served at /play (StaticFiles mount) for the SPA.
  config/                settings_schema.py (Pydantic), settings_manager.py (singleton)
  llm_clients/           connector.py, message_builder.py. LLMConnector translates the
                         chat-shaped messages/tools callers pass into Responses input/tools
                         (/v1/responses — the one local endpoint that honors reasoning.effort),
                         enqueues the payload on the `llm` queue and normalizes the worker's reply
                         back to chat shape. The worker owns the inference server address
                         (`--target`), so there is no llm.base_url. `get_connector()` is the
                         cached singleton.
  tools/                 tool_manager, system_tools, comfyui_tools (image backend), file_tools,
                         execution_context (resolve_base_path → the run root).
    trellis_server.py    the mesh backend: the 4B pipeline resident behind /generate. Owns the
                         COLD START (measured: ~330s → a pod WARM in ~116s, docs/DEPLOY.md has
                         the phase table). Boot does three things before the worker may register:
                         STAGE the tier's checkpoints to /dev/shm (the volume streams at 2.7GB/s
                         but does not retain page cache, so only loading from RAM holds — falls
                         back to the volume when it will not fit), LOAD with default init
                         suppressed (37s of a 43s load, every weight overwritten by the
                         checkpoint microseconds later) and only the models the tier asserts
                         (`models_for`, not all 8), then WARM UP on a throwaway mesh (lazy
                         DINOv3/BiRefNet + first-use kernel compile — 53s vs 13s for the job
                         that would otherwise pay it). /health reports `warm` + the timing split;
                         the entrypoint gates registration on it, so a claimed job never pays
                         boot and a pod that cannot generate dies at boot instead. STEADY-STATE:
                         to_glb decimates to 50k, not 500k — the game bundles a 20k-tri mesh
                         (decimate.mjs), so 500k was ~25x waste that starved that simplifier into
                         its sloppy/off-budget path; 50k cut postprocess 4-13s → ~2s and uploads
                         ~8x with equal-or-better final meshes (validated across char/foliage/
                         building).
```

**Inference path (chat / spec draft):** `MainAgent` / `draft_spec` → `MessageBuilder` →
`get_connector()` → `LLMConnector` → the `llm` queue.
**Inference path (build) — build-as-jobs:** the build is a CHAIN of `llm` jobs, not a resident loop.
`build_chain.advance` sweeps `CodegenModule`'s gates, picks the top error, builds one `build_steps`
turn, enqueues it on the `llm` queue tagged `metadata.stage="build"`, and RETURNS (the process may
die). A worker runs the turn; `/worker/complete` → `build_chain.on_completion` reloads the durable
cursor (`build_state.json`), applies the result, and advances to the next turn or finalizes. Local
work (gates, tool dispatch, deterministic fix passes) runs synchronously inside `advance`; only a real
inference suspends. So the whole state the old `AgentLoop` held — step count, cross-fix stall/park,
the fix's growing transcript, the read→edit tool grounding — lives in the cursor and is rehydrated
each completion. AUTHORING extracts the `write` tool call's `code` (falls back to salvage, then a
fenced block, so a model that ignores the tool still lands). GATE FIXES are the read→edit subloop with
read/edit/write as real tool calls; `write` is create-only, so every change to existing code is an
atomic multi-hunk `edit` (never dropped), and READ drops once the fix has read enough without writing
(`_READS_BEFORE_FORCE_ACT`). The human-note fix (`fix_from_note` / `kickoff(kind="fix")`) seeds the
build's FIRST fix with a synthetic `code="human"` Error (classifies to `default`); the outer loop then
re-gates and repairs any regression, exactly like a build. Crash recovery: a build with no turn in
flight and not done is re-advanced by the reaper (the per-run advance lock prevents a double-drive).

**Adding a mechanic:** widen the KIT (`runtime/engine.js` + a `kit_api*.md` section + a worked
example in the prompt). Generation just composes the new primitive. Adding a
whole game FAMILY = a new primitive family (pathfinding, grid/turn, particles, 3D physics).

**Adding a build capability that isn't a kit primitive:** a new tool in `maestro/codegen/tools.py`
and/or a new `Check` on `CodegenModule` (detection) — then route its `Error.code` to a fix shape in
`build_chain._SHAPE_BY_CODE` (defaults to the read→edit subloop), adding a new `build_steps` shape
only if the fix isn't a read→edit. **Adding a build STAGE** (beyond build/asset): register a driver
keyed on `metadata.stage` in the `/worker/complete` dispatch — the queue stays a generic transport.

---

## Settings & running

**Settings:** `src/config/settings.json` (gitignored). Copy from `settings.example.json`.
- `llm.model`, `llm.n_ctx`, `llm.reasoning`. No endpoint: LLM inference rides the queue, so an
  `llm` worker must be running or every call times out.
- The worker-pull queue is the ONLY transport to a GPU — llm, sprite/mesh images (queue `image`)
  and TRELLIS meshes (queue `mesh`) alike. There is no `enabled` flag and no endpoint setting on
  this side: the control plane touches no GPU at all, and a queue with no worker means every job on
  it times out. `workqueue.token` is the worker bearer secret. One worker per queue, and a queue
  owns its card:
  `python -m worker.agent --server <cp>:8000 --token <token> --queue image --target localhost:8188`
  (defaults: server localhost:8000, target localhost:1234, queue llm). exec_seconds are debited to
  the owning game via the run_scope contextvar, set around run_build / fix_from_note / add_assets —
  a stage outside that scope enqueues with no game_id and so is neither metered nor gated.
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
