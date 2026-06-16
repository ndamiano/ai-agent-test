# Maestro: Agentic Visual Novel Generation — Architecture Spec

## Context & Goal

Rebuild the VN-generation system in Maestro. The existing **pipeline** approach (fixed
stages, each writing JSON to disk for the next) is being **ripped out and replaced** — it's
too rigid and the output quality is poor. Pipelines survive only as *implementation details
inside individual tools*, not as the system's control structure.

**Target:** Generate a *good* (not great, not amazing) visual novel from a single prompt,
output as a runnable Ren'Py project.

**Hard constraint:** Runs locally on a single desktop — RTX 5090, 32GB VRAM. Models are
small relative to frontier models. This constraint shapes everything: the architecture must
keep every individual model call **narrow, bounded, and schema-constrained**, because small
models are good at local scoped tasks and bad at long-horizon, globally-coherent reasoning.

## Core Architecture

An **agentic loop**, not a pipeline. But it borrows the pipeline's two best properties
(completion guarantee, no context rot) without its rigidity.

Three conceptual layers:
1. **Spec layer** (agentic, cheap, human-gated): produces and amends the per-game contract.
2. **Executor** (NOT an LLM): drives the loop, builds context per step, handles retries.
3. **Stage primitives** (the old stages, demoted to tools): genre-agnostic capabilities the
   agent composes.

## The Spec (per-game contract)

The escape from rigidity: **the completion spec is generated fresh per game by the agent**,
NOT retrieved from a human-authored genre library. There is no "VN schema" file authored in
advance — requiring one would just relocate the rigidity up a level (you'd only be able to
make genres you pre-specified).

Flow:
- Agent drafts a spec from the request: a list of **components** (premise, cast, spine,
  nodes, endings, etc.), each with a **checkable done-condition** the agent commits to.
- **Human reviews and edits the spec interactively, then approves.** This is not a UX nicety
  — it's the load-bearing component. It places the human in the loop at exactly the step a
  small local model is weakest (global coherence / completeness) and where the human's
  strengths matter most. Build tools refuse to run until the spec is human-approved.
- Once approved, the spec is **frozen**. It becomes the contract: the agent builds against
  it, and "done" = artifact satisfies the spec. Freezing is the *human's* action, not a tool
  the agent calls.

"Done" guarantee is preserved: not because steps are hardcoded, but because the artifact is
validated against the frozen spec. (Note: running all stages ≠ being done — the old pipeline
could run every stage and still ship a broken VN. Validation against a spec is strictly
better.)

### Done-conditions must be checkable-by-construction
Done-conditions cannot be free prose, or `validate` can't run them. They must be a small
typed representation: a count, a field-existence check, a distinctness check over a set, a
"compiles" flag, etc. Because the *agent* declares these invariants, code can check things
you never anticipated (e.g. "5 weapons, each distinct" → count + distinctness check), even
for genres you never specified. **This representation is load-bearing — pin it down early.**

Fuzzy quality checks ("are voices distinct", "are endings differentiated") still need LLM
review — but that's just review, available in any architecture; it is NOT a selling point of
this design and shouldn't be leaned on structurally.

## Tools

Principle: **one tool per distinct capability**, where "distinct" = different inputs or
different effects. NOT "as few tools as possible" — overloading a tool (e.g. a god-mode
`write_component` dispatching on a `type` param) just hides complexity in a parameter the
model must still get right, and prevents per-operation schemas/validation. Splitting by
*target* (premise vs. ending) is wrong — that's a parameter. Splitting by *kind of action*
(write text vs. generate image vs. compile) is right.

Also: **bound every form of state the agent touches.** Do NOT give raw `read_file`/`write_file`
filesystem access — that recreates unbounded, unvalidatable scratchpad state and causes
context bloat. Use *scoped* memory tools instead.

Two tool families:

**Spec tools (rare, human-gated):**
- `propose_spec(request)` → drafts initial spec (components + committed done-conditions).
  Returns to the human for review, does NOT enter the build.
- `amend_spec(changes, reason)` → the ONLY path to changing a frozen spec mid-build. `reason`
  is mandatory (forces articulation of why the spec was wrong; serves as approval signal +
  debugging trail). Always pauses for human approval.
