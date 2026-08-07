# Maestro Roadmap

What each stage of `docs/vision.md` means in practice, its bar, and where it stands. The vision
holds the goal; `CLAUDE.md` holds the architecture and changes as fast as the measurements do. This
file is the middle: what we are working on, and what would tell us to move on.

Two tracks run at once. The **product track** (vision stages 1–3) is whether the games are worth
having. The **delivery track** is whether a stranger can pay for one and get it safely. They gate
each other in one direction only: delivery may run ahead, but no delivery stage that takes money
opens before the product stage it sells has cleared.

---

## Where we are — 2026-07-31

A build is one model, six tools, and a transcript, writing plain HTML/CSS/JS. It asks for its own
art while it writes the code that uses it. Two gates stand between a build and `built`: an
`index.html` exists, and the error gate — the staged game is opened headless and an uncaught
error re-enters the fix machine, one error per round (rebuilt 2026-08-02 from the 2026-07-30
measurement). Whether the game is any *good* is a question a person answers by playing it.

The platform under that is further along than the games are: auth, credits, exec-second metering,
containerized deploy, a worker-pull queue with three GPU queues and a RunPod autoscaler — all
landed and running. **Product track is stage 1. Delivery track is private alpha.** The gap between
them is deliberate and is the main thing to watch: it would be easy to keep building platform
because platform work has clear done-conditions and "good game" does not.

---

## Product track

### Stage 1 — Any game asked for comes back running — **IN PROGRESS**

Whatever the genre, whatever the request, the build ends with something that opens and responds.

- **Bar:** a battery of varied requests — 2D and 3D, arcade and turn-based and narrative — where
  every build produces a game that loads, takes input, and does not crash. Failures are
  investigated, not averaged away.
- **Landed:** the no-contract build loop (measured 2–4 min, 4/4 working on a 25-game grid,
  2026-07-27); the done-nudge (2026-07-30, `docs/experiments.md`); `generate_media` so a game can
  ask for its own art; the human-note fix path.
- **Open:** no standing battery is *run* on a schedule — the grids were one-off experiments. The
  known recurring defects from those grids, none yet earning more than a prompt line: 3D scenes lit
  near-black, fixed canvas with no window scaling, silent games, arrow-keys-only input.
- **On trial:** `generate_media` entered unmeasured (2026-07-28). What settles it is COVERAGE — how
  much of what the player sees got art — not whether it is called at all. An unused schema costs
  every turn of every build. First count (2026-08-01, `asset_use`): of 352 assets asked for across
  the staged games, 128 rendered and were never referenced and 115 referenced paths were never asked
  for. The done-nudge now carries both lists; whether telling the model changes the number is the
  open question. Its `kind` (sprite/tile/scene/mesh, 2026-08-01) is not yet measured
  either: before it, every ask rendered through the one item-icon workflow, so a game's floor tiles
  came back matted to fragments. A kind now picks the model and the post-op (2026-08-06 bake-off):
  sprites and scenes through NetaYume Lumina, tiles through DreamShaperXL then min-cut quilting to
  a seamless square — and both samplers run at a real cfg, so the negative prompt works.
  A landed render is now checked against its kind and the defect shown in the gallery — BROKEN only,
  never whether the picture suits the game, which stays the same human question as whether the game
  plays right.
- **Work lives in:** `tasks/build_loop.md`, `tasks/quality_backlog.md`.

### Stage 2 — Any game asked for comes back good — **NOT STARTED**

The hard one, and the one everything downstream waits on.

- **Bar:** the owner plays a battery of builds and is happy with them. This is a human judgement on
  purpose and stays that way — "good" is not definable in code, and every attempt to make a gate
  judge it has been measured to cost more than it returned (see `CLAUDE.md`, and
  `docs/experiments.md` for the judge-then-fix result: 208 of 227 steps, worse in round 2 than
  round 1). There is no metric to hit here. There is a point at which the owner calls it, and the
  roadmap is waiting on that call rather than on a number.
- **Explicitly rejected as the answer:** local LLM-judge gates, "the game must do X" checks, any
  automated round that fixes what a previous round already got right.
- **Open question:** what a play-critic would look like if one ever returns — it would have to
  answer the failure that retired the last one, not just be built better.
- **Work lives in:** `tasks/quality_backlog.md`, `tasks/asset_quality.md`, `tasks/game_style.md`,
  `tasks/game_media.md`.

### Stage 3 — Cheap enough that making games is casual — **PARTLY LANDED**

Someone tries an idea, hates it, tries again, without doing arithmetic first.

- **Bar:** a measured cost per game that supports a price a person spends without thinking, with the
  margin coming from utilization rather than from charging more.
- **Landed:** exec-second metering debited to the owning game, the compute budget that admits or
  refuses jobs, and the queue-driven autoscaler (workers self-exit on drain; pods come up on
  backlog). Builds run concurrently rather than serializing behind one queue.
- **Measured 2026-07-31 (dev box, `tasks/unit_economics.md`):** a finished game costs **~$0.11** of
  GPU at the rented-5090 rate — median 386 GPU-s including every fix, p90 $0.58, worst $2.26. 95%
  of the spend is llm turns, 5% art. 31% of first builds ended failed, measured against the 80- and
  120-turn caps in force that week.
