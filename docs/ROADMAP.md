# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product;
everything else is scaffolding. North star: magical output — a game worth sharing, not "technically
complete."

This file holds the **milestone targets** — what each stage means and its bar to clear. For current
architecture + how it works, see `CLAUDE.md`.

---

## Current architecture — the codegen rebuild (branch `codegen-rebuild`)

The universal Game IR was retired: it was a {VN, point-click, walking-RPG} engine faking
universality, topped out at "valid" not "good", and couldn't express most games. The model now
writes **real JS game code against a fat primitive kit**, gated by a pure-Node local gradient
(headless + probe). This buys **any game + local** (bending "good" for now). Full plan +
task breakdown: **`docs/codegen_rebuild_plan.md`**.

- **Phase 1 (DONE):** productized the codegen loop (`src/maestro/codegen/`) on the surviving
  `AgentLoop`; live parity on asteroids/pacman/platformer/collect3d(3D).
- **Phase 2 (DONE):** demolished the IR — deleted `renpy/`, `godot/`, the IR (`ir_assemble`/
  `ir_crossref`/schema), every mechanic module, and their tests; kept the `Module`/`Check`/`Error`
  ABC + `AgentLoop` + `Services`. The `api/` build endpoints still call the old entry points lazily
  (retargeted in Phase 6).
- **Phase 5 assets (IN PROGRESS):** skin the placeholder shapes. A post-build reskin stage
  (`maestro/codegen/reskin.py`, CLI `--assets <run_id>`) plans a sprite set from the spec + source,
  rewrites the draw code to prefer `kit.sprite(id)` with the shape as fallback, re-gates, then renders
  the sprites (ComfyUI) into `game/assets/` + `assets.json`; the runtime preloads them. Purely
  additive — a run with no images still passes every gate and renders as shapes. Mode-dispatched:
  3D games instead plan meshes, tag entities `mesh:"id"`, render an image (ComfyUI) → GLB (TRELLIS),
  which `engine3d` preloads + scales to each entity's box. 2D sprites + 3D meshes done; also bumped
  render to 16:9 HD filling the window. Styled-prompt stage pending.
  *(Swapped ahead of frontend: a skinned game validates via the runtime harness; in-app UX is
  scaffolding.)*
- **Next:** Phase 3 harden the probe → Phase 4 widen the kit → finish Phase 5 assets → Phase 6
  frontend (retarget `api/` to codegen) → Phase 7 the "good" tier (deferred cloud play-critic).

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
- **Deploy:** `DEPLOY.md` — single worker serves API + SPA same-origin, public HTTPS via Tailscale
  Funnel. Pre-open hardening landed: prod launch (no dev reload), pinned/same-origin CORS,
  per-handle login throttle.
- **Deferred risks (fix before public beta):** single-GPU DoS (unbounded build queue, no per-user
  in-flight cap), 30-day session TTL, public `/docs`, and the new codegen surface: the generated
  `game.js` runs in the player's browser — sandbox it (served from a null-origin/sandboxed iframe,
  no same-origin API access) so a malicious/broken generation can't touch the app. Detail in
  `DEPLOY.md` § Known deferred risks.

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
