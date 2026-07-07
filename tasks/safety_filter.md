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
  3. **Authored text** — nodes/characters/dialogue written during the build loop (`maestro/modules/`).
  4. **Pre-asset prompts** — the image prompts built before generation (`comfyui_tools.py`,
     `renpy/fns.py` `generate_images`).
  5. **Generated images** — post-generation, before they're written into the artifact
     (`generate_images` output; the sequential `run_jobs` results).
  6. **Final artifact gate** — before packaging/download (`run.py:62-77`).
- **Ties to `auth_and_billing.md`:** a violating user should be attributable/blockable (needs
  identity) — another reason auth lands first.

## Phase 1 — Research
- [ ] **Define the policy scope.** Enumerate the hard-illegal categories to block (CSAM first; then
      jurisdiction-dependent illegal categories). Distinguish explicitly from allowed mature content.
      Get legal input on obligations (esp. CSAM reporting/preservation).
- [ ] **Survey moderation options** for each modality:
  - **Text** — hosted moderation APIs (OpenAI moderation, Anthropic, Google) vs local classifiers;
    latency/cost/accuracy tradeoffs; that it must run against *generated* output, not just input.
  - **Images** — pre-gen prompt screening (blocklists/classifier on the prompt) AND post-gen image
    classification; CSAM-specific detection (hash-matching services + their access gating), NSFW
    classifiers; false-positive tolerance against legitimate mature art.
- [ ] **Decide the architecture:** which hook points (1–6 above) get a filter, and what each does
      (block / regenerate / flag-for-human / hard-stop-and-report). Input-side + output-side both —
      output-side is non-negotiable for images.
- [ ] **False-positive strategy** — how a wrongly-blocked legitimate build is surfaced/appealed
      (ties to the human-in-the-loop review surface).
- [ ] **Deliverable:** a short design doc (policy + chosen tools + hook map + block/report actions)
      reviewed before implementing.

## Phase 2 — Implement (shape TBD by Phase 1)
- [ ] **Input screening** on the request (chat + spec) — cheap first line.
- [ ] **Output text moderation** on authored content before it's accepted into the artifact.
- [ ] **Image safety** — prompt screening pre-gen + classifier post-gen at the `generate_images`
      seam; CSAM handling per the research (detect → block → the legally-required action).
- [ ] **A blocking gate** at the artifact boundary (`run.py` packaging) as the backstop.
- [ ] **Logging / flagging / attribution** — violations recorded against the user (needs auth) for
      review + account action.
- [ ] **Tests:** known-bad prompts/text are blocked; legitimate mature content is NOT blocked
      (false-positive guard); the image seam rejects a flagged generation; the artifact gate fails
      closed on a violation. (Use synthetic/proxy fixtures — never real illegal content in tests.)

## Ordering
Research (Phase 1) before any implementation — the tool + obligation decisions drive everything.
Depends on `auth_and_billing.md` for attribution/blocking. A launch gate: must land before public
access, alongside auth.

## Parked (needs owner + likely legal input)
- Jurisdiction scope (what "illegal" covers depends on where you operate).
- CSAM detection infra + reporting obligations — access-gated services; legal counsel required.
- Hosted-API vs fully-local moderation (a local-first product may resist sending content to a
  third-party moderation API — tension to resolve).
