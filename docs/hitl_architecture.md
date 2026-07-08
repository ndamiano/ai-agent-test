# Maestro HITL — formal architecture

> Status: formal vision. Supersedes the framing in `hitl_vision.md` (kept as the
> experiential north-star). This defines the *model* — what an asset is, what "done"
> means, how the human and the loop share the work — and the *UI* that renders it.

## Thesis

**A Maestro build is one shared to-do list drained by two workers: the loop and the
human.**

Every buildable thing — a scene, a character, a place, an ability, the premise — is
an asset with a single boolean: **`dirty`**. Dirty means "this needs attention:
build it, rewrite it, or at least look at it." Not dirty means "assume this is
fine." That's the whole state model. No stale/fresh trichotomy, no fingerprints, no
hashing — one flag.

- The **loop** drains dirty assets it can build/fix (author the missing scene, fix
  the broken reference).
- The **human** drains dirty assets by **thumbs-up** ("nah, this is fine" → clear the
  flag) or **thumbs-down + a note** ("change this" → hand it back to the loop to
  rewrite).
- **Completion = no dirty assets left.**

Both workers run at once, on the same list. A human edit can *add* dirty flags; a
thumbs-up *clears* one; the loop keeps working around them. That's the entire system.

---

## The dirty flag

Each component can be flagged `dirty` (default clean) with an optional `review_note` (why it's
flagged / what to change). The flag lives in a **sidecar** in the `human` HITL store, not on the
component schemas — zero component-schema changes (see "where `dirty` lives" below).

**Set dirty by:**

- **the loop** — a missing asset, or one that fails a machine check (today's errors
  are just dirty flags with an automatic fixer).
- **a human thumbs-down** — with the "change this" note attached as `review_note`.
- **an upstream edit** — editing an asset flags the things that depend on it (below).
- **the human, manually** — "this whole range needs another look" (for dependencies
  the machine can't see: plot, tone, foreshadowing).

**Clear dirty by:**

- **thumbs-up** — human says it's fine. Done.
- **rewrite** — the loop (or a human hand-edit) rebuilds it. A note-driven rewrite
  uses the `review_note` as the instruction.

The human is **never expected to sweep everything.** Dirty is a *highlight*, not a
gate on their attention — they can drain the dirty set if they want, or let the loop
rewrite it. The default posture is "trust the loop, glance at what's flagged."

---

## How dirty spreads (no fingerprints)

When an asset is edited, flag the things that depend on it. The dependency graph
already exists in the system:

- **reference edges** — `ir_crossref` already emits `{path, ref, kind}` for every id
  reference (a scene jumps to node X, an ability reads stat Y). Edit X → flag its
  referencers dirty.
- **state-flow edges** — the `state` module already pairs every flag/var/item's
  producer with its consumer. Edit the scene that sets `has_key` → flag the scenes
  that read it.
- **narrative order** — a scene's story-state delta feeds later scenes. Edit scene
  12 → the scenes after it *may* be affected.

The rule is a plain graph walk: "who points at the thing I edited? set their
`dirty = true`." No hashing, no auto-un-dirtying — a flag set this way clears the
same as any other (thumbs-up or rewrite).

**Propagation is transitive and always reflags.** Edit an asset → walk the *whole
downstream closure* (everything that depends on it, through the chain) and set every
one dirty — even assets the human blessed long ago. A bless only ever means "clean
*given upstream as it was then*"; when upstream moves, the flag comes back. (Edit
scene 4 → scene 12 depended on it → scene 12 is dirty again, no matter how many
iterations ago it was blessed.) This is the one thing fingerprints would have
detected automatically; without them it's a flat, correct rule: **any edit reflags
its entire downstream closure.**

During the *initial* build this is a no-op — downstream assets are still dirty
(unbuilt) anyway. Churn only appears on edits to already-clean assets, which is
exactly where reflagging is wanted.

---

## The shared to-do list

Collapse everything the build tracks — the loop's errors, missing assets, human
rejections, things-to-review — into **one list of active errors.** A dirty asset
**emits an error**, exactly like a failing machine check. That's all it is.

