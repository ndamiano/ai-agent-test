# Safety Filter — Illegal-Content Prevention

**Type: research → implement.** The shape isn't settled; the research phase decides the approach,
then implement. This is a **launch gate** (legal/trust-safety), not optional.

## Why
Public launch means untrusted users driving generation. The platform must not produce illegal
content. The single highest-risk vector is the **local, uncensored image model** (SDXL/Illustrious
has no built-in guardrails and is prompt-steerable to illegal imagery — CSAM is the critical legal
category, with mandatory-reporting obligations). Text output is a secondary risk. Nothing today
filters either.

## Guardrails (READ FIRST)
- **Do not rely on the generation model refusing.** Local models don't. The filter is a separate,
  external layer.
- **Cover images AND text.** An LLM-only moderation view misses the biggest risk (the image model).
- **Fail closed for illegal categories.** On uncertainty in a hard-illegal category, block — do not
  ship-and-hope.
- **Separate illegal from merely mature.** Mature/dark creative themes are in-scope for the product
  (dark stories, violence-in-fiction); *illegal* content is the line. Don't build a prudish filter
  that guts legitimate creative range — target the illegal categories precisely.
- **CSAM is special.** It likely carries legal reporting + preservation obligations and may need
  hash-matching infra (e.g. PhotoDNA-class services, access-gated) — not just a classifier. The
  research must surface these obligations; get legal input before finalizing.

## Background (VERIFIED)
- **No content moderation anywhere.** Grep for moderation/nsfw/csam/content_filter finds only log
  dumps + the standard `nsfw` **negative-prompt** token in SDXL prompts (`src/tools/comfyui_tools.py`,
  `src/renpy/fns.py`) — not a safety system.
- **Content-flow hook points** (where a filter could sit), from prior recon:
  1. **Input** — user request into chat (`src/api/routers/chat.py`) and the spec request paragraph.
  2. **Spec freeze gate** — `spec_tools.freeze_spec` (already a human/gate checkpoint).
  3. **Authored text** — game code/data/dialogue written during the build (`maestro/codegen/`).
  4. **Pre-asset prompts** — the image/mesh prompts built before generation
     (`src/tools/comfyui_tools.py` `build_item_payload`, consumed by `codegen/reskin.py`).
  5. **Generated images** — post-generation, before they're written into the artifact
     (the asset-chain results — `save_sprite`/`mesh_from_image` in `codegen/asset_chain.py`).
  6. **Final artifact gate** — before the build is staged for `/play` (`stage_for_play`).
- **Ties to `auth_and_billing.md`:** a violating user should be attributable/blockable (needs
  identity) — another reason auth lands first.

## Phase 1 — Research
- [x] **Define the policy scope.** Enumerate the hard-illegal categories to block (CSAM first; then
      jurisdiction-dependent illegal categories). Distinguish explicitly from allowed mature content.
      Get legal input on obligations (esp. CSAM reporting/preservation).
      → `tasks/safety_phase1_notes.md`. Legal input on reporting/preservation obligations is still
      an open action item (flagged, not resolved — no counsel consulted).
- [x] **Survey moderation options** for each modality:
  - **Text** — hosted moderation APIs (OpenAI moderation, Anthropic, Google) vs local classifiers;
    latency/cost/accuracy tradeoffs; that it must run against *generated* output, not just input.
  - **Images** — pre-gen prompt screening (blocklists/classifier on the prompt) AND post-gen image
    classification; CSAM-specific detection (hash-matching services + their access gating), NSFW
    classifiers; false-positive tolerance against legitimate mature art.
      → `tasks/safety_phase1_notes.md`.
- [x] **Decide the architecture:** which hook points (1–6 above) get a filter, and what each does
      (block / regenerate / flag-for-human / hard-stop-and-report). Input-side + output-side both —
      output-side is non-negotiable for images.
      → `tasks/safety_phase1_notes.md`; the pre-alpha basic block (below) implements hook points 1
      and 4 only (input + pre-gen image prompt). 3/5/6 (authored text, post-gen image classifier,
      final artifact gate) are Phase 2.
