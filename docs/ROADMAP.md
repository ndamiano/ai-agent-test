# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product;
everything else is scaffolding. North star: magical output — a game worth sharing, not "technically
complete."

This file holds the **milestone targets** — what each stage means and its bar to clear. For current
architecture + how it works, see `CLAUDE.md`.

---

## Current architecture — the model writes the game

The fat primitive kit was retired (2026-07-28), and the interface-first pipeline with it. Measured
over a 25-game grid across two local models: given the whole problem and five tools, a local model
writes working browser games in 2–4 minutes, 4/4; given a declared architecture to fill in, the same
model produced unplayable ones in 5–37 minutes. So the model now writes **plain HTML/CSS/JS with no
engine of ours between it and the screen**, and the platform's job is everything around that —
the prompt, the queue, the compute budget, the art, the human's review.

What that deleted: `runtime/engine.js` + `engine3d.js` + `engine.d.ts` + the kit_api docs, the
headless/render/typecheck gates, `interfaces.py` (the declared architecture), `module.py` (the gate
list), `fix_classes.py`, `data_files.py`, `scaffold.py` + its templates, `controls.py`, and
`reskin.py`'s planning half — about 5,000 lines, replaced by ~600.

What survived, and why:
- **The job chain.** A build is still a linear chain of `llm` jobs driven by `/worker/complete`, with
  the whole loop's state in a durable cursor. Nothing about that depended on the kit.
- **The human's review.** The run's PROMPT is shown in the box it will be sent from, and pressing
  Build is what approves it — one artifact, one action, and the text on screen is byte for byte
  the build's user message.
- **The asset chain.** Unchanged transport; what changed is that the GAME asks for its own art with
  `generate_media` while writing the code that uses it — one call, one render, the path back
  immediately — so the stage costs zero planning calls. (On trial since 2026-07-28; see CLAUDE.md
  § Adding a capability for what settles it.)
- **`src/worldgen/`** — unwired. It produced the one thing nothing else did (real scale), and
  re-pointing it to emit data a game reads is an open decision.

The one gate left is `index.html` exists. Everything past that is a human judgement, deliberately
left visible rather than filled with a proxy. See `CLAUDE.md` for the full contract.

---

## Milestones

### Pre-alpha — *validate it FUNCTIONS* (DONE)
Prove the whole loop works for someone who isn't the owner. Inference runs on the owner's PC (5090)
over Tailscale; a couple of trusted people. Manual accounts, credits stubbed, one genre, no payments.
- **Bar (cleared):** a non-owner logged in, requested a game, it built without hand-holding, and
  they played it — validated end-to-end on a second box.
- Built: auth + ownership, per-user access, manual credits (accounts start at 0; admin grants).
  Builds run as a chain of llm jobs on the shared (autoscaled) `llm` queue — the old single-thread
  build queue is gone, so concurrent builds no longer serialize behind one another.

### Private alpha — *validate DEMAND + measure economics*
Deployed off the owner's box; manual accounts + granted credits; basic safety; one genre reliably
good with refund-on-fail. Measure real cost/time/failure per game.
- **Bar:** people want it, and the unit economics are real (`tasks/unit_economics.md`).
- **Detail:** `tasks/launch_plan.md` Stage 0.
- **Deploy:** `DEPLOY.md` — single worker serves API + SPA same-origin, public HTTPS via Tailscale
  Funnel. Pre-open hardening landed: prod launch (no dev reload), pinned/same-origin CORS,
  per-handle login throttle.
- **Compute (landed):** every GPU backend is worker-pull (`worker/agent.py`, queues llm/image/mesh),
  three RunPod worker images + the provisioned network volume, and a queue-driven autoscaler
  (`src/scaler/`) — workers self-exit when their queue drains, the control plane adds pods on
  backlog and reaps dead ones. On-demand RunPod inference is no longer a Release-stage item.
- **Hardening (landed 2026-07-23):** `/docs` dev-only, session TTL 7d, `/play` responses carry a
  containment CSP. Still open: TRUE origin isolation for generated game code — required before any
  game-sharing feature, not before beta (ownership-gating means a game only runs in its owner's
  browser, so the localStorage token it can reach is already its own). (The old build-flood DoS is
  retired: builds charge credits before enqueue, jobs admit against the compute budget, the
  autoscaler absorbs depth, accounts start at 0 credits.) Detail: `DEPLOY.md` § Known deferred
  risks and `tasks/platform_polish.md` P1.

### Public beta (paid) — *first dollar*
Real payments + pricing, safety filter proper, legal set, concurrency for N users.
- **Bar:** paying users; positive unit economics confirmed; no safety/legal gaps.
- **Detail:** `tasks/launch_plan.md` Stage 1.

### Release (v1, public) — *open + scaling*
Self-serve signup, on-demand inference (runpod), parallel assets, breadth of modules + quality/polish.
- **Bar:** open to the public and scales without falling over.
- **Detail:** `tasks/launch_plan.md` Stage 2.

---

## Long-term direction (beyond v1)
The horizon once the business stands — not scheduled work:
- **Breadth of magical output** — more mechanic-modules, richer media (music/SFX/animation), styled
  presentation, real-time games, higher asset quality (the `tasks/` generation-quality group).
- **New artifact types** — long-form fiction, comics/manga, world-building, music.
- **North star — VR world generation:** "explore a world where magic is real in VR" → a playable
  world. Blocked mainly on text-to-3D asset quality (improving fast); world bible / NPC / quest /
  asset-spec stages are LLM-achievable today. Engine target: Godot (OpenXR).