- **Open:** the same table off rented pods (cold start and a different serving stack are not in
  these seconds), a failure rate at the 200-turn cap, and the price itself. Cheap to do, and it
  blocks every paid stage.

---

## Delivery track

### Pre-alpha — validate it FUNCTIONS — **DONE**

A non-owner logged in, requested a game, it built without hand-holding, and they played it —
end-to-end on a second box. Auth + ownership, per-user access, manual credits (accounts start at 0;
admin grants).

### Private alpha — validate DEMAND and measure the economics — **IN PROGRESS**

Deployed off the owner's box, manual accounts, granted credits, no payments.

- **Bar:** people want it, and real COGS/failure data exists.
- **Landed:** containerized deploy + public HTTPS (`deploy.md`); prod hardening — no dev reload,
  pinned same-origin CORS, per-handle login throttle, `/docs` dev-only, 7-day session TTL,
  containment CSP on `/play`; safety Phase 1 (fail-closed CSAM-adjacent block on prompt input and
  the image seam); alpha-tier ToS + privacy with 18+ acceptance; all GPU work on worker-pull queues
  with the autoscaler live; URL routing + settings page + in-page iframe play with console-error
  capture and a human-gated fix modal (2026-08-01); origin isolation LIVE in prod (verified
  2026-08-03: `play.origin` = gamesummonerusercontent.com framed by gamesummoner.com, DNS + TLS
  serving) — non-owner play (demos, sharing) is safe on the isolation front; self-hosted product
  analytics (batched `/api/events` into the durable event store, SPA funnel tracking incl. the
  enhance-toggle A/B, admin rollup) and the backup story (DB snapshots for the DBs, per-run
  archives for the games, a runnable restore drill — `docs/backups.md`), both 2026-08-03.
- **Open:** the economics measurement (stage 3 above), and enough product-track progress that
  showing it to people is worth their time.
- **Detail:** `tasks/launch_plan.md` Stage 0.

### Public beta, paid — first dollar — **BLOCKED on product stage 2**

- **Bar:** paying users, positive unit economics confirmed, no safety or legal gaps.
- **Needs:** payments are LIVE (2026-08-03: hosted Checkout on live keys, $5/credit packages,
  signed-webhook + redirect-return completion, refunds revoke credits — a real purchase and
  refund verified end to end); safety Phase 2 landed 2026-08-03 (post-gen NSFW verdict on every
  render with fail-closed control-plane policy, artifact text gate that HOLDS a flagged build,
  durable violations + admin view — `tasks/safety_filter.md`; hash-matching stays parked pending
  counsel). Remaining: the full `tasks/legal_ops.md` checklist with counsel review, and the
  game-execution sandbox.
- **Detail:** `tasks/launch_plan.md` Stage 1.

### Public release — open and scaling — **NOT STARTED**

- **Bar:** open to the public, scales without falling over.
- **Needs:** open self-serve signup (invite-code signup landed 2026-08-03 — admin-minted codes,
  per-IP throttled, atomic redemption; "open" means dropping the invite gate, a policy call, not
  code), and whatever
  the beta's load teaches about the autoscaler's ceilings.
- **Detail:** `tasks/launch_plan.md` Stage 2.

---

## Vision stages 4–6 — after the business stands

Not scheduled, but the direction is decided, and one of them has a prerequisite that must be built
before its stage starts.

### Stage 4 — A maker can hand their game to anyone

A link that works on any device for someone who has never heard of Maestro.

**Hard prerequisite: true origin isolation for generated game code.** Today a game only runs in its
owner's browser, so the localStorage token it can reach is already its own — ownership-gating is
what makes the current posture safe. Sharing removes exactly that protection, so isolation is
required *before any sharing feature*, not before beta. `deploy.md` § Known deferred risks,
`tasks/platform_polish.md` P1.

### Stage 5 — Games are worth paying for

The buying mechanism is deliberately unspecified until games are good enough that someone would want
one (see `vision.md`). Groundwork that already exists: run ownership, the credit ledger, and an
IP-ownership question that `tasks/legal_ops.md` has to answer before anyone sells anything.

### Stage 6 — We own the stack

Rented RunPod GPUs today. Owned hardware later, our own models after that. Nothing here is
scheduled; the point of writing it down is that every architecture decision should keep the exit
open — which is why the control plane speaks a canonical request and the worker owns the wire
format, and why nothing proprietary is in the loop.

---

## Horizons — not commitments

Things we would love to make once the above stands:

- **Richer output** — music, SFX, animation, real-time games, higher asset quality.
- **Other artifact types** — long-form fiction, comics, world-building, music.
- **VR world generation** — "explore a world where magic is real" as a playable world. Blocked
  mainly on text-to-3D asset quality; the world-bible / NPC / quest stages are achievable today.
  Engine target would be Godot (OpenXR).
- **`src/worldgen/`** — a standalone procedural world generator, currently unwired. It produced the
  one thing nothing else did: real scale. Re-pointing it to emit data a game reads is an open
  decision, not a dependency.
