# Launch Plan — Current → Business

The **meta-doc**: sequences the scattered platform + quality work into an ordered path to first
dollar, and defines each stage's bar. Points into the other task files rather than restating them.

## The two axes (why "product vs business" is slightly false)
- **Can it make a GOOD game?** (quality/product) — `quality_backlog.md`, `asset_quality.md`, modules.
- **Can a stranger pay + get one safely?** (business/infra) — auth, payments, deploy, safety, scale.

They're separable, but a business that ships bad games churns — so a **minimum reliability bar is a
business requirement**, and you do **NOT need breadth (`module_catalog`) to launch**. Launch narrow +
good, expand later.

## Stage 0a — Pre-alpha (immediate — validate it FUNCTIONS)
Before demand validation, prove the loop works for a non-owner. Inference stays on the owner's PC
(5090) over Tailscale; a couple of trusted people; manual accounts, stubbed credits, no payments.
**The build playbook + copy-paste agent prompts live in `pre_alpha_kickoff.md`.** A narrow subset of
Stage 0: auth+ownership, a single-GPU build queue, per-user WS routing, stubbed credits. Everything
else below is Stage 0 (private alpha) and later.

## Stage 0 — MVP / Private Alpha (no pricing required)
**Goal:** put game generation in front of a few trusted people; validate demand + that it works;
measure real cost/time/failure (`unit_economics.md`). No real payments, no self-serve signup.

- [ ] **Auth (manual accounts)** — `auth_and_billing.md` T1 + T2 (login + run ownership). No signup.
- [ ] **Credits stubbed** — ledger + deduct/refund (`auth` T3) with **manual grants** (`auth` T4 admin
      op). No payment integration yet.
- [ ] **Deployed + reachable** — `build_deploy.md` T1 (CI) + T2 (containerize) + T3 (datastore for
      users/credits). Can't run an alpha off a dev shell.
- [ ] **Reliably good on ONE genre** — narrow to the best-working path (VN/dialogue). Judge-in-loop
      (`quality_backlog.md` §1) + an acceptable failure rate + refund-on-fail. Breadth deferred.
- [ ] **Download works** — already does (Ren'Py distribute / Godot export); confirm delivery in the
      deployed env.
- [ ] **Safety posture** — trusted alpha lowers risk, but image gen is uncensored: at minimum start
      `safety_filter.md` Phase 1 (research) and a basic block; don't hand it to anyone outside a
      trusted circle without it.
- [ ] **Minimal legal** — `legal_ops.md` alpha tier (short ToS + privacy note).
- [ ] **Measure** — real cost/time/failure per game (`unit_economics.md`) during these builds.

**Exit:** people use it, want it, and you have real COGS/failure data.

## Stage 1 — Paid Beta (first dollar)
**Goal:** charge a small cohort; validate the $5 hypothesis.

- [ ] **Real payments** — `auth_and_billing.md` T4 with a concrete provider (Stripe or MoR).
- [ ] **Pricing set** — from Stage 0 data; base = 1 credit / speed-up = 2 (`unit_economics.md`).
- [ ] **Safety filter proper** — `safety_filter.md` Phase 2 (non-negotiable once public-ish).
- [ ] **Legal set** — `legal_ops.md` full checklist (ToS/privacy/refunds/IP/age-gate/entity).
- [ ] **Concurrency S1** — `scaleout.md` S1 (queue + per-run isolation + WS routing) so N payers
      don't collide.
- [ ] **Speed-up tier** — A100 rental path (verify economics first).

**Exit:** paying users, positive unit economics confirmed, no safety/legal gaps.

## Stage 2 — Public Launch & Scale
- [ ] **Self-serve signup** — the deferred `auth_and_billing.md` launch-gate.
- [ ] **On-demand inference** — `scaleout.md` S3 (runpod spin-up) + S2 (parallel assets) for capacity.
- [ ] **Ownership / resale tier** — the 100-credit resell path + "contact me" baseline + site listing
      (`legal_ops.md`, `unit_economics.md`).
- [ ] **Breadth + polish** — `module_catalog.md`, `game_media.md`, `game_style.md`, `asset_quality.md`,
      `realtime_substrate.md` — the growth engine, now that the business stands.

## "Minimum to charge $1"
Real payment → credit ledger deducts → a build runs (isolated, safe) → a good game downloads →
refund on failure. That's Stage 1's core loop. Everything before it is Stage 0; everything after is
scale.

## Critical-path ordering (across files)
`unit_economics` spike (during alpha) ∥ `safety_filter` research →
**auth T1/T2 → build_deploy T1-T3 → auth T3 (credits) → [Stage 0 alpha] →
payments T4 + legal_ops + safety Phase 2 + scaleout S1 → [Stage 1 paid] →
self-serve + scaleout S2/S3 + breadth → [Stage 2 public]**

## Parked
- Waitlist / how the trusted alpha cohort is sourced.
- Whether Stage 1 is invite-only or open — depends on safety + concurrency readiness.
