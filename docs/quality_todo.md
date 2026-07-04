# Quality TODO — improving generated game output

The north star is *good* output, not *valid* output. Today every loop done-condition is
structural/integrity (counts, refs, reachability, compiles) — **nothing checks writing or
mechanic quality**. "Done" = a valid skeleton, not a good game. Closing that gap is the theme
of this list.

Ordered by impact on output quality. Recommended sequencing: ship **#1 (judge-in-loop)** and
**#2 (outline)** first — they turn the system from "valid" into "good" and every later item
plugs into the judge. The rest are depth once the feedback spine exists.

---

## 1. Quality feedback in the loop (biggest lever — currently missing)

Loop completion = structural floor pass. Nothing reads finished content and judges it. Re-add
the LLM-judge the rebuild removed, this time as an in-loop gate.

- [ ] **LLM-judge as a `done_condition` check type.** Register `quality_gate` (like `compiles`)
  in `maestro/validate.py`. Reads assembled content, scores against a rubric, emits structured
  revision to-dos (per-node / per-place). A failing score is a real failure in the to-do list →
  forces a revision pass. Without this, prompts have no error signal.
- [ ] **Reuse `eval/cli.py score game` rubric** for that check — already exists and validated
  (28-match human read). Do not author a second rubric.
- [ ] **Revision tool + mode.** `revise_node` / `revise_place` taking a judge note → targeted
  rewrite. Distinct from `edit_node` (surgical structural patch) — this is quality-driven.
- [ ] **Cap revision passes** (budget) so the judge can't loop forever on a stubborn small model.

## 2. Dramatic structure (the 20-node middle is improvised)

Premise → endings exist; the middle is authored node-by-node, each node seeing only the
story-state snapshot. No global arc → no pacing, escalation, or setup/payoff across the script.

- [x] **Outline / beat-sheet stage between premise and nodes.** New `outline` module/component
  (`maestro/discrete/outline.py`): logline + ordered beats (each with a dramatic `purpose` +
  `tension`) + a planned `ending_paths` entry per premise ending. Baseline forces ≥5 beats and a
  path for *every* ending (`refs_resolve premise.endings → outline.ending_paths`), so branches
  diverge by design. vn-only (ships in the `vn` preset; pnc/card use `dialogue_npc`).
