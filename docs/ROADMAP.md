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
writes **real TypeScript game code against a fat primitive kit**, gated by a pure-Node local gradient
(headless + probe). This buys **any game + local** (bending "good" for now). Full plan +
task breakdown: **`docs/codegen_rebuild_plan.md`**.

- **Phase 1 (DONE):** productized the codegen loop (`src/maestro/codegen/`) on the surviving
  `AgentLoop`; live parity on asteroids/pacman/platformer/collect3d(3D).
- **Phase 2 (DONE):** demolished the IR — deleted `renpy/`, `godot/`, the IR (`ir_assemble`/
  `ir_crossref`/schema), every mechanic module, and their tests; kept the `Module`/`Check`/`Error`
  ABC + `AgentLoop` + `Services`.
- **Phase 6 frontend (DONE):** retargeted `api/` + the SPA to codegen. The games router is
  codegen-only (list/detail/freeze/build/pause/resume/auto-pause/fix/assets → `maestro.codegen.run`);
  chat drafts specs via `tools/chat_tools.py`; build/spec progress emits through `tools/build_events.py`;
  a built run stages to `runtime/games/<id>/` served at `/play`. The GamesPanel reviews the codegen
  spec read-only, drives freeze→build→skin→fix→play, and the IR component/asset browser is deleted.
  Also finished the demolition: removed the leftover `renpy/`/`godot/`/`utils/` dirs, `maestro/spec.py`,
  `tools/tts_tools.py`, and the dead IR asset generators in `comfyui_tools.py`.
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
- **Data-file stage (IN PROGRESS):** content out of code. The model designs per-game datasets
  (`game/data/manifest.json` + rows; every row carries an implicit id/name/look/presence/size
  envelope), a deterministic gate validates them and generates a typed `game/data.ts` games import
  (a new blocking `data` check between plan and authoring). Fixes three things at once: content
  edits become row edits, the fix loop sees schema + one example row instead of inline stat blocks,
  and asset planning becomes deterministic enumeration from `look` rows (kills the 3–8 sprite
  ceiling). Design + task breakdown: `docs/codegen_rebuild_plan.md` § Data files.
- **Control-scaffold stage (landed):** the pipeline, not the model, wires controls. EVERY
  build seeds a GENERATED `game/main.ts` from a per-scheme template (config + movement + frame
  loop + the dialogue loop when the spec uses it); the model authors gameplay behind the `game.ts`
  hooks (createState/init/update/draw/hud), and the probe gains a scheme-aware `dead_movement`
  invariant (movement keys must displace the steered entity — an action key mutating state can no
  longer green a game the player can't steer, the failure two live builds shipped). Design +
  breakdown: `docs/codegen_rebuild_plan.md` § Control scaffold.
- **Worldgen × scaffold merged (landed):** the two pre-seeds were rival owners of `main.ts`, so a
  world spec took the older path and lost every control-layer guarantee. They are now orthogonal
  axes — worldgen seeds the PLACE (`world.ts`), the scaffold seeds the CONTROLS (`main.ts`) — and a
  village RPG plans, decomposes and gates exactly like every other game. Terrain following and
  building collision became scaffold passes (`state.ground` / `state.walls`), wired by main.ts
  itself on a world game — a live build proved prompting for them isn't enough: it used the whole
  WORLD API correctly and still never assigned them, leaving the player walking through buildings in
  mid-air with every gate green. Next: a probe invariant for "the player is on the ground", so this
  class of defect is caught rather than prevented only by construction.
- **Gamepad controls normalize (landed):** a spec may describe a gamepad; the runtime has none
  (`Input` is keys + pointer), so `draft_spec`/`freeze_spec` MAP the intent onto real keys —
  stick→WASD, right stick→Mouse, A/B/X/Y→E/Q/F/R, colliding aliases moved to a free key. A live
  build drew `LEFT_STICK`/`A_BUTTON` and `unbound_control` became unsatisfiable, grinding the fix
  loop to the step cap. Same shape as the probe's existing mouse-token normalization: map the
  vocabulary, never forbid it.
- **Collision + actions (landed):** two kit widenings behind the scaffold. `kit.collideWorld` is
  the ONE 2D solid pass (tile pushout + solid-pair separation; entities tag `solid: true`; the 2D
  scaffold templates call it after the hook update) — the shipped walk-through-walls and
  enemies-stack incidents were both this missing pass. `kit.register`/`kit.bindings` make spec key
  bindings machine-readable: every runner fires registered handlers on the pressed edge, and the
  probe gains `dead_action` (every binding must act), `unbound_control` (every non-movement spec
  control must be registered — the spec's controls map now rides into the probe) and
  `solid_overlap` (no solid entities interpenetrate at rest). Design:
  `docs/codegen_rebuild_plan.md` § Collision + actions.
- **Spec-vs-code audit (landed 2026-07-23):** the gates prove a game RUNS, not that its declared
  mechanics exist — a live build shipped every gate green with dead gold, unreachable floors and
  zero-damage combat. A build now ends on SPEC-EXHAUSTED, not errors-zero: when the gates go green,
  the frozen spec's mechanically-enumerated claims (controls/mechanics/win/lose/render; movement
  excluded — scaffold law) are each judged by a bounded read→verdict subloop
  (`maestro/codegen/audit.py`, shape `audit` in `build_steps`): the judge reads the files it needs
  and cites the traced path, per-claim verdicts logged durably to `audit_verdicts.jsonl`. Validated
  against human play-notes: single-shot judging over pasted sources was wrong both ways (unanimous
  "broken" on working mechanics; 11/12→5/12 swings on unchanged code) and 3-vote majorities did NOT
  fix the bias — grounding did (traced judge agreed with the human on ~6/7 confident claims).
  Delivered claims ANCHOR later rounds and future builds (flip only with cited regression
  evidence), so rounds ratchet. Failed claims become fixes on the `fix_from_note` lane, then
  re-gate → re-audit until a round returns zero findings (round + step caps as fail-open backstops;
  audit builds default 200 steps). CLI: `--audit <run_id>`. Also widened `single_mover` to the two
  shipped blind spots (input-guard movers, movers inside `kit.register` handlers), register-strip
  deterministic. Known ceiling: the audit certifies the spec AS WRITTEN — a shallow spec certifies
  shallow (the unwinnable-fight case: no claim promises winnable combat); spec richness is a
  stage-1 lever.
- **Next:** finish the data-file stage → Phase 3 harden the probe → Phase 4 widen the kit → finish
  Phase 5 assets (styled-prompt stage) → Phase 7 the "good" tier (deferred cloud play-critic).

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
- **Hardening (landed 2026-07-23):** chat rate-limited (uncharged inference, 30 turns/hour),
  `/docs` dev-only, session TTL 7d, `/play` responses carry a no-exfiltration CSP. Still open:
  TRUE origin isolation for generated `game.js` — required before any game-sharing feature, not
  before beta (ownership-gating means a game only runs in its owner's browser). (The old
  build-flood DoS is retired: builds charge credits before enqueue, jobs admit against the
  compute budget, the autoscaler absorbs depth, accounts start at 0 credits.) Detail:
  `DEPLOY.md` § Known deferred risks and `tasks/production_hardening.md`.

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
