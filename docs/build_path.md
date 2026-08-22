# The build path, module by module

`CLAUDE.md` holds the laws this code exists to serve; this file is the map. Module docstrings are
the authority on contracts — what is here is the shape and the reasons that are not visible from any
one file. `docs/architecture.md` is the layer above (processes, state, trust boundaries).

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
                         synchronously and SUSPENDS only at a real inference (enqueue one llm job +
                         return; the process is free to die). Never more than one turn in flight per
                         run, and advance runs only in the control-plane process (completion handler
                         + reaper), so an in-process lock serializes them. kickoff/pause/resume/stop/
                         is_active/status_of are the API/CLI entry points. `kickoff(fresh=True)` is
                         the FROM-SCRATCH build: empty the game folder, then seed. Only the button
                         asks for it — a plain re-trigger, a resume and a fix all carry the folder
                         forward on purpose. It snapshots before deleting and preempts the art the
                         last attempt is still waiting on, since a render in flight would land in
                         the new folder and write itself into a manifest that never asked for it.
                         Without it a second attempt opens on the dead build's half-written files:
                         the model reads them, believes them, and re-asks for art it already has
                         under new ids (measured 2026-08-01: three naming schemes for one cast, 40
                         renders, no finished game). `stop` ends a build where it stands and KEEPS
                         what it wrote: playability is judged as it is at the step cap (an
                         index.html), while the builds row records `stopped` — a run ended by hand
                         over a game that runs is not a run that failed. `pause` DEQUEUES: a
                         still-queued turn is cancelled and its step refunded, a claimed one is left
                         to its worker and applied when it lands, and the build parks at the ENQUEUE
                         boundary rather than the top of advance. That boundary is what makes pause
                         cost nothing: parking at the top discarded a turn the GPU had already been
                         paid for, and announced itself on every reaper re-drive (one feed line
                         every 5s, forever). A refused compute budget PREEMPTS the run's
                         still-pending asset jobs before giving up — gameplay beats art. A landed
                         turn is ARCHIVED to the run dir before its jobs row is emptied
                         (`turn_log`) — append first, so there is never a moment with neither copy.
      build_steps.py     the turn MACHINE: step(spec, run_dir, tools, cursor, result, error)
                         -> Infer|Done.
                         A `result` of None is NO TURN TO APPLY (a resume, a reaper re-drive) and
                         re-asks from the transcript as it stands; `{}` is a turn that ran and
                         answered with nothing, which the nudge branch handles; an `error` is a turn
                         the WORKER could not deliver, which is neither. Collapsing any two of the
                         three scolds the model for a reply it never sent and burns a turn.
                         Owns the seven tool schemas, the transcript, compaction, the DONE-NUDGE
                         (`cursor.done_nudged` — asked once, then the next `done` is taken), and
                         every way a reply TOO BIG TO LAND arrives — all three answered with the one
                         remedy (write it in pieces), because they are one event: the reply cut off
                         before any tool call, the call whose ARGUMENTS stop mid-write (unreadable,
                         so nothing ran — reported as the truncation it is, never as the missing
                         `path` it parses to), and the 500 the inference server's own tool-call
                         parser returns for an oversized call. Measured 2026-08-01: one 64 KB
                         write_file reported as `KeyError: 'path'`, resent identically, four server
                         500s scored as silence, a dead build at step 28. A failing call resent with
                         identical arguments is COUNTED and the count told back; what to do instead
                         is the model's call. That count is keyed PER CALL, not against the previous
                         one: a model stuck on an edit re-reads the file between attempts, and a
                         succeeding read between two identical failures must not clear the ledger
                         (measured 2026-07-29: 12 identical failing edits, each scored as the first).
                         A read reaches the transcript as the FILE (`<file path=…>`), never
                         serialized — a JSON body shows every quote as \" and the model copies that
                         into old_text, where it matches nothing (measured 2026-07-28: 17 of one
                         build's 32 turns, resent byte-identical).
      build_state.py     the durable build CURSOR (runs/<id>/build_state.json) — phase, step count,
                         the PAUSE flag, the done-nudge flag, the growing transcript, and the
                         read→edit tool grounding, rehydrated into build_tools each completion (a
                         fresh process would else refuse a resumed edit). Job metadata carries only
                         {stage,run_id,build_id}; this file is the single source the completion
                         reloads, advances, rewrites. Pause lives here and not in memory: a control
                         plane that restarts mid-build holds nothing, while its turns keep
                         completing.
      turn_log.py        the run dir's own copy of the conversation (runs/<id>/turns.jsonl), and
                         the only one that lasts: a turn's request IS the transcript so far, so a
                         payload per jobs row stored the same conversation once per turn (951 MB of
                         `jobs.payload`, 26 MB for one 117-turn build). The system prompt and the
                         seven schemas are byte-identical every turn, so they ride ONE `meta` record
                         and a `turn` record carries only what that turn ADDED — turn k is
                         `system + tools + concat(added[0..k])`. A `compact` record carries the
                         rounds `build_steps.compact` dropped and the note that replaced them, so a
                         replay shows what was really sent. A FIX appends its own meta and never
                         truncates. The prompt log reads bodies from here once the row is empty.
      archive.py         the run dir's OFF-BOX copy. tools/s3.py is a minimal SigV4 client over
                         `requests` — a wrong signature is a loud 403 and the payload hash rides
                         the request, so a signing bug cannot silently succeed. A settled finalize
                         (no gate fix kicked, no stage left) uploads the run dir as one tar.gz,
                         minus a world's `world_build/` — the world a game plays was published
                         into the game folder, and the stages' working material is hundreds of
                         megabytes nothing reads back. `evict` reclaims local disk and REFUSES without a verified remote
                         copy or under an active build; `rehydrate` pulls it back and re-stages;
                         `ensure_local` hooks play-session/build/fix so an evicted game is a
                         download away. A boundary like snapshots: an unconfigured bucket logs and
                         stands aside.
      snapshots.py       the game folder's HISTORY, in git: `runs/<id>/game.git` is the repo and
                         `game/` its work tree, so nothing appears inside the folder the model
                         lists, staging copies and /play serves. A commit at every finalize that
                         produced a playable game, and one before a fix edits — a fix re-edits code
                         that already worked, so the last playable version is exactly what it can
                         destroy. Everything in the folder is committed, art included: a restore
                         that leaves half the game at another version is not a restore. git is a
                         BOUNDARY — a snapshot that cannot be taken is logged and the build carries
                         on, since losing history is not a reason to lose a game.
      tools.py           list_files / read_file / write_file / edit_file / generate_media /
                         compose_scene / compose_world — the smallest surface that works, and kept
                         that way. compose_world is worldgen's build face: one 3D world per game,
                         refused a second time because the game is already written against the
                         first one's metres and regions.
                         compose_scene is scenegen's build face: it bakes a whole MAP —
                         assets/<id>_ground.png plus <id>_scene.json (walkable grid, door cells,
                         POIs) — and the model reads the json and wires it (verified in a real
                         build 2026-08-06: three scenes asked for, fetched at runtime, walkable
                         grid driving collision). Interiors/dungeons bake synchronously, pure
                         CPU; town/glade ride the SCENE CHAIN (below): scene.json and a
                         code-painted ground land at tool time, the diffusion picture upgrades
                         the same path as it renders. A path is resolved and must land inside the
                         game folder. Every failure is REPORTED to the model as text (a missing
                         argument names itself) and never guessed at: substituting a default for a
                         missing `path` sent every write in a run to one file.
                         `read_file` on a missing path the manifest lists as requested answers
                         PENDING — keep the path, do not re-ask — never "no such file": the
                         error reading as a failed ask made a build re-request its whole set
                         under "-v2" ids (11 duplicates, 2026-08-03), and ok=True keeps the
                         repeat ledger from scolding a legitimate second look.
                         `read_file` returns a WINDOW of whole lines from a 1-based `offset`. Both
                         halves are load-bearing: a window cut mid-line is text the model cannot
                         reproduce and it copies the cut into old_text, and without `offset` the
                         tail past the ceiling is unreachable, so an edit there can never land
                         (measured 2026-07-29: 12 byte-identical edits, 248 of 249 chars matching,
                         the 249th the cut). A window that stops short says where the rest is and
                         that a file this size is worth splitting.
      staging.py         where a game lives (runs/<id>/game/) and how it reaches the browser: copy
                         the folder to runtime/games/<slug>/. No bundle, no transform.
                         `has_authored_files` discounts the seed, so it says whether a BUILD wrote
                         anything — which is what offers the from-scratch button. Also the SEED
                         (seed_vendor): the game folder starts holding the vendored renderer and
                         nothing else, since everything placed there steers the first list_files.
      assets.py          the ASSET stage — `request_media` is what the game's generate_media call
                         runs: enqueue ONE `image` job, record the ask in assets.json, answer with
                         the path. Nothing plans, rewrites or inspects the game's source. `kind`
                         picks the model, the workflow and what a landed render owes: sprite is
                         matted and autocropped, scene keeps the whole frame, a tile is quilted
                         seamless (`tools/quilting.py`, in `asset_chain._save_flat` — soft, a
                         quilt that throws saves the raw render), mesh chains image →
                         TRELLIS (its image leg renders as a sprite — TRELLIS lifts a cut-out
                         subject). `check_render` reads the alpha of what landed against the kind
                         that was asked for and records a `defect` on the manifest entry: a matte
                         that never ran, one that ate the subject, a background full of holes. It
                         may only find BROKEN — whether a picture suits the game is the same human
                         question as whether the game plays right, so the gallery SHOWS the defect
                         and nothing acts on it. An already-rendered file, a blocked prompt
                         and a refused budget are all answered, never retried blind. A REPEATED id
                         is answered ONCE and then obeyed — the done-nudge shape: the first repeat
                         says "not requeued, call again to replace it", the second re-renders and
                         rewrites the entry's prompt. Silently keeping the old picture was worse
                         than either, because "redraw these" got agreement and no new art.
                         Also owns start_from_manifest (the TOP-UP: re-render what the record says
                         is still missing) and the single-asset regenerate — the user's text is a
                         CHANGE NOTE merged (one small llm call) with the entry's original prompt,
                         so "give him a red cape" keeps the goblin; img2img seeds from the render.
                         A top-up RESUMES a mesh from its `<id>.src.png` if one is there: the chain
                         needs ComfyUI and then TRELLIS, and a one-GPU box holds one at a time, so
                         always restarting at the image leg never reached the second half.
      scene_chain.py     compose_scene's GPU path (the 2026-08-08 validated cell recipe, see
                         docs/experiments.md). Synchronous half at tool time: one plan call
                         (blocking llm job — the calling turn has already completed, so the queue
                         is free), the scenegen solver, scene.json (walkable TRUTH, never
                         diffused), a code-painted ground.
                         Async half as jobs: store-miss subjects (Qwen-2512, image queue) each
                         chaining TRELLIS (mesh queue) whose completion deposits
                         subject+GLB+sprite to the asset store, plus one masked terrain img2img
                         (DreamShaper; structural cells at 0.45 so code keeps owning where
                         things ARE) — the BATCH FINALIZE is the fan-in barrier
                         (claim_batch_finalize already guarantees exactly-one against the
                         reaper): composite store sprites over the terrain, then one Qwen-Edit
                         embedding job whose finalize drift-checks each box against the
                         composite (detection only — broken, never bad), lands the final ground
                         AT THE SAME PATH and re-stages. Every enqueue carries game_id, so
                         admission and debit ride the queue like all GPU work; a refused budget
                         at any seam leaves the best ground already on disk (code paint →
                         terrain → composite → embed, each overwriting the last). Its names
                         ride asset_chain's registries, so the completion dispatch stays one
                         branch.
      asset_store.py     the ASSET STORE: rendered object TYPES shared across games, so a mesh
                         that cost ~30s of GPU is never paid for twice. Entry =
                         <data_dir>/asset_store/<key>/ holding subject.png + mesh.glb +
                         sprite.png + meta.json; every intermediate kept (dropping one forces
                         re-paying the stage upstream), meta stamps which model made each piece
                         so an upgrade invalidates exactly its own leg. Claim-then-fill
                         (O_EXCL + TTL) so two builds missing one type render it once. Type
                         RESOLUTION is one small llm call for the whole plan (flavor name →
                         generic type + subject phrase), the style riding the KEY — a desert
                         inn and a snow inn are different entries, which is what retires the
                         battery's cottage-in-the-desert; fallback is the name itself, which
                         renders right and merely reuses less. Games copy sprites out at
                         composite time and never own entries; /play never reads the store.
      stages.py          STAGED CONSTRUCTION — the hardest system gets a whole build to itself,
                         then the game grows by fix builds (measured 2026-08-02/03: a duel built
                         alone earned a dedicated AI module; the same duel inside the full
                         request earned zero opponent code — seven staged chains produced the
                         battery's strongest games). `plan` is one small llm call
                         (prompts/stage_plan.txt): stage 1 a complete playable game of the core
                         system, each later stage ADDS one system and names what must keep
                         working; a plan that fails degrades to one stage, the request as-is.
                         The plan lives in runs/<id>/stages.json WITH the original request.
                         Stage advance rides the post-finalize seam strictly AFTER the error
                         gate: a stage is only stacked onto a game that loads clean. CLI:
                         `run --staged "<request>"`. Web: POST /api/games/enhance plans WITHOUT
                         building, and planning is where the CREDIT is charged — it is the game's
                         first inference, and any free inference path is a cost leak (a one-off
                         llm call on the autoscaled queue bills ~40s of pod wall-clock for ~3s of
                         work). The plan call meters against the grant it just bought; re-planning
                         the same run (body run_id) never charges again, and a paid plan builds on
                         ITS run whether staged or plain. The plan is saved beside the run, so the
                         create page offers a charged-but-never-built plan for resume
                         ("unstarted_plan" on the list, "plan" on the detail). The SPA shows the
                         user's text and the editable stage list side by side; a one-stage plan
                         still shows review — nothing builds unseen. Build sends the stage texts
                         to /build — the boxes' contents ARE what builds, so THE PROMPT IS THE
                         ARTIFACT holds stage by stage. Enhancement is a checkbox (default on);
                         opting out gets a one-time recommendation notice whose "don't show
                         again" lives in localStorage. A plain build clears any stale stage plan.
      error_gate.py      the ERROR GATE — after a playable finalize, open the staged game in a
                         headless browser (playwright chromium, an ephemeral static server, one
                         click + Enter + Space to get past a title screen) and re-enter the fix
                         machine with the FIRST uncaught error and its address. One error per fix
                         build; node --check supplies the file:line a browser SyntaxError omits
                         (tried as module then script — authored games import three); a
                         redeclaration note lists every declaration site; a parse note says fix
                         the whole file. Stops on MAX_ROUNDS, on the same error two rounds
                         running, and never touches a build a human stopped. A probe that cannot
                         run logs and stands aside — the gate is a boundary like snapshots.
                         Round state in runs/<id>/error_gate.json.
      asset_use.py       does the game LOAD the art it asked for — static analysis over the game's
                         own source, no model and no GPU. Two facts: an asset the source never
                         names (paid for, never seen) and an `assets/…` path in neither the manifest
                         nor the folder (a broken image, and no top-up can fill it because nothing
                         ever asked). An asset still RENDERING is neither — it is in the manifest,
                         so the source naming it is right. It rides the DONE-NUDGE, which is already
                         the one place the build asks what is unfinished and is asked ONCE.
                         Matching is generous one way and literal the other so the count
                         under-reports: an id named ANYWHERE counts as loaded (a game may build
                         `"assets/" + id + ".png"` at runtime), while a missing path must be written
                         out in full. The vendored renderer is not the game's source — GLTFLoader
                         discusses `assets/` paths in its comments.
      asset_chain.py     what a finished asset job does NEXT — the names in its `metadata.then`: a
                         CONTINUATION to enqueue (mesh_from_image), OPERATIONS on this result
                         (save_sprite / save_flat / decimate), and the batch's FINALIZE. It owns those
                         names so the queue stays a generic transport that never learns what an
                         asset is. Every save runs the SAFETY policy first (`_admit`): the worker
                         attached NSFW scores to the render, `assets.render_verdict` decides, and
                         a refused (or verdict-less) render is deleted, marked on the manifest and
                         recorded as a violation — a refused mesh source never reaches TRELLIS.
      artifact_screen.py the artifact TEXT gate — the game folder's authored text through the same
                         narrow screen as every input seam, at finalize before staging. A hit
                         holds the build.
      prompts/           build.txt
      run.py             create_run / propose_prompt / set_prompt / run_build (CLI: kickoff +
                         block-poll the cursor) / fix_from_note + the CLI. The web build/fix path is
                         fire-and-forget through build_chain.kickoff, not run.py.
    services.py          parse_args — any argument shape a local model returns → a dict.
                         parse_args_checked also says whether anything was READABLE: a call cut off
                         at the output cap and a call that carries no arguments both answer {}, and
                         only one of them is a failure.
    tool_calls.py        recovering a tool call the model wrote as TEXT, when the server's own
                         parser didn't claim it. A registry of encodings (Hermes/Qwen, DeepSeek,
                         Mistral, Llama python-tag, two XML forms, named object, arg-shape), tried
                         strictest first. A parser only wins if EVERY call it found names an offered
                         tool and carries that tool's required arguments.
    state.py             RunState — durable per-run dir <working_dir>/runs/<run_id>/ (spec.json,
                         game/ folder). Ownership + charge state live in db/, not the run dir.
  worldgen/              the 3D world behind the compose_world build tool: the worldclaw pipeline
                         (ported 2026-08-21), stage for stage, with every GPU call on maestro's
                         queues. build.py runs the nine stages in order — scene, terrain-plan,
                         terrain-assets, construct, terrain-refine, regional-plan, objects,
                         scene-refine, final-render — and `start_at`/`stop_after` make any run of
                         them resumable, which is what lets one world be built in legs. planning/
                         and terrain/ and objects/ are the stages themselves; llm.py, backends/
                         images.py and backends/meshes.py are the only ways out to a card, each a
                         job on the llm, image or mesh queue. Each stage names its own sampling
                         temperature (the grounding pass is greedy, the planners are not) and its
                         own mesh seed, and both ride the job all the way to the server.
                         terrain/render.py is both the refinement loop's eyes and the contract:
                         it writes world.json and draws it in headless chromium through
                         runtime/vendor/world.js — the SAME loader the game imports, so the agent
                         never judges a picture the game cannot reproduce. Every path in
                         world.json is relative to the folder it sits in, which is what makes the
                         folder copyable into a game.
                         compose.py is the build face. The tool BLOCKS through `construct` — the
                         first stage at which a world exists to write at all, since world.json
                         names the height field, the region materials and the scatter — and
                         answers with size_m and the regions in metres, because the model places
                         gameplay by region the turn it hears back. The rest runs on one thread
                         for that run, in three legs (terrain-refine / regional-plan..objects /
                         scene-refine..final-render), and PUBLISHES after each.
                         The publish contract: `<run_dir>/world_build/` holds the intermediates
                         (~275 MB of concept images, region compositions and refinement views) and
                         no game ever reads it; `<game>/world/` holds only what world.json names —
                         the json, heightmap.f32, the weight textures, each region's albedo and
                         normal, and the GLBs — at the same relative paths, so staging, archiving
                         and playing carry the world with the game and the game fetches nothing
                         outside its folder. world.json is rewritten from the world as it stands
                         at every publish and replaced by rename, so a browser never reads one
                         naming a file that is not there yet. A world with no meshes yet still
                         COLLIDES: the loader reads collision from stated heights, never from a
                         GLB. Nothing here blocks `done` — a build finalizes on a game whose
                         ground already loads while the scenery is still rendering — and a leg
                         that fails is logged and left, because the pipeline resumes by stage.
                         Locally the three queues are drained by `scripts/local_gpu.py auto`
                         (`docs/local_dev.md`).

  scenegen/              scene composition behind the compose_scene build tool. bake.py is the
                         synchronous face (interiors/dungeons); blockout.py is the SOLVER behind
                         the scene chain: the llm plans relations — counts, kinds, no positions —
                         and code places deterministically, roads by construction, walkable truth
                         emitted, POIs snapped to the network. Terrain features are BANDS whose
                         depth varies along the edge, so a coast is a coastline and not a ruler,
                         and a sand strip handed the water band's own depths follows it by
                         construction; variety patches are cosmetic and never touch hazard or
                         cost, so placement and roads are unchanged by them.
                         prompts/blockout_plan.txt is its hill-climbable plan prompt. The rest is
                         the library: seeded layouts where every town door faces a street by
                         construction, kit-assembled buildings that return their door cells,
                         zone-scatter rules, procedural materials, distance bands, the light plan.
                         Code owns everything spatial; diffusion paints materials and parts — part
                         sprites are code-drawn from a style-keyed palette today, rendering them
                         through the image queue in the game's own style is the marked upgrade.
                         Structures restyle at the PART level, never img2img over an assembled
                         building.
