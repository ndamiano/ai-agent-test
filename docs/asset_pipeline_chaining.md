# Asset Pipeline Chaining

How the asset (skin) stage stops being a long-lived polling thread and becomes a chain of queue
jobs that carry their own follow-up work.

---

## 1. The problem

`queue_client.run_job` enqueues one job and blocks on a 250ms poll until it completes. Every
producer shares it, so:

- **Queue depth never exceeds 1.** The scaler's scale-up rule is `pending ÷ effective workers`
  (`scaler/policy.py`), so it can never fire on an asset run — only the starvation trigger can.
- **Workers wind down with work seconds away.** A worker that finishes mesh 3 sees an empty queue
  and starts burning its `idle_exit_seconds` window while mesh 4 is still waiting for its image.
- **A thread is parked for the whole stage.** `add_assets` runs on its own thread and spends
  minutes sleeping. At 100 concurrent runs that is 100 parked threads.
- **The mesh stage barriers on a directory.** `run_trellis_batch(sprite_dir, out_dir)` globs a
  directory, so every image must land before any GLB starts. Mesh *i* only needs image *i*.

Measured shape (`fdb1c9b`): image ~5s warm, TRELLIS 27s steady / 318s cold (222s volume load +
~65s triton JIT). For 8 meshes the mesh queue is the entire wall-clock; the win is keeping it
saturated and visible, not running the producer concurrently.

---

## 2. The design

A job becomes a node in a chain. Its `metadata.then` declares what happens when it completes:
enqueue a follow-up job, run control-plane operations on its result, and — when it is the last
job of its batch — run the batch's finalize operations.

```
metadata = {
  "then": {
    "enqueue":   {"queue": "mesh", "kind": "trellis_mesh", "from_result": "file"} | null,
    "operations": ["decimate"],                              // this job's result
    "finalize":   ["write_mesh_manifest", "fit_building_boxes",
                   "build_bundle", "stage_for_play"]         // the batch, winner only
  }
}
```

Every job in a batch carries the same `finalize` list; exactly one of them runs it (§4). The
worker never sees `metadata` — `/worker/claim` returns `payload` only, so a worker stays a
generic executor with no knowledge of chains.

Producers enqueue and return. Nothing waits.

---

## 3. Schema

Three columns on `jobs`. The table's data is disposable and there are no external readers, so
these go straight into the `CREATE TABLE` in `db/store.py` — no `ALTER` path, no migration shim.

```sql
metadata     TEXT,          -- JSON: then + chain provenance. Never sent to a worker.
batch_id     TEXT,          -- correlation key for last-one-out
finalized_at REAL           -- stamped by whoever ran the batch finalize
```

```sql
CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id, status);
```

`batch_id` is top-level rather than a metadata key because the completion transaction runs
`COUNT(*) WHERE batch_id = ? AND status IN ('pending','claimed')` on the hot path of every job in
the system, while holding the write lock. Inside a JSON blob that is a table scan. Metadata holds
what nothing queries.

---

## 4. The completion transaction

`complete_job` already moves the job row, the game's `seconds_used` debit and the worker's
`busy_seconds` in one transaction. It grows the chain, in this order:

1. Flip the job to `done` / `failed`, debit as today.
2. If `then.enqueue` and the job succeeded — insert the follow-up job with the **same
   `batch_id`, `game_id` and `build_id`**, admitted against the budget like any other enqueue.
3. `COUNT(*)` remaining `pending`/`claimed` in the batch.
4. Zero remaining → this completion owns the batch finalize.

**Step 2 must precede step 3.** Inverted, the last image completes, sees zero remaining, and
finalizes a batch whose mesh jobs do not exist yet. Both steps inside the one write transaction
means exactly one completer can observe zero, so there is no double-finalize race.

`game_id` copies from the parent row, so a continuation's `exec_seconds` still debits the right
game even though it was enqueued outside `run_scope`. This is the attribution hole `90a4a15`
closed; it needs a test.

A continuation refused by the budget (`InsufficientCompute`) leaves the batch short. It is not an
error: the finalize still runs and the game renders primitive shapes, which is the existing
soft-degrade contract (`reskin.py` `generate_meshes`).

### Executing the ops

`then.operations` and `then.finalize` name entries in a registry — `{name: callable}` in one
module owned by the asset pipeline. The router dispatches by name so the transport itself stays
free of pipeline semantics; the registry is the single place that knows what `decimate` means.

Ops run fire-and-forget via `asyncio.to_thread` — `/worker/complete` is `async`, and a bare
`create_task` around sync CPU work blocks the event loop and stalls every other worker's
completion. They are bounded CPU (seconds), not a wait, so a thread here is not the thing §1
objects to. The completing worker's HTTP response does not wait on them.

On success the finalize stamps `finalized_at`.

---

## 5. Events

Every completion publishes to the WS via `event_bus.publish_sync` (`event_bus.py:58`), which is
sync-callable from the router thread and resolves `run_id` → owner itself. A job's `game_id` is
the run_id, so this needs no new plumbing. Completions on all queues emit; the client filters.

This is also what makes §7 work — the WS becomes the progress channel that the removed return
value used to be.

---

## 6. Chaining the asset stage

`reskin.generate_meshes` stops running the pipeline and only starts it:

- Allocate a `batch_id`.
- Enqueue every image job at once, each carrying `then.enqueue` for its mesh job and the batch's
  `finalize` list. Depth is `N` immediately, so the scaler sees real backlog and a worker never
  idles mid-run.
