# Safety Filter — Phase 1 Research Notes

Decision-ready notes backing `safety_filter.md` Phase 1. Scope: pre-alpha tier (trusted users,
but the image model is uncensored SDXL) — a narrow, fail-closed block on hard-illegal categories
only. Explicitly not a prudish filter: mature/dark creative themes (violence-in-fiction, dark
stories) are in-scope product and must not be blocked.

## 1. Policy scope

**In scope to block (hard-illegal):**
- Sexual content involving minors — the critical category. Covers explicit CSAM terminology
  directly, and the combination of a minor-age/school-age descriptor with explicit sexual
  content (age *or* sexual language alone is never enough to block).

**Explicitly out of scope to block (this is product, not a violation):**
- Violence-in-fiction (revenge dramas, war, murder mysteries, horror).
- Adult sexual content between adults (not a target of this filter at all; unrelated to the
  CSAM-adjacent category).
- Dark/mature themes generally (trauma, abuse-as-plot-in-service-of-story, moral ambiguity).

**Jurisdiction-dependent illegal categories** (bestiality, specific weapons/drug synthesis
instructions, real-person deepfakes, etc.) are **parked** — see `safety_filter.md` Parked
section. They need an explicit scope decision (which jurisdiction governs) that this pass does
not make. Folding them into the current filter would either be a no-op (nothing in the catalog
targets them) or scope creep past what a pre-alpha keyword filter can respectably screen.