```

The rest of the platform is build-path-agnostic: `auth/` (identity, bearer sessions, credits,
invite-code signup — the only self-serve account path, admin-minted codes, per-IP throttled — the
/play handoff + per-game grant-cookie gate, `playgrants.py`; an account carries an EMAIL, which
exists to recover it: `/auth/forgot` mails a single-use link that answers identically for an address
with no account, and setting a password ends every session the account has), `db/`
(games/builds/events/jobs/workers + the compute budget), `worker/` (the pull-side GPU worker),
`scaler/` (the RunPod autoscaler), `api/` (FastAPI routers), `llm_clients/`, `tools/`, `config/`,
and `frontend/` (the React SPA, served same-origin by the API).

---

## Where inference happens

**Only inside a build.** There is no conversational surface: `POST /api/games` takes the prompt the
person typed, creates the run, charges it, and starts the build in one call, so every llm job on the
queue belongs to a game that is paying for it.

**Build-as-jobs.** `build_chain.advance` runs one `build_steps` turn, enqueues it on the `llm` queue
tagged `metadata.stage="build"`, and RETURNS (the process may die). A worker runs the turn;
`/worker/complete` → `build_chain.on_completion` reloads the durable cursor (`build_state.json`),
applies the result, and advances. Crash recovery: a build with no turn in flight and not done is
re-advanced by the reaper, and the per-run advance lock prevents a double-drive.

**Adding a build STAGE** (beyond build/asset): register a driver keyed on `metadata.stage` in the
`/worker/complete` dispatch — the queue stays a generic transport.

---

## The art ledger

`generate_media` entered UNMEASURED (2026-07-28) and is still unsettled — art is the one capability
no `write_file` can stand in for. Counted over the 35 staged games (2026-08-01): 19 called it at all,
308 asks, 27 of them meshes across 6 builds — and the asks RATION. One 3D village asked for five NPC
portraits and a lighthouse while building five shops, a farm, lamp posts and every interior out of 27
code primitives. So the prompt no longer tells the model to draw a plain shape at the spot and the
tool no longer prices a render in minutes: both framed art as a thing that might not arrive, and a
model that believes that draws a prism and moves on.

What settles it is COVERAGE — how much of what the player sees got art — not call-at-all, and the
same run has to show that a build whose art never lands still renders, since nothing now tells the
model to draw something in the meantime.

Coverage, counted (2026-08-01, `asset_use.audit` over the 35 staged games): 352 assets asked for,
**128 rendered and never referenced by the game's source**, and **115 `assets/…` paths referenced
that were never asked for**. One ghost game asked for all 11 of its assets before writing a line of
code, wrote the game with 89 canvas primitives, read its own `assets.json`, and loaded none of them.
One card game rendered 67 and used none, while shipping 114 paths under an `assets/cards/` folder
that does not exist. The tool answers with a path and never learns whether the path was used, so
until the audit nothing in the loop could see either half. Orphaning is NOT the recent prompt edit: a
build carrying the older "draw a plain shape at that spot" line orphaned 7 of its 8.

---

## The retired gate

`tsc` used to be the contract gate, and it earned its place against `engine.d.ts` — types to check
*against*. Measured on the 25-game grid after the kit came out: unfiltered `--checkJs` reported 54–94
errors on games that WORK (implicit-any, `getElementById` possibly-null, `let x = []` inferring
`never[]`); filtered to the codes that mean something in untyped JS it found **zero real defects**,
and every hit traced to a global declared in an inline `<script>` that tsc never reads. It is gone.
If output ever moves back to TypeScript it comes back for free and is worth it immediately.