- Return. The first mesh job is enqueued the moment its image lands (~5s), not after all images.

`run_trellis_batch(sprite_dir, out_dir)` is deleted — the directory argument *is* the barrier.
`_decimate_glb` becomes the `decimate` op.

2D is the same shape with no `then.enqueue`: enqueue all sprite jobs with a batch finalize.

`then.from_result` names the field of the parent's result that seeds the follow-up payload. The
image result is already offloaded to `<data_dir>/blobs/` with a `file` path on the row
(`a80d4fd`), so the continuation reads that path.

Known, unchanged: the mesh payload carries the PNG as base64, so a ~1MB payload lands in the jobs
row. That is what `run_trellis_batch` does today. A worker-side blob fetch would remove it;
out of scope here.

---

## 7. Status without a return value

`add_assets` returns `{"ok", "generated", "result"}` today and the games router reports from it.
Under this design the stage has no return value — it ends on a completion minutes later.

- The batch's state is derived from its jobs: pending/claimed → running, all terminal → done,
  `finalized_at` set → staged.
- `GET /games/{id}` reads that instead of a thread's result.
- Progress arrives over the WS (§5) and replays from the events log.

This is the largest piece of work in the design and the one least contained by the queue.

---

## 8. The reaper

A daemon on the same shape as `Autoscaler` (`threading.Event` stop, `_stop.wait(tick)` loop,
`tick()` wrapped so a bad tick logs and skips), ~5s tick, started unconditionally at app startup.

It does **not** live inside the autoscaler:

- The autoscaler is only constructed when `runpod.enabled` and `api_key` are set (`app.py:58`).
  There is no scaler on the home box or in dev, which is exactly where a wedged job needs reaping.
- `scaler/stats.py` is deliberately the only scaler module importing `db.store` — the SQS seam.
  Reaping is a write against `jobs`, and under SQS it would be the broker's visibility timeout,
  not ours.

Duties:

- **Lapsed leases.** Today these are requeued only opportunistically inside `claim_job`
  (`store.py:327`), so a queue that goes quiet leaves a claimed job wedged with its reservation
  held. Sweep unconditionally.
- **Pending past max age** → fail and release the reservation. For chained jobs this replaces the
  enqueuer timeout entirely: no clock starts until a job is real work.
- **Orphaned batches** — the parent failed and no continuation is coming → finalize with what
  landed.
- **Missed finalize** — batch all-terminal, the winner carries `then.finalize`, `finalized_at IS
  NULL`, `finished_at` older than 60s → run it. Covers a restart in the window between the last
  GLB landing and the manifest being written, which would otherwise strand the batch and cost a
  full GPU re-skin (~250s) to redo ~3s of CPU whose inputs are already on disk.

---

## 9. What stays

`queue_client.run_job` keeps the blocking shape for LLM: the fix loop needs its answer inline. It
stops being the only shape. It gains `enqueue_only` (or a sibling `enqueue_job`) for chain heads.

Its deadline starts at enqueue (`queue_client.py:39`) though the setting describes waiting on a
*claimed* job (`settings_schema.py:33`). Assets stop caring — nothing waits — but LLM still does,
and a deep `llm` queue makes tail jobs time out while pending. Split the clock: pending bounded by
the reaper, execution bounded from `started_at`.

---

## 10. Estimates

`estimates.py` reserves 45s per `image` job against a measured ~5s warm, and 240s per `mesh`
against 27s steady. Over-reserving is safe at a 14,400s grant (8 meshes = 1,920s, 13%), so this
does not block. The jobs table has no history to tune against; capture `exec_seconds` from the
next runs.

---

## 11. Tests

- Completion transaction: continuation enqueued before the remaining-count; exactly one completer
  observes zero across concurrent completions; a batch with a refused continuation still
  finalizes.
- Attribution: a continuation's `exec_seconds` debits the parent's `game_id`.
- Worker isolation: `/worker/claim` response carries no `metadata`.
- Reaper: lapsed lease requeued with no claim traffic; over-age pending fails and releases its
  reservation; a batch with `finalized_at IS NULL` past the grace window is finalized once.
- Depth: an 8-mesh run leaves 8 pending rows immediately after `generate_meshes` returns.

---

## 12. Status

Landed: schema, the completion transaction, the ops registry (`maestro/codegen/asset_chain.py`),
the reaper (`db/reaper.py`, started from `api/app.py`), `reskin` chaining, `run_trellis_batch` and
`run_jobs` deleted, and the status migration — `assets_done` and `build_finished` now come from the
finalize, and a `job_done` event fires per completion.

Not done: the claim-clock split (§9). Assets no longer care, but a deep `llm` queue still times
tail jobs out against a clock that started at enqueue.

**Deploy note.** The schema goes straight into `CREATE TABLE`, which only applies to a fresh db —
`CREATE TABLE IF NOT EXISTS` will not add columns to an existing `jobs` table, and the new
`idx_jobs_batch` then fails outright at boot. Any db that predates this needs the columns added
once:

```sql
ALTER TABLE jobs ADD COLUMN metadata TEXT;
ALTER TABLE jobs ADD COLUMN batch_id TEXT;
ALTER TABLE jobs ADD COLUMN finalized_at REAL;
```

Additive, so the existing rows (and their `exec_seconds` history) survive.