**Legal input — NOT obtained.** CSAM in most jurisdictions carries mandatory reporting and
evidence-preservation obligations (see `safety_filter.md` Guardrails: "get legal input before
finalizing"). This pass does **not** implement reporting/preservation — it only refuses and logs
the refusal server-side. **Action item, unresolved:** get counsel on (a) whether a keyword-level
refusal (nothing is generated, nothing is stored) still triggers reporting duties in any target
jurisdiction, and (b) what a compliant report/preserve pipeline looks like if/when hash-matching
infra is added. Do not treat the current implementation as legally sufficient beyond pre-alpha,
trusted-user scope.

## 2. Moderation options surveyed, per modality

### Text
| Option | Latency/cost | Accuracy | Fit |
|---|---|---|---|
| Hosted moderation API (OpenAI `omni-moderation`, Google Perspective, Azure Content Safety) | ~100-300ms, per-call cost, network dependency | High recall for common categories, tunable thresholds | Rejected for this pass — the product is local-first (see project memory: "a local-first product may resist sending content to a third-party moderation API"), and every request would leave the box, including the mature/dark content that is explicitly *not* a violation. Worth revisiting for Phase 2's *output* moderation (hook 3), scoped to only what's flagged risky by a cheaper first pass. |
| Local classifier (e.g. a small BERT-family toxicity/NSFW-text model) | Local inference cost (a second model resident or swapped in), no network | Better recall/precision than keywords, but needs an inference seat and eval work | Deferred — real accuracy work (eval set, threshold tuning) is out of scope for "Phase 1 research → basic block." Good Phase 2 candidate once Phase 1's hook points are proven. |
| Keyword/pattern list (chosen for this pass) | ~0ms, zero cost, no network | Low recall in general (easy to phrase around), but the specific CSAM-combination pattern (age/school descriptor + explicit sexual term) is a narrow, high-precision-by-construction target: for THIS category, direct terminology is unusually blunt/literal, unlike e.g. self-harm content which is often euphemistic. | **Chosen.** Matches the guardrail "fail closed for illegal categories" cheaply, runs before any LLM call, needs no new infra, and is trivially auditable (a human can read the whole rule set in `safety_terms.json`). Explicitly a Phase 1 floor, not a ceiling — see "Known gaps" below. |

Must run against *generated* output too (hook 3, authored dialogue/character text), not just
input — noted as still-open in `safety_filter.md`. Deferred this pass: the build loop's authored
text is far more voluminous and varied than a single chat message or image prompt, so the
false-positive risk of the same keyword approach is much higher there (a village-full of NPCs,
one mentioning a child character's age in an innocuous scene, would light up the same
minor-descriptor list many times over). It needs its own design (likely: only screen dialogue/
narration paired with an explicit sexual-content check, scoped per-node, with a correction-prompt
fix path through the module system rather than a hard refusal) — a Phase 2 item.

### Images
| Option | Latency/cost | Accuracy | Fit |
|---|---|---|---|
| Pre-gen prompt screening (blocklist/classifier on the finalized prompt) | ~0ms for keyword, done before the model call | Same tradeoffs as text; catches the *steering*, not what the model actually renders | **Chosen for this pass** — it's the cheapest possible intervention and matches the guardrail "do not rely on the generation model refusing" (SDXL/Illustrious has no refusal behavior at all, unlike the tile-generation ideogram stack which already has its own stochastic refusal the codebase works around in `tile_refused()`). |
| Post-gen image classification (NSFW/CSAM classifier on the output pixels) | Adds an inference pass per generated image (real GPU-seconds on the single shared box) | Catches what pre-gen prompt screening misses (a "clean" prompt that the model renders as something else — SDXL is not fully steerable) | **Not implemented this pass** — needs a concrete classifier choice + a decision on false-positive handling (regenerate? placeholder? flag-for-human?) that Phase 1's "basic block" mandate doesn't cover. This is the most important Phase 2 gap: pre-gen screening alone is necessary but not sufficient — a prompt-screen-only filter is defeated by any prompt that reads innocuous but the model renders unsafely. |
| CSAM-specific hash-matching (PhotoDNA-class, access-gated) | Requires an approved-provider relationship, no public self-serve API | The only mechanism that can reliably catch *known* CSAM (re-uploads/re-generations of known bad content); irrelevant to *novel* generated content, which is the actual risk vector here | **Parked** (per `safety_filter.md` Guardrails: "may need hash-matching infra... not just a classifier"). Hash-matching defends against redistribution of known material; the risk this product actually has is *novel* generation, which hash-matching cannot catch by definition. It matters if/when user-uploaded reference images enter the pipeline (they don't today — check: no user image upload feature exists in the current API surface, confirmed via `grep -rn "UploadFile\|multipart" src/api/` returning nothing safety-relevant). Revisit if image upload ships. |

## 3. Architecture — hook-point map

Reusing `safety_filter.md`'s enumeration (Background, hook points 1–6):

| # | Hook point | This pass | Rationale |
|---|---|---|---|
| 1 | Input — chat message + spec request paragraph | **Implemented.** `src/api/routers/chat.py` (raw message, before the agent sees it) + `src/tools/chat_tools.py:propose_game_spec` (the request paragraph, defense-in-depth in case the agent reformulates before drafting a spec). | Cheapest, earliest point — refuses before any inference runs at all. |
| 2 | Spec freeze gate | Not touched. | Already a human checkpoint (`freeze_spec`); the human reviewing the frozen spec is itself a safety backstop for anything hook 1 misses in the request paragraph. No code change needed for pre-alpha (trusted, human-gated). |
| 3 | Authored text (nodes/characters/dialogue during the build loop) | **Deferred to Phase 2** — see "Text" survey above for why the same keyword approach doesn't transfer cleanly. |
| 4 | Pre-asset image prompts | **Implemented.** `src/tools/comfyui_tools.py:run_jobs` — the single chokepoint every build image job (character/background/item/tile/cg/title-card) funnels through before `_run_comfyui_job` is called; plus the standalone `generate_image` chat tool (a second live seam that bypasses `run_jobs`, found during implementation — see `renpy/fns.py`'s `generate_images` docstring in `safety_filter.md`, which only names the build-pipeline path). | `run_jobs` was chosen over screening each `build_*_job()` helper individually because it's the one place *every* job — regardless of kind — passes through immediately before dispatch, so one hook covers all current and future job kinds without per-kind duplication. |
| 5 | Generated images (post-gen, before written into the artifact) | **Deferred to Phase 2** — needs a classifier decision (see Images survey). |
| 6 | Final artifact gate (`run.py` packaging) | **Deferred to Phase 2** — a backstop for anything upstream missed; lower priority while hooks 1+4 already fail closed at the earliest points and the platform is trusted-user-only. |

**Action taken per hit:** block only (refuse the chat turn / raise inside the spec-proposing tool
/ skip the image job with a placeholder-style failure result). No "regenerate" or
"flag-for-human" mode implemented — those need a review surface that doesn't exist yet (ties to
`human.py`'s HITL waiver store conceptually, but that store is for build-quality waivers, not
safety review, and repurposing it is a Phase 2 design question, not this pass's).

## 4. False-positive strategy

Two mitigations, both live in this pass:
1. **Narrow-by-construction matching.** The CSAM category requires *either* an unambiguous
   standalone phrase ("child porn", "csam", ...) *or* a minor-descriptor **and** an explicit
   sexual term **co-occurring in the same text**. Neither half alone blocks — "a 10-year-old's
   birthday party" and "an adult romance novel with an explicit sex scene" both pass individually.
   This directly targets the guardrail "cover ages/school-age descriptors combined with sexual
   context, not innocent uses."
2. **Clear, non-shaming refusal message** naming the actual boundary ("sexual content involving
   minors... mature or dark themes are fine; this specific combination is not") so a legitimately
   mature request that trips the filter has an actionable signal to rephrase, rather than a bare
   403.

**Not implemented — a formal appeal/override path.** `safety_filter.md`'s human-in-the-loop
review surface (`human.py` waivers) is scoped to build-quality corrections, not safety review;
wiring a wrongly-blocked request to a human reviewer is real design work (who reviews? what do
they see — the flagged text itself, which the logger deliberately does NOT persist? does
approval re-run the same request?) that doesn't fit "basic block." For pre-alpha (a few trusted
people), the mitigation is: the filter is narrow enough that a false positive should be rare, and
the refusal message tells the user exactly what to change and try again themselves.

## 5. Known gaps (explicitly deferred, not silently missed)

- **Word-boundary keyword matching is defeatable** (leetspeak, spacing tricks, unlisted synonyms,
  non-English phrasing). Acceptable for pre-alpha/trusted-user scope; not acceptable as the
  permanent Phase 2 answer for a public launch — Phase 2 should add a classifier layer per the
  Text/Images survey above.
- **No post-generation image check** — a prompt that reads clean but renders unsafely is not
  caught. This is the single most important Phase 2 gap (see Images survey, row 2).
- **No authored-text (dialogue/narration) screening** during the build loop (hook 3).
- **No final artifact gate** (hook 6) as a backstop.
- **No legal sign-off** on reporting/preservation obligations — flagged, not resolved.
- **Violations are logged, not persisted for account action** — `maestro.safety` logger only, no
  database row keyed to the user for a future ban/review workflow. Cheap to add once there's an
  actual moderation queue to feed (a Phase 2 item, ties to `auth_and_billing.md`'s user model).

## Recommendation for Phase 2 sequencing

1. Post-gen image classifier (hook 5) — highest-value gap, since pre-gen prompt screening is
   provably insufficient against a non-fully-steerable model.
2. Persist violations to a queryable store (ties to auth) so repeat offenders are visible before
   real payments are involved.
3. Authored-text screening (hook 3) — needs its own false-positive design given per-node volume.
4. Final artifact gate (hook 6) as the last backstop.
5. Legal review of reporting/preservation obligations — should happen in parallel with all of the
   above, not block them, but must land before any public-facing (non-trusted-circle) launch.
