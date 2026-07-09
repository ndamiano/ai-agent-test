# Quality Backlog — improving generated game output

## Why
The north star is *good* output, not *valid* output. Today every loop done-condition is
structural/integrity (counts, refs, reachability, compiles) — **nothing checks writing or
mechanic quality**. "Done" = a valid skeleton, not a good game. Closing that gap is the theme
of this list.

Ordered by impact. Recommended sequencing: ship **§1 (judge-in-loop)** first — it turns the
system from "valid" into "good" and every later item plugs into the judge. **§2 (prompt
iteration)** is the standing quality grind that runs alongside everything. The rest are depth
once the feedback spine exists.

> **Caveat:** some file/term references below predate the module-system rewrite (they name
> `validate.py`, `executor.build_context`, `discrete/outline.py`, genre/preset). Verify against
> the current `maestro/modules/` system before implementing — the intent holds, the paths may
> have moved.

Shipped items from the old quality list (outline stage, prompt-partial DRY, spec-prompt
single-source, tool gating onto `Module`, context-bloat trims, born-compliant write fixes) are
recorded in `finished.md`.

---

## 1. Quality feedback in the loop (biggest lever — currently missing)

Loop completion = structural floor pass. Nothing reads finished content and judges it. Re-add
the LLM-judge the rebuild removed, this time as an in-loop gate.

- [ ] **LLM-judge as a `quality_gate` check.** A `Check` that reads assembled content, scores
  against a rubric, and emits structured per-node / per-place revision errors. A failing score is
  a real error in `get_errors` → forces a revision pass. Without this, prompts have no error signal.
- [ ] **Reuse `eval/cli.py score game` rubric** for that check — already exists and validated
  (28-match human read). Do not author a second rubric.
- [ ] **Revision tool + fix mode.** `revise_node` / `revise_place` taking a judge note → targeted
  quality rewrite. Distinct from `edit_node` (surgical structural patch).
- [ ] **Cap revision passes** (budget) so the judge can't loop forever on a stubborn small model.

## 2. Iterate on EVERY prompt (systematic climb)

Every `.txt` prompt is a quality lever; none has been systematically climbed since the rewrite.
Do a deliberate pass over the whole prompt surface, not ad-hoc tweaks. Standing grind, not a
one-time task.

- [ ] **Inventory every prompt.** Enumerate all `.txt` under `maestro/prompts/` (+ partials),
      `renpy/prompts/`, and any engine prompt dirs. Build a tracking table: prompt → what call it
      backs → last climbed → verdict. This table lives here and gets checked off.
- [ ] **One climb pass per prompt.** Use `maestro/climb.py` (clone a finished run, wipe one
      module, re-run the loop with a candidate prompt = comparable output) for module prompts;
      the spec drafter via `propose`. Baseline → judge → hypothesis → iterate → keep only if better.
- [ ] **Re-run the same prompt before rejecting** (seed variance) — a bad single output may be
      noise, not the prompt. (Known law from prior climbing.)
- [ ] **Apply the proven generation-failure laws** during each climb: examples get copied even
      when labelled as FAILURES (so never ship a bad example); voice/spec fields get maximized;
      mood/length *bounds* are ignored; planning vocabulary leaks into prose; code-level hygiene
      beats prompt rules. Lean on the `grade-scenes` skill + `slop_scan` tooling.
- [ ] **Guard against overfitting.** Run the genre battery (RPG / horror / cards / tense drama)
      after any prompt change — a win on the comedy gold-premise that regresses horror is not a win.
      Move tone into spec/story DATA, not fixed prompt text.
- [ ] Depends on §1's judge for an automated score; until then, Claude-as-judge manually (no rubric).

## 3. Dramatic structure — remaining checks

Outline/beat-sheet stage shipped (see `finished.md`). Still open:

- [ ] **Check: choices have consequences.** A meaningful fraction of menu choices carry `effects`
  or gate distinct endings. Stops decorative choices.
- [ ] **Raise `min_branches`** floor, or make it proportional to node count. Add a
  `branch_divergence` check: branches reach different endings, not rejoin immediately.

## 4. Continuity across branches (known-deferred bug)

`story_state` is a single spine snapshot; branches "rejoin." On a branching VN a branch-A node
sees facts set on branch-B. Real continuity bugs.

- [ ] **Per-path story state.** Track facts per branch path, not one global snapshot.
- [ ] **`recent_events_tail` = 3 is aggressive.** Relies on the agent promoting durable facts to
  `established_facts`, unchecked. Add a check or widen the window.
- [ ] **Continuity check in the judge** — facts a node references must be established on a path
  that reaches it.

## 5. Mechanics depth — combat / economy (shallowest)

Mechanic loops are never validated for being actual games.

