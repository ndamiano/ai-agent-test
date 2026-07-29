# Safety Filter — Illegal-Content Prevention

Verified: 2026-07-25

**Launch gate** (legal/trust-safety), not optional. Phase 1 research + the input/pre-gen-prompt
screens landed (see `finished.md` and `tasks/safety_phase1_notes.md`); the output-side hooks are
what remain.

## Why
Public launch means untrusted users driving generation. The platform must not produce illegal
content. The single highest-risk vector is the **local, uncensored image model** (SDXL/Illustrious
has no built-in guardrails and is prompt-steerable to illegal imagery — CSAM is the critical legal
category, with mandatory-reporting obligations). Today only the INPUT side is screened; nothing
inspects what comes back out.

## Guardrails (READ FIRST)
- **Do not rely on the generation model refusing.** Local models don't. The filter is a separate,
  external layer.
- **Fail closed for illegal categories.** On uncertainty in a hard-illegal category, block.
- **Separate illegal from merely mature.** Mature/dark creative themes are in-scope for the product
  (dark stories, violence-in-fiction); *illegal* content is the line. Don't build a prudish filter
  that guts legitimate creative range — target the illegal categories precisely.
- **CSAM is special.** Likely legal reporting + preservation obligations and may need hash-matching
  infra (e.g. PhotoDNA-class services, access-gated) — not just a classifier. **Legal input is
  still unobtained** (open action item from Phase 1 — no counsel consulted).

## Background (VERIFIED)
- **Landed:** `src/tools/safety.py` (+ `src/tools/safety_terms.json`) — `screen_text` /
  `screen_image_prompt` / `log_violation`; wired at hook 1 (`api/routers/chat.py` +
  `tools/chat_tools.py:propose_game_spec`) and hook 4 (`tools/comfyui_tools.py:build_item_payload`
  — a flagged prompt is skipped, never crashing the build). Violations log to
  the `maestro.safety` logger with the authed user id and only the matched term(s).
  Tests: `tests/test_safety.py`.
- **Open hook points** (numbering from Phase 1's map in `safety_phase1_notes.md`):
  3. **Authored text** — game code/data/dialogue written during the build (`maestro/codegen/`).
  5. **Generated images** — post-generation, before they're written into the artifact
     (`save_sprite`/`mesh_from_image` in `codegen/asset_chain.py`).
  6. **Final artifact gate** — before the build is staged for `/play` (`stage_for_play`).

## Phase 2 — the output side
- [ ] **Output text moderation** on authored content before it's accepted into the artifact (hook 3).
      → done when: `grep -n "screen_text" src/maestro/codegen/tools.py` is non-empty
- [ ] **Post-gen image classification** + CSAM-specific detection (hash-matching per the research) —
      the non-negotiable half for the image model (hook 5).
      → done when: `grep -n "screen_image_prompt" src/maestro/codegen/asset_chain.py` is non-empty
- [ ] **A blocking gate at the artifact boundary** (`stage_for_play`) as the backstop (hook 6).
      → done when: `grep -n "screen_" src/maestro/codegen/gates.py` shows a call inside `stage_for_play`
- [ ] **Persistent per-user violation record** for account action (today it's log-only).
      → done when: `grep -n "violation" src/db/store.py` is non-empty (a table/query exists)
- [ ] **Formal appeal/override path** for a wrongly-blocked build (Phase 1 deferred the design).
      → done when: `grep -rn "appeal" src/api/` is non-empty (an appeal endpoint exists)
- [ ] **Tests** for each of the above, synthetic fixtures only (the `test_safety.py` convention).
      → done when: `cd src && python -m pytest ../tests/test_safety.py -q` passes and covers hooks 3/5/6

## Ordering
Legal input on CSAM obligations gates the hash-matching decision; the classifier hooks (3/5/6) do
not, and can land first. Must be complete before public access.

## Parked (needs owner + likely legal input)
- Jurisdiction scope (what "illegal" covers depends on where you operate).
- CSAM detection infra + reporting obligations — access-gated services; legal counsel required.
- Hosted-API vs fully-local moderation (a local-first product may resist sending content to a
  third-party moderation API — tension to resolve).
