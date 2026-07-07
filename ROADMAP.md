# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product;
everything else is scaffolding. North star: magical output — a game worth sharing, not "technically
complete."

This file holds the **milestone targets** — what each stage means and its bar to clear. The detailed
work lives in **`tasks/`** (see `tasks/README.md` for the index; `tasks/launch_plan.md` sequences the
execution across those files). For current architecture + how it works, see `CLAUDE.md`.

---

## Milestones

### Pre-alpha — *validate it FUNCTIONS* (current focus)
Prove the whole loop works for someone who isn't the owner. Inference runs on the owner's PC (5090)
over Tailscale; a couple of trusted people. Manual accounts, credits stubbed, one genre, no payments.
- **Bar:** a non-owner logs in, requests a game, it builds without hand-holding, and they download a
  working game.
- **Build path:** `tasks/pre_alpha_kickoff.md` (the playbook) → auth + ownership, a build queue that
  serializes on the single GPU, per-user access, stubbed credits.

### Private alpha — *validate DEMAND + measure economics*
Deployed off the owner's box; manual accounts + granted credits; basic safety; one genre reliably
good with refund-on-fail. Measure real cost/time/failure per game.
- **Bar:** people want it, and the unit economics are real (`tasks/unit_economics.md`).
- **Detail:** `tasks/launch_plan.md` Stage 0.

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
