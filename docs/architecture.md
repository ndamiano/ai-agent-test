# Architecture

System shape: what the pieces are, where state lives, what the invariants are, and what it would
take to run on more than one box.

`CLAUDE.md` is the authority on the build loop itself (the driver, the turn machine, the tools).
This file is the layer above: processes, boundaries, storage, scale.

---

## The two planes

**Control plane** — one CPU-only container. FastAPI serving the API + the built SPA same-origin
(single uvicorn worker), the job queue, the databases, the run directories, and two background
threads (queue reaper, RunPod autoscaler). It owns all durable state and all decisions.

**Compute plane** — worker agents (`worker/agent.py`), one process per queue, running wherever a GPU
is. A worker long-polls `/worker/claim`, executes against its own local inference server, and posts
the result to `/worker/complete`.

The only coupling is the queue. The control plane holds no GPU endpoint, dials nothing, and has no
config naming a compute host. A worker needs three things: the control-plane URL, the shared bearer
token, and a queue name.

```
  browser ──► control plane ──► jobs table
                    ▲                │
                    │                │ claim (long-poll)
              complete                ▼
                    └────────── worker agent ──► local inference server (127.0.0.1)
```

Consequences worth stating:

- **Adding capacity is not a deploy.** Any process with the token can serve prod traffic. A
  workstation on a tailnet and a rented RunPod pod are the same thing to the queue.
- **A queue with no worker is a timeout, not an error.** Nothing tells the control plane that a
  queue is unserved.
- **Workers are anonymous and interchangeable.** A worker row exists for heartbeat and scaling
  bookkeeping, never for routing.
- **The wire format is the worker's problem.** The control plane enqueues a canonical chat request;
  the worker translates it for whatever its target serves (`llm_clients/wire.py`,
  `worker/handlers.llm`). Supporting a new engine is a branch in the worker.

### Queues

| queue | work | target |
|---|---|---|
| `llm` | one build turn (one inference) | llama.cpp / any OpenAI-shaped server |
| `image` | one sprite or texture render | ComfyUI |
| `mesh` | image → 3D | TRELLIS |

One worker per queue, and a queue owns its card. `exec_seconds` are debited to the game named by the
job row's `game_id`; a job without one is neither metered nor gated.

---

## Request paths

**Create a game.** `POST /api/games` takes the typed prompt, creates the run, charges credits, and
kicks off the build in one call. There is no conversational surface — every `llm` job on the queue
belongs to a game that is paying for it.

**Build as a chain of jobs.** A build is not a resident loop. `build_chain.advance()` runs all local
work synchronously (tool dispatch, staging, cursor writes) and suspends at the one point that needs
a GPU: it enqueues a single `llm` job tagged `metadata.stage="build"` and returns. The process is
then free to die. `/worker/complete` routes on `metadata.stage` back into `build_chain`, which
reloads the durable cursor, applies the result, and advances again.

Crash recovery falls out of this: a build with no job in flight and no terminal phase is re-advanced
by the reaper. Job metadata carries only `{stage, run_id, build_id}` — the cursor file is the single
source that a completion reloads, advances, and rewrites.

A landed turn is appended to the run's own `turns.jsonl` and its jobs row is then emptied, so the
db holds live work and the run dir holds the archive. The append comes first: the two writes cannot
share a transaction, and the order is what guarantees the body is never in neither place.

**Assets.** `generate_media` enqueues one `image` job and answers immediately with the path the file
will appear at. Chained work is named in the job's `metadata.then` (`mesh_from_image`, save/decimate
operations, the batch finalize) and dispatched by `asset_chain`, so the queue stays a generic
transport that never learns what an asset is.

**Adding a stage** beyond build/asset means registering a driver keyed on `metadata.stage` in the
`/worker/complete` dispatch.

---

## Where state lives

| state | location | notes |
|---|---|---|
| accounts, sessions, credit ledger | `auth.db` (SQLite) | `MAESTRO_DATA_DIR` |
| games, builds, jobs, events, workers, compute budget | `platform.db` (SQLite, WAL) | `MAESTRO_DATA_DIR` |
| run dirs — spec, build cursor, turn log, game source + its git history | `<WORKING_DIRECTORY>/runs/<run_id>/` | local filesystem |
| staged playable games | `runtime/games/<slug>/` | local filesystem, served at `/play` |
| structured config the env can't express | `src/config/settings.json` | host bind mount |

Two named Docker volumes back the first four rows (`maestro-data` → `/data`, `maestro-games` →
`/app/runtime/games`). See `deploy.md` for the invariant.