- [x] **Add `outline` to the vn build order** (premise → outline → nodes, declared authoritatively
  in the outline module's deps). Injected into `write_node` context for free: a passing upstream
  component is rendered into every later step's context, so the node sub-loop sees the whole arc;
  `write_node.txt` now tells it to realize the outline rather than improvise. *(Soft assignment —
  the model maps scenes onto the arc; a hard per-node beat-coverage check is a possible follow-up.)*
- [ ] **Check: choices have consequences.** New check — a meaningful fraction of menu choices
  carry `effects` or gate distinct endings. Stops decorative choices.
- [ ] **Raise `min_branches`** floor, or make it proportional to node count (20 nodes / 2
  branches ≈ linear). Add a `branch_divergence` check: branches reach different endings, not
  rejoin immediately.

## 3. Continuity across branches (known-deferred bug)

`story_state` is a single spine snapshot; branches "rejoin." On a branching VN a branch-A node
sees facts set on branch-B. Real continuity bugs.

- [ ] **Per-path story state.** Track facts per branch path, not one global snapshot — the
  pre/postcondition rigor `story_state.py` explicitly defers.
- [ ] **`recent_events_tail` = 3 is aggressive.** Relies on the agent promoting durable facts to
  `established_facts`, unchecked. Add a check or widen the window.
- [ ] **Continuity check in the judge** — facts a node references must be established on a path
  that reaches it.

## 4. Mechanics depth — combat / economy (shallowest)

`combat` baseline = `min_abilities: 2, min_combatants: 2, min_encounters: 1`. `economy` baseline =
nothing. Mechanic loops are never validated for being actual games.

- [ ] **combat depth checks.** Ability/status variety across combatants; encounter difficulty
  escalates; >1 encounter for a real campaign arc (unchecked).
- [ ] **Economy balance check.** A variable has both sources and sinks; the win/lose loop is
  reachable and not trivially degenerate (infinite money / instant ruin).
- [ ] **More combat_models.** Only `turn_based` plays (`real_time`/`auto` declared, unimplemented).
  Add depth here before widening the model set — a shallow effect set caps combat quality.
- [ ] **Encounter flavor.** Encounters are pure params (no narrative framing). Let combatant
  banter and rising tension ride on the fight — currently mechanical.

## 5. Point-and-click puzzle depth

`places` checks reachable / items_used / goal_reachable — structural. A "puzzle" can be
trivially open.

- [ ] **Puzzle-depth check.** Win requires an actual item/flag chain (≥N gated steps), not a
  single `win` hotspot anyone can click.
- [ ] **Stub-examine check.** `examine` text must be substantive, not placeholder. Folds into the
  judge.
- [ ] **Red-herring / interactable richness** — judge note, not a hard gate.

## 6. Asset quality

Two-pass emotion img2img is good. Gaps:

- [ ] **Background-description lint.** Known caveat: Illustrious renders a creature named in a bg
  description as the subject. Lint bg descriptions to stay environmental (auto-strip / warn).
- [ ] **Character-consistency check** across scenes (sprite identity drift) — at least a judge
  note.
- [ ] **Quality gate on title_card / CGs** — currently optional and ungraded.

## 7. Spec drafting (upstream — a wrong spec is a wrong everything)

- [ ] **Validate genre/preset classification fit.** Misclassify → wrong modules → wrong game. A
  cheap LLM self-check, or surface confidence to the human at freeze.
- [ ] **Premise judge before freeze.** The premise prompt is strong (Grimgong test, central
  question) but unenforced — `each_has` only checks fields are non-empty, not that the central
  question is a real values tension. The highest-leverage single judge call, since everything
  derives from the premise.

## 8. Post-compile refiner pass (make a working game first, then improve it)

Today the loop's only verdict is the structural floor — when it goes green the artifact is
*valid*, and that's the end of it. The image world's lesson: generate first, then run a refiner
that fixes the problematic bits (low-res eyes, mangled hands). Apply the same shape here.

- [ ] **Refiner stage after the first successful compile.** Read the finished, assembled content
  and fix problematic pieces in place (flat/under-written nodes, thin examine text, weak match
  flavor) — a quality-driven rewrite pass distinct from the build loop's structural drive. Plugs
  into the §1 judge for its problem list and the §1 `revise_node`/`revise_place` tools for the fix.
- [ ] **Heal manual-edit `story_state` desync here.** Human-in-the-loop hand-edits (edit a node's
  character line via the games API) leave the cumulative `story_state.json` stale — it can't be
  recomputed from the artifact, only carried. Rather than block edits at author time, let the
  refiner re-derive / repair continuity after the fact. Until the refiner exists, manual edits
  knowingly accept story_state staleness (the HITL editing is intentionally unguarded on this).
- [ ] **Cap refiner passes** (budget) like the §1 judge, so it can't loop forever polishing.

---

# Codebase / prompt / tooling debt

Not user-facing quality directly, but it caps how fast quality can climb: duplication breeds
drift, and a leaky seam means every new module re-touches the core. Found while auditing the
prompts + tool-scoping.

## 9. Prompt duplication (DRY) — confirmed, the suspicion was right

The same "house rules" are copy-pasted across 6–8 prompts; `render_template` is plain `{key}`
substitution with no include/partial mechanism, so each prompt re-states everything. Bloats
every call's context (worse for small models — more tokens, more distraction) and drifts.

- [x] **Shared prompt partials injected via `{{include:NAME}}`.** `render_template` now resolves
  `{{include:NAME}}` from `maestro/prompts/partials/`. Factored: `tool_call_rule`, `conditions_ref`,
  `effects_ref`, `derive_from_request`. (Originally specced as ctx keys; an include directive keeps
  callers unchanged.) Repeated verbatim today:
  - "Respond with a TOOL CALL, never prose. You write JSON, NOT Ren'Py — the compiler renders
    it" — in write_node, write_place, fix_node, fix_place, mode_premise, mode_asset,
    build_agent_system.
  - The CONDITIONS block (`{"flag":"x"}, {"item":"x"}, {"var":...}`) and EFFECTS block
    (`set_flag / clear_flag / …`) — duplicated across write_node, write_place, fix_place **and**
    again inside the SKEL_* comments.
  - "Derive every detail from the premise/request, never from the skeleton placeholder ids" —
    in 6 prompts.
- [x] **Skeleton shape no longer authored twice.** `write_node.txt`/`write_place.txt` stopped
  restating the JSON shape; they reference the appended `skeleton_guide` ("Required component
  shapes") — `SKEL_NODES`/`SKEL_PLACES` are the one source, killing the `location` drift.

## 10. Spec prompts triplicated + done-conditions authored twice

`propose_spec.txt` / `propose_spec_pnc.txt` / `propose_spec_card.txt` are 270 lines of
near-duplicate. Worse, each hardcodes a prose "good default set" of done-conditions that
**duplicates the code baselines** in the modules (dialogue SPINE / navigation / combat) — two
sources for the same contract, guaranteed to drift.

- [x] **Generate the spec prompt from the composed preset.** One `propose_spec.txt`; its
  `{component_shapes}` = `skeleton_guide(genre)` and `{default_conditions}` = the composed modules'
  `baseline` JSON, with a per-preset `{genre_blurb}` and a shared `allowed_checks` partial.
  `propose_spec_pnc.txt`/`propose_spec_card.txt` deleted; baselines stay the single source.
- [x] **Generic spec-drafter language.** Genre/engine-specific framing moved into `genre_blurb_*`;
  the template body is preset-agnostic.

## 11. Tool-selection-per-module leaks back into the core

The module refactor was supposed to kill per-genre dispatch, but per-target/per-mode tool gating
and prompt selection are **hardcoded in `agent.py`** (`_MODE_TOOLS`, `_TARGET_TOOLS`,
`_PLACE_TARGET_TOOLS`, `_TARGET_PROMPT`, `_PLACE_TARGET_PROMPT`, `_MODE_PROMPTS`), keyed by
component / check-type strings. Adding a module meant editing these maps — the seam leaks.

- [x] **Move tool gating + prompt mapping onto the `Module`.** Added `mode_tools` / `mode_prompt`
  / `prompts` / `target_jobs` / `target_tools` / `subloop` to `Module`; `compose()` unions them and
  `agent.py`'s decider + the single `make_subloop(module)` read the bundle. The `_MODE_TOOLS` /
  `_TARGET_*` / `_*_PROMPTS` globals are gone — adding a module needs no `agent.py` edit.

## 12. Tool surface gaps that cap quality

- [ ] **`read_story_state` is absent from the places and combat tool sets.** PnC talk-node
  barks and encounter flavor are authored blind to continuity. Add it where those modules write.
- [ ] **No tool to read sibling content for setup/payoff.** `read_node` is by-id and the injected
  node-graph view is structural only — an author can't see what was foreshadowed elsewhere, so
  callbacks/payoff are hard. Weigh a scoped "recent beats" read against context bloat.
- [ ] **`validate` is exposed as an agent tool *and* the executor auto-validates each step.**
  Check whether the in-loop `validate` tool call is ever load-bearing or just burns a turn.

## 13. Context bloat in the build-step render path (found in a live run)

Audited a real `vn` build (qwen3.6-35b-a3b) by capturing every LLM request. Premise/asset/outline
each wrote first-try and the OUTPUT was strong, but **every build step ships ~20K chars (~5K
tokens) to write ONE node** — and the first `write_node` call failed by returning prose instead of
a tool call (then recovered only via reasoning-escalation). The architecture preaches "minimum
context per step"; the render path violates it. Three mostly-static blocks injected EVERY step:

- [x] **Scope `skeleton_guide` to the active component.** `run.py` builds `guide =
  skeleton_guide(genre=spec.genre)` ONCE (all 4 component shapes, ~3.5K chars) and passes it as
  `component_guide` to both `make_llm_decider` and every `make_subloop` — so a node step is shown
  the premise/asset/outline AUTHORING skeletons for components already locked. Fix: per-mode guide
  (`skeleton_guide(component_ids=[mode])`); the decider should pick the active mode's skeleton, and
  `make_subloop(module)` should scope to `module.components`. Biggest, cheapest win (~3.5K→~0.9K).
- [x] **Trim `LOCKED COMPONENTS` (upstream) to the fields the consumer uses.**
  `executor.build_context` loads full component bodies into `upstream`; `_render_context` dumps them
  verbatim. For node authoring the worst offender is `asset_manifest`'s image-gen prose (5 bg
  paragraphs + 3 CG paragraphs + title card, ~2K chars) — a node author needs background **ids**,
  never their Stable-Diffusion prompts; also drop `image_file`/`color`. Add a per-component "context
  view" (like the executor `projectors`, but for upstream) so each consumer sees a trimmed shape.
- [x] **Scope or drop the SPEC block in `_render_context`.** It dumps the WHOLE spec (all components
  + every done_condition, ~1.5K) into every user message; the TO-DO already lists the failing checks
  and LOCKED COMPONENTS carries the settled ids. Scope to the active component (+ its deps) or drop.

  *#1–3 share one root cause and would likely have prevented the node-1 prose-not-tool-call failure
  outright. Do them before tuning generation prompts — they change what the model actually sees.*

- [x] **`premise count >= 3` mis-fits an intimate cast — confirmed defect.** On a two-hander the
  floor forced the model to invent a third "character" — `narrator`/"The House" with example_lines.
  Downstream it bit: that entity actually SPEAKS (`speaker: "narrator"` lines), but `asset_manifest`
  only generated sprites for the two real sisters, so "The House" talks with no sprite, AND narration
  is now split inconsistently between `speaker: null` and `speaker: "narrator"`. Fix: relax the VN
  character floor (min 2), or model an "environment/narrator voice" as an explicit non-cast concept
  the count doesn't include and `all_characters_speak` doesn't demand.
- [x] **No per-node `location` check — the opening scene shipped with no background.** `scene_01`
  (the first thing the player sees) had `location: None`; the skeleton SAYS "Tag EVERY node" but
  nothing enforces it, so an untagged node compiles to a scene with no background image. Add an
  `each_node_has_location` done-condition (or enforce it in `write_node` like the min-lines floor),
  at least for the start node.
- [x] **`classify_genre` can't emit `card_ante`.** `classify_genre.txt` offers only `vn` /
  `point_and_click`; the third preset is unreachable from the classifier, so a wander-and-gamble
  request misclassifies. Add `card_ante` (or its trigger) to the classifier prompt.

### Found in the verification re-run

- [x] **`speaker: "narration"` thrash.** The model writes the literal string `"narration"`/
  `"narrator"` for narration instead of `speaker: null`. Nothing catches it at write time, so it
  surfaces only at crossref/compile as a bogus undeclared character — and the small model then
  thrashes (15+ steps, editing the WRONG node) trying to clear it. 4 such lines stalled a whole
  build. Fixed: `write_node`/`edit_node` normalize narration-alias speakers to null at write time
  (`is_narration_speaker` in `ir_assemble`, also applied as an assembly backstop). Healed the
  stuck run on recompile.
- [~] **`write_node` tool-call reliability on a small model — investigated, partly a model ceiling.**
  Replaying real outputs settled the causes: (1) the token-budget bump was WRONG — `max_tokens`
  doesn't bind reasoning (a call hit 26.7k under a 20k cap) and the extra room let the model
  over-reason into an EMPTY output; reverting to 8k *reduced* no-tool-calls (forces it to wrap up).
  (2) ~40% of calls stringify `content`, but that JSON is usually MALFORMED (mangled quotes) —
  `_coerce_json` now rescues the valid subset (shipped). (3) Born-compliant `location` enforcement
  removed a ~20-step fix phase. Residual: the model still sometimes dumps text / emits malformed
  tool JSON — a small-model JSON-emission ceiling the loop's nudge/escalation recovers from (builds
  complete, slowly). Remaining levers (not done): a more capable node-authoring model; or shrink the
  per-call JSON the model must emit (fewer required fields / shorter nodes).
- [ ] **Fix-phase efficiency on a small model.** Reachability/location/crossref repair runs
  one `edit_node` per failing item, and the small model repoints poorly (observed 3–4 edits on one
  node). Born-compliant enforcement (min_lines, location, narration) attacks this at the source;
  reachability + crossref still grind. Consider batch-repair tools or born-compliant wiring.