- [x] **False-positive strategy** — how a wrongly-blocked legitimate build is surfaced/appealed
      (ties to the human-in-the-loop review surface).
      → `tasks/safety_phase1_notes.md`; narrow keyword/combination matching (not a prudish filter) +
      a clear refusal message is the pre-alpha mitigation. A formal appeal path is Phase 2.
- [x] **Deliverable:** a short design doc (policy + chosen tools + hook map + block/report actions)
      reviewed before implementing. → `tasks/safety_phase1_notes.md`.

## Pre-alpha basic block (landed, ahead of full Phase 2)
A narrow, fail-closed keyword/pattern screen for the CSAM-adjacent category only (violence-in-
fiction and other dark/mature themes are explicitly NOT filtered):
- `src/tools/safety.py` (+ data file `src/tools/safety_terms.json`) — the shared `screen_text` /
  `screen_image_prompt` + `log_violation`.
- Hook point A (input) — `src/api/routers/chat.py` screens the raw chat message before it reaches
  the agent; `src/tools/chat_tools.py:propose_game_spec` screens the spec request paragraph too
  (defense in depth, since the agent may reformulate the request before proposing a spec).
- Hook point B (image prompts) — `src/tools/comfyui_tools.py:build_item_payload` (the chokepoint
  every asset image/mesh prompt funnels through — `codegen/reskin.py` builds each job's payload
  here) and the standalone `generate_image` fn screen each finalized prompt; a flagged prompt is
  skipped (never sent to the model, logged as blocked), never crashing the build.
- Violations are logged (`maestro.safety` logger) with the authed user id where available — never
  the full flagged text, only the matched term(s).
- Tests: `tests/test_safety.py` (rewritten 2026-07-23 after the codegen rebuild dropped the
  original) — both match shapes, false-positive guards, the logging contract, the chat refusal path.

Still open for full Phase 2: authored-text moderation (hook 3), post-gen image classification
(hook 5), the final artifact gate (hook 6), and a classifier/hash-matching upgrade path (explicitly
out of scope for this pass — see Guardrails above).

## Phase 2 — Implement (shape TBD by Phase 1)
- [x] **Input screening** on the request (chat + spec) — cheap first line. (pre-alpha basic block)
- [ ] **Output text moderation** on authored content before it's accepted into the artifact.
- [x] **Image safety** — prompt screening pre-gen at the `build_item_payload`/`generate_image`
      seam (`src/tools/comfyui_tools.py`, consumed by `codegen/reskin.py`). Post-gen classifier +
      CSAM-specific detection (hash-matching per the research) still open.
- [ ] **A blocking gate** at the artifact boundary (`run.py` packaging) as the backstop.
- [x] **Logging / flagging / attribution** — violations recorded (with user id where available) via
      the `maestro.safety` logger. A persistent per-user violation record for account action is
      still open (today it's log-only).
- [x] **Tests:** `tests/test_safety.py` (2026-07-23): known-bad text blocked (both match shapes,
      synthetic fixtures only); legitimate mature/dark content passes (false-positive guard, incl.
      word-boundary and adult-age cases); inflected sexual terms match (regression — the list once
      held bare stems the boundary compiler could never match); log carries matched terms + user,
      never the full text; a blocked chat message never reaches the agent. Seam plumbing
      (`build_item_payload` → None, `generate_image` → error) covered in `test_gpu_queue.py`.

## Ordering
Research (Phase 1) before any implementation — the tool + obligation decisions drive everything.
Depends on `auth_and_billing.md` for attribution/blocking. A launch gate: must land before public
access, alongside auth.

## Parked (needs owner + likely legal input)
- Jurisdiction scope (what "illegal" covers depends on where you operate).
- CSAM detection infra + reporting obligations — access-gated services; legal counsel required.
- Hosted-API vs fully-local moderation (a local-first product may resist sending content to a
  third-party moderation API — tension to resolve).