- [ ] **Combat depth checks.** Ability/status variety across combatants; encounter difficulty
  escalates; >1 encounter for a real campaign arc.
- [ ] **Economy balance check.** A variable has both sources and sinks; the win/lose loop is
  reachable and not trivially degenerate (infinite money / instant ruin).
- [ ] **More combat models.** Only `turn_based` plays (`real_time`/`auto` declared, unimplemented).
  Add depth here before widening the model set — a shallow effect set caps combat quality.
- [ ] **Encounter flavor.** Encounters are pure params (no narrative framing). Let combatant
  banter and rising tension ride on the fight.

## 6. Point-and-click puzzle depth

`places` checks reachable / items_used / goal_reachable — structural. A "puzzle" can be
trivially open.

- [ ] **Puzzle-depth check.** Win requires an actual item/flag chain (≥N gated steps), not a
  single `win` hotspot anyone can click.
- [ ] **Stub-examine check.** `examine` text must be substantive, not placeholder. Folds into the
  judge.
- [ ] **Red-herring / interactable richness** — judge note, not a hard gate.

## 7. Asset quality

Two-pass emotion img2img is good. Gaps:

- [ ] **Background-description lint.** Illustrious renders a creature named in a bg description as
  the subject. Lint bg descriptions to stay environmental (auto-strip / warn).
- [ ] **Character-consistency check** across scenes (sprite identity drift) — at least a judge note.
- [ ] **Quality gate on title_card / CGs** — currently optional and ungraded.

## 8. Spec drafting (upstream — a wrong spec is a wrong everything)

- [ ] **Validate module-selection fit.** Wrong modules → wrong game. A cheap LLM self-check, or
  surface confidence to the human at freeze.
- [ ] **Premise judge before freeze.** The premise prompt is strong (central question) but
  unenforced — non-empty ≠ a real values tension. The highest-leverage single judge call, since
  everything derives from the premise.

## 9. Post-compile refiner pass (make a working game first, then improve it)

The image world's lesson: generate first, then run a refiner that fixes the problematic bits.
Apply the same shape here.

- [ ] **Refiner stage after the first successful compile.** Read the finished content and fix
  problematic pieces in place (flat/under-written nodes, thin examine text, weak match flavor) —
  quality-driven, distinct from the structural build loop. Plugs into §1's judge + `revise_*` tools.
- [ ] **Heal manual-edit `story_state` desync here.** Hand-edits leave `story_state.json` stale;
  it can't be recomputed from the artifact, only carried. Let the refiner re-derive / repair
  continuity after the fact rather than block edits at author time.
- [ ] **Cap refiner passes** (budget) like §1.

## 10. Tool surface gaps that cap quality

- [ ] **`read_story_state` absent from the places and combat tool sets.** PnC talk-node barks and
  encounter flavor are authored blind to continuity. Add it where those modules write.
- [ ] **No tool to read sibling content for setup/payoff.** `read_node` is by-id and the injected
  node-graph view is structural only — an author can't see what was foreshadowed elsewhere. Weigh
  a scoped "recent beats" read against context bloat.
- [ ] **`validate` exposed as an agent tool *and* auto-run each step.** Check whether the in-loop
  `validate` tool call is ever load-bearing or just burns a turn.
- [ ] **[0-pt pickup] Code-fill ASSIGNED id/name, don't ask the model for them.** Where a create has
  an assigned id/name (cast roster entry, a scene's slot id, an inventory item's ref), the prompt
  currently re-asks the model to emit them — pure downside: it's the surface a mislabel/drift lives on
  (the julian≠marcus cast desync), and it's wasted fields per the small-model strategy. Fix: inject
  the assigned id/name via the create-guard's existing `prepare(view, assigned, args)` hook (scenes
  already uses `prepare` to stamp the system-picked beat), so the model authors ONLY the fields it
  must (voice/history/example_lines). Deletes the id-drift error class instead of narrowing it.
  Wiring: `cast_view` exposes uncarded roster entries as slots + the guard's `assign`/`prepare` fill
  id+name. Straightforward; the mechanism exists.

## 11. Small-model fix-phase efficiency

- [ ] **Fix-phase efficiency.** Reachability/location/crossref repair runs one `edit_node` per
  failing item, and the small model repoints poorly (observed 3–4 edits on one node). Born-compliant
  enforcement attacks this at the source; reachability + crossref still grind. Consider batch-repair
  tools or born-compliant wiring.
- [~] **`write_node` tool-call reliability — partly a model ceiling.** Malformed tool JSON / prose
  instead of a tool call still happens; `_coerce_json` rescues the valid subset and the loop's
  nudge/escalation recovers (builds complete, slowly). Remaining levers: a more capable
  node-authoring model, or shrink the per-call JSON (fewer required fields / shorter nodes).