The box is one box, but the state is not: a control-plane thread snapshots both DBs to the S3
bucket (`tools/db_backup.py` — every 15 minutes, sooner on account/credit writes), and every
settled run uploads itself as one archive to the same bucket (`maestro/codegen/archive.py`), with
a nightly `--archive-all` sweep for any upload that failed. Staged games are a copy of each run's
`game/` and are re-staged on rehydrate rather than backed up. Mechanism, secrets, and the restore
drill: `docs/backups.md`.

---

## Invariants

These are load-bearing; breaking one is a redesign, not a bug fix.

1. **The control plane touches no GPU.** The queue is the only transport.
2. **One turn in flight per run.** Enforced today by an in-process lock, with `advance` reachable
   only from the completion handler and the reaper.
3. **The durable cursor is the truth.** In-memory build state is always reconstructible from
   `build_state.json`; a control plane that restarts mid-build holds nothing while its turns keep
   completing.
4. **A gate may only detect broken, never "bad."** One gate stands between a build and `built`:
   `index.html` exists. See `CLAUDE.md`.
5. **The prompt is the artifact.** No inference runs between the person's words and the build's
   user message.
6. **Nothing proprietary in the loop.** MIT/Apache-2.0 weights and tooling only (`vision.md`).

---

## Trust boundaries

- **API.** Bearer sessions; every route gated by middleware except explicit public paths. The
  WebSocket authenticates itself (HTTP middleware never sees the socket scope). Account creation
  is by CLI or the invite-code signup route — a code is admin-minted, use-counted, and redeemed
  atomically with the user insert; signup is throttled per client IP.
- **`/worker`.** A single shared bearer token (`WORKQUEUE_TOKEN`). Any holder can claim any job on
  any queue and post any result. Workers are trusted infrastructure, not tenants.
- **Inference servers.** Bound to `127.0.0.1` on the worker's own box, never exposed. A reachable
  one is an unauthenticated GPU.
- **Generated game code.** Runs in the user's browser in an iframe, contained by a CSP on every
  `/play` response and admitted by a per-game grant cookie the handoff flow mints
  (`auth/playgrants.py`) — the play surface holds no credential its JS can read. With
  `play.origin` set, games are served from their own registrable domain and the host-split
  middleware keeps the API off that host entirely — true origin isolation, required before any
  sharing feature; unset, the game still shares the app origin's localStorage and the ownership
  check at play-session mint is what keeps that safe. Detail: `deploy.md` § Known deferred risks.

---

## Scaling

Today: one control-plane box, one uvicorn worker, N GPU workers.

**The expensive axis already scales out.** GPU work is queue-pulled and autoscaled; capacity is
added by starting processes that the control plane never has to know about. The control plane is
I/O-bound coordination — enqueue, apply a completion, serve static files — so a single box goes a
long way, and product limits arrive before control-plane CPU limits do.

**What pins the control plane to one process**, in the order they'd need fixing:

1. **SQLite.** Two boxes cannot share `platform.db`. Every other item below is downstream of this.
   The fix is mechanical: `db/store.py` and `auth/store.py` are single modules over a `_db()`
   context manager with plain SQL. `claim_job` already expresses the claim as
   `UPDATE … WHERE id = (SELECT … ORDER BY created_at LIMIT 1) RETURNING *`, which becomes
   `FOR UPDATE SKIP LOCKED` in Postgres — a better claim, not a compromised one.
2. **The background singletons.** The reaper and the autoscaler start unconditionally at boot. Two
   control planes means two autoscalers reading the same backlog and both adding pods — the one
   failure here that spends money. Needs a leader lease or a separate singleton process.
3. **The advance lock is in memory** (`build_chain._locks`, keyed by run id). It is what enforces
   invariant 2. Across processes, two completions for one run can interleave into a double advance.
   The durable cursor makes the fix small: compare-and-swap on the cursor's step count instead of
   holding a lock.
4. **Run state is on local disk.** `runs/<id>/` and `runtime/games/<slug>/` must be on the box that
   handles the completion. This is the only item that is a project rather than a change — shared
   storage, or object storage for staged games. The games half is wanted anyway, for share links
   and a CDN.
5. **Per-process counters.** The login throttle and the LLM rate limiter are per process, so N boxes
   multiply their limits by N. The WebSocket manager holds connections in memory, so a user
   connected to one box sees no events from another — this one degrades gracefully, since
   `GET /api/games/{id}/events` already serves the same data by polling.

Until (1)–(3) land, `uvicorn --workers N` is unsafe on a single box for the same reasons multiple
boxes are — vertical scaling is untapped, not free.
