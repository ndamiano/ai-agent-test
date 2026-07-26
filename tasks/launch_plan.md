# Launch Plan — Current → Business

Verified: 2026-07-25

The **meta-doc**: sequences the scattered platform + quality work into an ordered path to first
dollar, and defines each stage's bar. Points into the other task files rather than restating them.

## The two axes (why "product vs business" is slightly false)
- **Can it make a GOOD game?** (quality/product) — `quality_backlog.md`, `asset_quality.md`, modules.
- **Can a stranger pay + get one safely?** (business/infra) — auth, payments, deploy, safety, scale.

They're separable, but a business that ships bad games churns — so a **minimum reliability bar is a
business requirement**, and you do **NOT need genre breadth to launch**. Launch narrow +
good, expand later.

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
      `docs/DEPLOY.md`); validated on a second box, non-owner drove the full loop. T1 (CI) now
      exists too (`.github/workflows/ci.yml`).
- [ ] **Reliably good on ONE genre** — narrow to the best-working path (VN/dialogue). Judge-in-loop
      (`quality_backlog.md` §1) + an acceptable failure rate + refund-on-fail. Breadth deferred. NOT STARTED.
      → done when: `quality_backlog.md`'s Q1 items are all checked and a VN failure-rate figure is in `unit_economics.md`
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
- [ ] **Measure** — real cost/time/failure per game (`unit_economics.md`) during these builds.
      → done when: `unit_economics.md`'s "Measure real cost/time per game" and "failure/retry rate" items are checked

**Exit:** people use it, want it, and you have real COGS/failure data.

## Stage 1 — Paid Beta (first dollar)
**Goal:** charge a small cohort; validate the $5 hypothesis.

- [ ] **Real payments** — `platform_polish.md` P2 with a concrete provider (Stripe or MoR).
      → done when: `src/auth/credits.py`'s `get_provider()` returns a concrete provider, not `UnconfiguredProvider`
- [ ] **Pricing set** — from Stage 0 data; base = 1 credit / speed-up = 2 (`unit_economics.md`).
      → done when: `unit_economics.md`'s "Set the launch price" task is checked
- [ ] **Safety filter proper** — `safety_filter.md` Phase 2 (non-negotiable once public-ish).
      → done when: all six `safety_filter.md` Phase 2 items are checked
- [ ] **Legal set** — `legal_ops.md` full checklist (ToS/privacy/refunds/IP/age-gate/entity).
      → done when: all seven `legal_ops.md` checklist items are checked
- [ ] **Concurrency S1** — `platform_polish.md` P3 (per-run inference isolation; queue + WS routing already shipped) so N payers
      don't collide.
      → done when: `src/api/routers/websocket.py` routes events per-user and jobs carry per-run isolation in `src/db/store.py`
- [ ] **Speed-up tier** — A100 rental path (verify economics first).
      → done when: `unit_economics.md`'s "Verify the premium tiers" task is checked and an A100 queue config exists in `settings.json`
- [ ] **Production hardening** — the four security deferrals now live in
      `tasks/platform_polish.md` P1; land them before charging strangers.
      → done when: `grep -rn "sandbox" src/worker/ src/maestro/` is non-empty (the remaining game-execution sandbox item)

**Exit:** paying users, positive unit economics confirmed, no safety/legal gaps.

## Stage 2 — Public Launch & Scale
- [ ] **Self-serve signup** — the deferred `platform_polish.md` launch-gate.
      → done when: a public signup route exists in `src/auth/router.py` (today it has none, by design)
- [ ] **On-demand inference** — runpod spin-up + parallel assets, both shipped (`finished.md`) — capacity is now a scaling-config question.
      → done when: `runpod.enabled` is `true` in the production `settings.json` and `src/scaler/autoscaler.py` is live
- [ ] **Ownership / resale tier** — the 100-credit resell path + "contact me" baseline + site listing
      (`legal_ops.md`, `unit_economics.md`).
      → done when: `legal_ops.md`'s IP-ownership item is checked and `grep -rn "resale" src/api/` is non-empty
- [ ] **Breadth + polish** — `game_media.md`, `game_style.md`, `asset_quality.md`, kit widening
      (new primitive families) — the growth engine, now that the business stands.
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
