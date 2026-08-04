# Launch Plan — Current → Business

Verified: 2026-08-03

The **meta-doc**: sequences the scattered platform + quality work into an ordered path to first
dollar, and defines each stage's bar. Points into the other task files rather than restating them.

## The two axes (why "product vs business" is slightly false)
- **Can it make a GOOD game?** (quality/product) — `quality_backlog.md`, `asset_quality.md`,
  `build.txt`.
- **Can a stranger pay + get one safely?** (business/infra) — auth, payments, deploy, safety, scale.

They're separable, but a business that ships bad games churns — so a **minimum reliability bar is a
business requirement**. Reliability is measured across the genre battery, not on one genre: the
ordering is breadth first (`docs/vision.md`, `docs/roadmap.md` product stage 1), because a maker
who picks from a menu of supported genres is not describing the game in their head.

## Stage 0a — Pre-alpha (immediate — validate it FUNCTIONS)
Before demand validation, prove the loop works for a non-owner. Inference stays on the owner's PC
(5090) over Tailscale; a couple of trusted people; manual accounts, stubbed credits, no payments.
A narrow subset of
Stage 0: auth+ownership, a single-GPU build queue, per-user WS routing, stubbed credits. **All four
build pieces DONE** (auth+ownership T1/T2, build queue, per-user WS routing, credits stub T3 — suite
green). **Validated end-to-end on a second box** — a non-owner drove the full loop. Everything else
below is Stage 0 (private alpha) and later.

## Stage 0 — MVP / Private Alpha (no pricing required)
**Goal:** put game generation in front of a few trusted people; validate demand + that it works;
measure real cost/time/failure (`unit_economics.md`). No real payments, no self-serve signup.

- [x] **Auth (manual accounts)** — login + run ownership, no signup (shipped — `finished.md`).
- [x] **Credits stubbed** — ledger + atomic deduct (`auth` T3) + manual grants (`auth` T4 admin
      op) DONE. The free initial grant was removed — accounts start at **0 credits**; alpha testers
      get manual grants (`auth/cli.py grant`). No payment integration yet (T4 seam only).
- [x] **Deployed + reachable** — containerize + datastore/volumes + deploy, all shipped (`finished.md`) — T4
      (deploy) DONE (Dockerfile + compose + named-volume persistence + `scripts/deploy.sh` +
      `docs/deploy.md`); validated on a second box, non-owner drove the full loop. T1 (CI) now
      exists too (`.github/workflows/ci.yml`).
- [ ] **Reliably running across the genre battery** — the battery runs regularly now
      (`docs/experiments.md`: 25-game grids, the staged ladder), the error gate zeroed the
      load-dead class, staged construction carries the hardest systems, and the goodwill refund
      path exists (admin). Open half: the QUALITY bar is still the owner playing each game — no
      per-battery failure-rate figure is written down.
      → done when: a per-battery failure-rate figure is in `unit_economics.md`
- [x] **Delivery works** — the `/play` web harness serves the staged bundle
      (`runtime/games/<id>/`, cookie-gated). Browser play IS the product (decision 2026-07-23):
      the old Ren'Py/Godot download made sense when games were desktop builds; a downloadable
      export is a possible later add, not launch work.
- [x] **Safety posture** — `safety_filter.md` Phase 1 (research, `safety_phase1_notes.md`) done +
      a basic fail-closed CSAM-adjacent block landed (chat/spec input + the image-generation
      seam, `src/tools/safety.py`). Still pre-alpha tier (keyword/pattern only, no classifier/
      hash-matching) — don't hand it to anyone outside a trusted circle without Phase 2.
- [x] **Minimal legal** — alpha-tier ToS + privacy note live (`frontend/public/terms.html` +
      `privacy.html`, linked from login with 18+ acceptance). Counsel review before paid.
- [~] **Measure** — real cost/time/failure per game (`unit_economics.md`). Prod now meters
      exec-seconds per job to the owning game and the admin cost panels show paid vs billed
      GPU-seconds per queue; what remains is writing the per-game figures into
      `unit_economics.md`.
      → done when: `unit_economics.md`'s "Measure real cost/time per game" and "failure/retry rate" items are checked

**Exit:** people use it, want it, and you have real COGS/failure data.

## Stage 1 — Paid Beta (first dollar)
**Goal:** charge a small cohort; validate the $5 hypothesis.

- [x] **Real payments** — Stripe live (2026-08-03): hosted Checkout, signed webhook + redirect
      completion, refunds/chargebacks revoke credits; a real purchase + refund verified end to end.
- [x] **Pricing set** — $5 = 1 credit = 3h compute (2026-08-03). The old speed-up/A100 tier idea
      is superseded by the flat price; revisit only if the economics ask for it.
- [x] **Safety filter proper** — Phase 2 landed and deployed 2026-08-03 (`safety_filter.md`):
      post-gen image verdicts fail closed, artifact text gate holds flagged builds, durable
      violations + admin view. The appeal path and CSAM reporting stay parked with counsel.
- [ ] **Legal set** — `legal_ops.md` full checklist (ToS/privacy/refunds/IP/age-gate/entity).
      Owner ruling 2026-08-03: counsel input wanted, not gating.
      → done when: all seven `legal_ops.md` checklist items are checked
- [x] **Concurrency** — build-as-jobs made N concurrent builds N independent job chains, WS routes
      per-user; the vestigial globals are `platform_polish.md` P3, polish not correctness.
- [ ] **Production hardening residue** — `platform_polish.md` P1.8: the error-gate probe runs game
      JS on the control-plane box with open egress. Land before strangers.
      → done when: P1.8 is checked

**Exit:** paying users, positive unit economics confirmed, no safety/legal gaps.

## Stage 2 — Public Launch & Scale
- [x] **Self-serve signup** — invite-code signup live (2026-08-03): public route, admin-minted
      codes, per-IP throttled. "Open" means dropping the invite gate — a policy call, not a build.
- [x] **On-demand inference** — the autoscaler is live on prod across all three queues; capacity
      is a scaling-config question (`runpod.queues` in prod settings).
- [ ] **Ownership / resale tier** — the 100-credit resell path + "contact me" baseline + site listing
      (`legal_ops.md`, `unit_economics.md`).
      → done when: `legal_ops.md`'s IP-ownership item is checked and `grep -rn "resale" src/api/` is non-empty
- [ ] **Polish + richer output** — `game_media.md`, `game_style.md`, `asset_quality.md` — the
      growth engine, now that the business stands.
      → done when: `game_media.md`, `game_style.md`, and `asset_quality.md` each have zero remaining `- [ ]` lines

## "Minimum to charge $1"
Real payment → credit ledger deducts → a build runs (isolated, safe) → a good game is playable
at /play → refund on failure. That's Stage 1's core loop. Everything before it is Stage 0; everything after is
scale.

## Critical-path ordering (across files)
`unit_economics` spike (during alpha) ∥ `safety_filter` research →
**auth T1/T2 → build_deploy T1-T3 → auth T3 (credits) → [Stage 0 alpha] →
payments T4 + legal_ops + safety Phase 2 + scaleout S1 → [Stage 1 paid] →
self-serve + scaleout S2/S3 + breadth → [Stage 2 public]**

## Parked
- Waitlist / how the trusted alpha cohort is sourced.
- Whether Stage 1 is invite-only or open — depends on safety + concurrency readiness.
