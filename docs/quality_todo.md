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

- [ ] **Outline / beat-sheet stage between premise and nodes.** New `outline` component (act
  structure, turning points, which beats gate which endings). Nodes author against an assigned
  beat, not blind. Highest single lever after the judge.
- [ ] **Add `outline` to dialogue SPINE deps** (premise → outline → nodes). Inject the assigned
  beat into `write_node` context.
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

## 4. Mechanics depth — card_play / economy (shallowest)

`card_play` baseline = `count >= 1`. `economy` baseline = nothing. Mechanic loops are never
validated for being actual games.

- [ ] **card_play depth checks.** Stakes vary/escalate across matches; opponents distinct; >1
  match for a real wander-wager loop (the prompt says "not a single table" — unchecked).
- [ ] **Economy balance check.** A variable has both sources and sinks; the win/lose loop is
  reachable and not trivially degenerate (infinite money / instant ruin).
- [ ] **More card_models.** Only `high_card` / `blackjack`. Add depth (poker-lite,
  push-your-luck) — a shallow rule set caps card-game quality.
- [ ] **Match flavor.** Matches are pure params (no dialogue / stakes narrative). Let opponent
  banter and rising tension ride on the match — currently mechanical.

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
    it" — in write_node, write_place, fix_node, fix_place, mode_matches, mode_premise, mode_asset,
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
**duplicates the code baselines** in the modules (dialogue SPINE / navigation / card_play) — two
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
component / check-type strings. Adding `card_play` meant editing these maps — the seam leaks.

- [x] **Move tool gating + prompt mapping onto the `Module`.** Added `mode_tools` / `mode_prompt`
  / `prompts` / `target_jobs` / `target_tools` / `subloop` to `Module`; `compose()` unions them and
  `agent.py`'s decider + the single `make_subloop(module)` read the bundle. The `_MODE_TOOLS` /
  `_TARGET_*` / `_*_PROMPTS` globals are gone — adding a module needs no `agent.py` edit.
- [ ] **`matches` has no sub_runner, no fix mode, no per-target gating.** It's a single
  `write_component` call — it can't repair or grow against a richer check. The moment card_play
  gets the depth checks from §4, it needs a sub-loop like nodes/places.

## 12. Tool surface gaps that cap quality

- [ ] **`read_story_state` is absent from the places and matches tool sets.** PnC talk-node
  barks and card-match flavor are authored blind to continuity. Add it where those modules write.
- [ ] **No tool to read sibling content for setup/payoff.** `read_node` is by-id and the injected
  node-graph view is structural only — an author can't see what was foreshadowed elsewhere, so
  callbacks/payoff are hard. Weigh a scoped "recent beats" read against context bloat.
- [ ] **`validate` is exposed as an agent tool *and* the executor auto-validates each step.**
  Check whether the in-loop `validate` tool call is ever load-bearing or just burns a turn.