- (No `freeze_spec` tool — freezing is the human's out-of-band approval action.)

**Artifact tools (frequent, autonomous):**
- `write_component(component_id, content)` — fill a piece of the artifact (the old stages).
  Parameterized by target; agent chooses order.
- `read_component(component_id)` — inspect a piece on demand.
- `generate_asset(...)` — distinct capability (different backend/validation/failure modes
  than text generation); a real separate tool.
- `validate(component_id?)` — runs the code-checkable done-conditions; returns the structured
  failure list. This is the agent's steering signal AND its primary context tool (see below).
  Empty failure list against frozen spec = completion guarantee.
- `compile_renpy()` — the hard gate: does the artifact produce a launchable Ren'Py project?
  The one validator that can't be faked by review. **Build this first — it's the spine.**
- `update_scratchpad(summary)` — scoped working memory (see context section). Replaces rather
  than appends, forcing compression.
- `request_review(question, options)` — optional tightly-scoped escape hatch for genuine
  stuck-between-choices moments. Expect rare use; heavy use signals an underspecified spec.

Rough total ~10–14 tools. The number isn't the point — per-tool clarity is. The shape that
matters: spec tools are fenced/gated, artifact tools run free. **That fence is the design.**

## Check-in Mechanism

Do NOT rely on the agent deciding when to check in (a small model will either never or always).
Make check-ins **architecture-triggered**:
- Any `amend_spec` call → pauses for human (spec changes are inherently human decisions).
- **Milestone pause:** when `validate` first passes for a major component, surface it for review.
  Finishing a component *is* the check-in — the agent doesn't judge when to interrupt.

## Context Management (avoiding context rot)

The pipeline's best property was no context rot (each stage got a clean fixed context). Steal
that discipline without the rigidity.

**Key insight:** the agent does NOT need its transcript to decide the next step. It needs only:
(1) the frozen spec, (2) current artifact state via `validate`'s to-do list, (3) a short
summary of the last result. Everything else in a transcript is rot — and on a 32GB local model,
rot is fast and severe.

Disciplines:
- **Stateless per step.** Each iteration rebuilds a fresh minimal context from durable
  external state (spec + `validate` output + last-result + compressed scratchpad). The
  transcript is NOT used as memory.
- **Durable state on disk is the source of truth.** Artifact JSON + scratchpad live in files.
  Context is a short-lived projection rebuilt each step.
- **Tool outputs are pointers, not payloads.** A 4000-token premise goes to disk; context
  holds "premise: done, validates." Re-read via `read_component` only when relevant.
- **Scratchpad is summarized, not appended.** `update_scratchpad` *replaces*. Make it
  **structured** (forced fields: current-goal, recent-decisions, open-questions) so even a
  weak model maintains something useful.
- **`validate` recomputes the to-do list from durable state each step** — the agent never
  *remembers* what's left, it recomputes it. Can't rot on "what's done."

Result: context stays ~constant size as the game grows, because the growing thing (the
artifact) lives outside the window.

## Story State / Narrative Continuity (the subtle part)

Two DIFFERENT kinds of state — do not conflate:
- **Process state** ("what steps have I taken") — recomputable fresh each step from the
  artifact via `validate`. Handled by everything above.
- **Content/story state** ("what's happened in the game world so far") — **cumulative and
  order-dependent; CANNOT be recomputed**, only carried. This is the gap the rest of the
  design doesn't cover.

Two failure modes to avoid:
- Re-injecting full prior script into context → context rot in a content costume; blows the
  window on a long VN; quality collapses exactly when continuity matters.
- Pure statelessness → node 8 contradicts node 3.

**Solution: a separate, first-class "story-state object" (a continuity bible), distinct from
the script output.** Every content-generating step produces TWO things: the node content
(→ artifact/output) AND an update to the story-state object. The next step reads the
story-state object, NOT the prior script text.

Why it stays affordable: it's a **current-state snapshot**, not an accumulating log — stays
~constant size as the script grows unboundedly.

Structure it around what continuity actually breaks on (NOT lossy prose):
- **Established facts** (e.g. player learned the tablet is fake)
- **Entity states** (e.g. Betta: trust wary→warming, currently at the harbor)
- **Open threads** (raised-but-unresolved subplots)
- **Recent-events tail** — the last node or two in fuller detail; ages out into compressed
  facts as it scrolls off. (Continuity isn't uniform: immediate past needs detail, distant
  past needs only durable consequences. This is a bible with short-term memory.)

**Branching (VN-specific wrinkle):** "what's happened" is path-dependent — node 8 reached via
node 3 vs node 5 has different established facts. Two options:
- **Preconditions/postconditions per node** (planner-style): each node declares what must be
  true to enter and what it makes true on exit. "What's happened so far" = compose
  postconditions along the branch reaching the node. Makes continuity **code-checkable**
  (`validate` catches "node 8 assumes player met Betta, but path 3→8 never introduces her").
  More demanding for the model to generate. Use on consequential branch points.
- **Spine-tracked state** with branches as local variations that rejoin — looser, cheaper,
  probably fine for "good, not great." **Lean here first**, add precondition/postcondition
  rigor only where branches carry real consequence.

**Story-state schema is a spec-time decision; story-state content is a build-time
accumulation.** Different games track different things (romance → relationship states;
mystery / the Elena+obsidian-tablet+doomsday arc → clue-discovery + pattern progression). The
agent declares "this game's continuity hinges on these state variables" at spec time.

## Open Questions to Resolve Before/During Build

1. **Done-condition representation** — the typed, checkable-by-construction shape. Everything
   hangs off this.
2. **Structured scratchpad shape** — forced fields for working memory.
3. **Write-node split decision:** the write-node step must do two things — produce good
   dialogue AND accurately report the story-state delta. A small model doing both in one call
   tends to shortchange the bookkeeping. Decide: split into two steps (write, then extract
   state-delta from what was written) vs. keep fused. Quality/cost tradeoff. Natural thing to
   pin down before building the node loop.

## Build Order Suggestion

1. `compile_renpy()` first — it's the spine; proves the artifact→runnable-game path.
2. Then the executor + `validate` + durable-state-on-disk substrate (test with a hardcoded
   spec the agent *would* have generated, so you're testing executor/validators in isolation).
3. Then hand spec-authoring to the agent with interactive human review.
4. Story-state object + node loop last (resolve open question #3 first).