- The **loop** drains a dirty error by rewriting the asset (`review_note` = the
  instruction) — same as it drains any other error.
- The **human** clears a dirty error by thumbs-up (it's fine) — no rewrite needed.

Either path removes the error. **Completion = no active errors** (the list is empty)
— unchanged from today. Dirty doesn't add a new completion rule; it adds a new error
kind that the existing rule already covers.

### Dirty does not block — it self-heals through the drain

A dirty asset is **not a gate.** The loop never waits on a human and never routes
around a dirty upstream. It just keeps draining errors. Convergence is automatic:
editing scene 12 reflags its whole downstream closure dirty (scene 41 included), and
the drain rewrites every flagged asset until no dirty errors remain. If the loop
rewrote scene 41 *before* scene 12 settled, scene 12's edit reflags 41 again and it's
rewritten again — bounded by `_ATTEMPT_CAP`, correct by construction. No blocking
logic, no subtree gating.

**If the human wants the loop to wait for them, they pause** — the existing
pause-after-component and pause-after-step controls. Being waited on is an explicit
human choice, not a side effect of a dirty flag.

---

## Approval = thumbs up / down

An asset the loop finished is *provisional*: it passed the machine checks, but the
machine can't judge quality. So the human's verb over finished assets is a binary
judgment, available on every card:

- **Thumbs up** → clear dirty (or mark blessed). Won't be re-surfaced.
- **Thumbs down** → set dirty + prompt **"what's wrong?"** The answer becomes the
  `review_note` = the rewrite instruction. Rejection and "needs another look" are the
  same flag.

The human isn't required to thumb everything — unreviewed-but-passing assets are
simply "assume fine." Thumbs are how they *spend* attention where they want to, not a
chore the build waits on (unless the human opts into "I sign off on everything").

---

## The human as a concurrent builder (no pause)

Delete the current "pause the build first" gate on editing. The human never pauses to
touch an asset — they act, and the loop absorbs it:

- **Edit / rewrite-note any asset anytime** → its dependents flag dirty; the loop
  picks them up.
- **Thumb any asset** → clears or re-flags it.
- **Manually flag a range** → the verb for dependencies the graph can't see.

The only real serialization is the tool-dispatch lock that already exists
(`Services.dispatch`) so the loop and a human write don't hit the same file at once.
They don't need to *stop* for each other.

---

## The UI — a modular component browser

The UI is a direct render of the model: every component, real-time, with its state,
touchable and editable. It must be **modular** — adding a new component type to the
build should require little or no UI surgery.

### The shell (component-agnostic)

- **Tabs across the top, one per component type** in the build — Scenes, Characters,
  Places, Abilities, Items, … The tab set is *driven by the composed module set*, so
  a new module's component appears as a tab automatically. Each tab badges its
  **dirty count**.
- **A big central card** = the selected asset, full content, with a label/description
  of what it is. **Every field is editable inline.**
- **Left / right arrows** = previous / next asset within the current component type,
  with a position indicator ("Scene 4 of 12"). A **"jump to next dirty"** control so
  the human drains flagged items without hunting.
- **Dirty is highlighted** — on the tab badge, on the card (a colored edge / banner
  with the `review_note`), and as markers on the nav strip so dirty cards are
  findable at a glance.
- **A thumbs-up / thumbs-down control on every card**, with a **"change this" text
  field** that captures the note on thumbs-down.

### The content (per-component, pluggable)

The card's *body* is filled by a **per-component renderer**, chosen from a registry:

```
COMPONENT_VIEWS = {
  scenes:    SceneCard,      // screenplay view — NAME [emotion]: line
  cast:      CharacterCard,  // portrait + bio + traits
  world:     PlaceCard,      // the map / layout plan
  combat:    EncounterCard,  // stat block
  ...
  default:   SchemaCard,     // generic field/JSON editor
}
```

Adding a component type = optionally adding one renderer entry. Until one exists, the
**generic `SchemaCard`** renders any component as an editable field form from its
schema — so a brand-new module is fully usable (see, edit, thumb) on day one, and
gets a prettier bespoke renderer later. This mirrors the backend exactly: a module
already owns its *prompt* presentation block (`character_cards`, `nodes_index_block`,
…); its **human-facing card renderer is the same idea** — the module owns how its
component appears to the human, the shell owns navigation/dirty/approval. No central
router edits to add a component, same as the presenter registry on the Godot side.

### What the shell needs from the backend (uniform per asset)

`list assets of type X`, `get asset detail`, `set dirty / thumb / note`, `edit
content`. Content is decomposed per-component on disk already; expose it uniformly so
the shell is component-blind and the renderer is the only component-aware piece.

---

## Reused vs. new

**Reused:** the loop's error→fix drain (`agent_loop.py`) — an error *is* a dirty
flag with an auto-fixer; the `human` module's todo/waiver store — thumbs map onto it;
the dependency graph (`ir_crossref` records + `state` producer/consumer edges);
`Services.dispatch` write lock; `rewrite_node` for note-driven regeneration.

**New:**

1. **`dirty: bool` + `review_note` per component**, persisted in a sidecar in the `human`
   store (not on the component schemas).
2. **Dirty propagation** — on edit, walk the existing edges and flag direct
   dependents (+ a manual "flag everything after" verb).
3. **Dirty as a new error kind** — one Check emits it, the existing drain clears it;
   no new completion rule, no gating.
4. **Concurrent human builder** — human actions mutate the live error set; no pause,
   no blocking (the human pauses explicitly if they want to be waited on).
5. **The modular component-browser UI** — tabbed shell + per-component renderer
   registry + generic fallback + thumbs/note/dirty everywhere.

---

## Backend impact

Small and additive. The core loop, module ABC, substrates, engines, and compile are
**untouched**. The build already is a to-do drainer; this adds a new to-do kind, a
source that sets it, and a UI that renders it.

**`dirty` lives in a sidecar, not on the schemas.** Hold it in the `human` module's
existing cross-cutting HITL store — a set of asset idkeys + notes, exactly the shape
waivers already are, persisted in `RunState` next to story_state / scratchpad /
waivers. This means **zero component-schema changes**: `ir_assemble` / `ir_crossref`
never see `dirty` (it's build-time metadata, never projected to the engine), and no
module validator changes. The alternative (a `dirty` field on every schema) touches
every validator + the IR seam + requires stripping before compile — avoid it.

New pieces, all riding existing seams:

- **Dirty store** — extend the `human` HITL store (waivers' shape). *(S)*
- **Dirty → to-do** — a Check that sweeps the store and emits one Error per dirty
  asset; the loop's `get_errors` / `get_fix` drains it unchanged. *(S)*
- **Rewrite** — the existing `rewrite_node` / module author paths; `review_note` is
  the instruction. *(reuse)*
- **Propagation** — `mark_downstream_dirty(id)` over the DAG already computed from
  `ir_crossref` records + `state` producer/consumer edges, hooked after
  `edit_node` / `edit_component` / `rewrite_node`. *(S)*
- **Thumb / dirty tools + API endpoints + events** — human-only tools (like `force`),
  new `games.py` endpoints, event-bus emits for live UI. *(M plumbing)*

**No loop logic change.** Dirty emits an error; the loop drains errors already. The
loop never waits, never blocks, never gates a subtree — a dirty asset is auto-rewritten
like any other error, and convergence falls out of reflag + drain (above). The
prioritizer's existing dependency ordering (`check_rank` / `Module.priority`) already
tends to rewrite upstream before downstream; reflagging covers any out-of-order case.

## Settled policy

- **The loop never waits on the human.** A dirty asset is auto-rewritten like any
  other error. A human who wants to be waited on uses the *existing* pause
  (after-component / after-step) — being waited on is an explicit choice, never a
  side effect of dirty. (So there is no new `awaiting_human` state to build.)
- **Completion = no active errors** — unchanged. Dirty is just another error kind.
- **Thumbs-up clears the error; thumbs-down sets it (with a note).** The human is an
  *alternate* way to clear a dirty error, never a gate the loop stops for.
