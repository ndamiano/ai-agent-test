# Maestro HITL Vision — the insanely-good human-in-the-loop

> Status: vision + backlog. This is the target, not the current state. The issue
> list at the bottom is the gap. Drain it, don't implement it wholesale.

## The one principle everything derives from

**Maestro's only source of taste is the human.** The local models can build
structure, wire references, and fill skeletons — they cannot judge whether the
result is *good*. Quality is the product (CLAUDE.md north star), and quality
enters the system exactly once: through a human's judgment.

So the entire HITL experience has one job:

> **Extract the human's taste and apply it at the cheapest possible moment, with
> the least possible friction.**

"Cheapest moment" = earliest. Taste applied to the spec costs nothing (nothing is
built). Taste applied to scene 40 costs 39 scenes of wasted compute. Every part of
the ideal experience is a corollary of pushing steering earlier and making it
frictionless.

Two facts constrain the design:

- **Builds are long (~an hour) on local models.** The human cannot sit and watch.
  The experience must be *async-first*: fire, walk away, get pulled back only when
  a decision or a review is genuinely worth their attention.
- **The human is the only judge.** The loop cannot self-assess quality. So it must
  make its own output *legible* (show what it made, not what it did) and raise its
  hand when it's uncertain, thrashing, or at a fork.

**North-star for the experience:** a person makes a game they're proud of while
spending under ten minutes of *active* attention on an hour-long build — and never
once feels the build got away from them.

---

## Seven properties of the ideal experience

1. **Taste is front-loaded.** The spec gate is where steering is free. It should
   feel like pitching and shaping a creative brief, not filling a form of module
   checkboxes and sizing knobs.

2. **Async-first.** The default posture is fire-and-forget with pull-back-on-signal
   (notifications, a review queue). Sitting and watching a log is a failure mode,
   not the design.

3. **Steer without stopping.** Injecting intent ("make the villain colder") is
   queued and non-blocking. Pausing is a last resort, never the *price* of leaving
   a note.

4. **Legible, not verbose.** The human sees the plan filling in and the output
   landing — semantic progress (components, scenes, cast) — not a log of internal
   steps or a raw `step 47/300`.

5. **The loop surfaces its own doubt.** Thrash, parks, and low-confidence output
   raise their hand automatically. The human never has to *poll* for problems
   (today Nick has to babysit builds for thrash — the ideal inverts that).

6. **Every artifact is a conversation.** Any generated thing — scene, character,
   map, image, whole tone — can be redirected in natural language, at any
   granularity, at any time. A form field is a failure; a note is the interface.

7. **The gate is a decision, not a chore.** When the build genuinely needs a human,
   it presents a crisp choice with options and one-line consequences — not a raw
   error string the human has to decode.

---

## The ideal session, concretely

**Ask.** "Make me a noir detective game." Maestro streams back a *pitch*, not a
form: title, the hook, the feel, who you play, the core loop — readable prose. The
modules it chose are explained in plain language underneath ("Combat: the
interrogations escalate to fights"). The human talks back — "darker, less pulpy,
give the widow her own agenda" — and the brief reshapes conversationally. One
cheap sample (an opening line, one character card) lets them calibrate tone before
committing an hour of compute. They freeze: one button that reads *"Build this."*

**Build kicks off — a living board, not a log.** Components appear as cards filling
in: *Characters ✓ (4) · World ✓ · Scenes 3/12 building · Combat pending.* Each
finished scene lands as a readable card the moment it's done — the human skims
scene 1 while scene 4 generates. A note on scene 1 ("more menace") queues and
applies at the next relevant step *without stopping the build.*

**They walk away.** Twenty minutes later, a push: *"6 scenes done, 2 flagged
low-confidence — review?"* They open it, read the two flagged scenes, thumb one
down with *"the confession is too on-the-nose,"* thumb up the rest. The note
re-queues that one scene.

**The build hits a fork.** Two characters share no scene; the loop isn't sure who
confronts whom. Instead of parking silently and burning fix attempts, it raises a
**decision card**: *"Who confronts the killer — the detective, or the widow?"* with
a one-line consequence each. The human taps one. The loop continues.

**Premiere, not a status flip.** The build doesn't end on `build_done ok=true`. It
*premieres*: the opening scene, the cast, the map — *"Play it, or tell me what to
change."* The human plays, hits a flat scene, clicks it in place — *"she wouldn't
forgive him this fast"* — and it regenerates.

Everything above is achievable with the architecture that already exists (frozen
spec + agentic loop + per-module errors/fixes + events). The gap is surfacing and
wiring, plus a few genuinely new capabilities (decisions, steering queue,
notifications).

---

## The backlog

Grouped into epics. Each issue: rough size (S/M/L), surface (FE/BE/both), and type
(**wire** = capability exists, surface/connect it · **new** = genuinely new). The
**spine** (minimum path to the vision) is called out after the list.

### Epic A — Spec as creative collaboration (front-load taste)

- **A1 — Spec-as-pitch.** Render the spec as a readable creative brief (title,
  hook, feel, protagonist, core loop in prose) with modules explained in plain
  language, instead of a module table + sizing knobs. *(M, FE + light spec_write
  shaping, new)*
- **A2 — Conversational amendment.** Reshape the spec by talking ("darker", "add a
  rival") using the existing `amend_game_spec` chat tool, on the same surface —
  not by clicking +/- on modules and knobs. *(M, both, wire)*
- **A3 — Seed the story bible.** Let the human inject facts/entities/threads into
  `story_state_schema` before freeze — a character or premise the model must
  honor. *(M, both, wire)*
- **A4 — Cheap tone preview.** Generate one cheap sample (opening line or one
  character card) pre-freeze so the human calibrates tone before spending an hour.
  Optional/skippable — it costs one call. *(M, both, new)*
- **A5 — Freeze reads as "Build this."** The freeze affordance shows a plain
  contract summary of what will be made + a rough time estimate, not "Approve &
  freeze". *(S, FE, new)*

### Epic B — The living build (legible, async-first)

- **B1 — Component plan board.** Cards per component with state
  (pending/building/done/parked) and counts (scenes 3/12), replacing the rolling
  text log as the primary view. Needs a component-state model derived from
  `component_complete` + failing-set. *(L, both, new)*
- **B2 — Real progress + elapsed + ETA.** Surface step N/max, an elapsed timer, and
  a naive ETA from step rate. `max_steps` is already emitted; `elapsed` is computed
  server-side (`run.py`) but never sent. *(S, both, wire)*
- **B3 — Artifacts stream in legibly.** Finished scenes/characters land as readable
  cards the moment they complete, browsable while the rest builds — pushed, not
  refetch-on-event, and rendered (not raw JSON). *(M, both, wire)*
- **B4 — Live todo/health.** Attach the effective `todo` + failing detail to
  `build_step`/`build_started` events (today they're null, so the live pane only
  updates on slow refetch). *(S, BE, wire)*
- **B5 — Thrash/park surfacing.** Handle `error_parked` client-side and raise an
  early-warning card ("scene 3 failed 4 fix attempts — look?") so the human stops
  having to babysit for thrash. *(M, both, wire)*
- **B6 — Push notifications.** Notify on milestone / decision / park so the human
  can walk away and get pulled back (browser notification or desktop). *(M, both,
  new)*
- **B7 — Build health at a glance.** A progressing-vs-spinning signal from the
  failing-count trend, for trust calibration. *(S, FE, new)*

### Epic C — Steer without stopping (intent injection)

- **C1 — Queue a note without pausing.** A steering note ("make the villain
  colder") is picked up at the next relevant step, not gated on a pause. The
  `human` module already holds notes — extend it to carry steering into the fix
  context. *(L, BE, new)*
- **C2 — Redirect any artifact in natural language.** Generalize `rewrite_node`
  (scenes) to characters, maps, tone, images — one "tell it what to change"
  affordance everywhere. *(L, both, wire→new)*
- **C3 — Non-blocking edits.** Remove the "pause the build first" gate
  (`_require_editable`); queue edits to apply at a safe step boundary. *(M, both,
  new)*
- **C4 — Durable, visible steering.** A "your notes" panel showing pending/applied
  steering so the human trusts it landed. *(S, FE, new)*

### Epic D — Decisions, not errors (the gate as a crisp choice)

- **D1 — Wire `awaiting_human`.** The loop actually pauses (sets
  `RunControl.status="awaiting_human"` + emits the event) when the top error is
  human-owned, instead of burning `_ATTEMPT_CAP` fix attempts and quitting with
  `build_done ok=false`. This is a dead wire today (UI + docs advertise it; nothing
  emits it) and it's the foundation for D2/D3. *(M, BE, wire)*
- **D2 — Decision cards.** When the loop faces a genuine fork or an unresolvable
  park, a module emits a structured *decision* (options + one-line consequences),
  not a raw error string; the human taps a choice and the loop resumes with it.
  *(L, both, new)*
- **D3 — Human todos actually block completion.** Today `build_done` fires
  regardless of open human todos. Make them blocking and turn clearing them into a
  first-class review queue, not a hover "done" button. *(M, both, wire)*

### Epic E — Premiere & post-build steering (judge the output)

- **E1 — Completion is a premiere.** The finished build presents the opening scene,
  cast, and map with "play or change," not a status badge flip. *(M, FE, new)*
- **E2 — Redirect from inside the played game.** Click a flat scene while playing →
  rewrite in place (`rewrite_node` exists; needs the play↔edit bridge). *(L, both,
  wire→new)*
- **E3 — Targeted, note-driven asset regen.** Regenerate *one* asset from a note
  ("the map feels cramped", "she looks too old") instead of the coarse
  regenerate-all. *(M, both, wire)*

### Epic F — Chat as the front door

- **F1 — Stream chat responses.** Today chat blocks on `to_thread(agent.chat)` and
  shows bouncing dots; stream tokens + tool-use progress ("drafting spec…"). *(M,
  both, new)*
- **F2 — Chat→build continuity.** A chat-created run auto-surfaces and auto-selects
  — no tab switch + manual Refresh. *(S, both, wire)*
- **F3 — One continuous surface.** Asking for a game and watching it build is one
  conversation, not two disconnected tabs. *(L, FE, new)*

### Epic G — Trust & observability plumbing (cross-cutting)

- **G1 — Handle `error_parked` client-side** (emitted, unhandled today). *(S, FE,
  wire)*
- **G2 — Wire waiver notes** (backend `WaiveBody` accepts `note`; UI sends none, so
  the Contract tab falls back to raw idkeys). *(S, FE, wire)*
- **G3 — Fix optimistic-control desync** — pause/resume/cancel reconcile with
  server truth instead of flipping local state blind. *(S, FE, wire)*
- **G4 — Per-run WS rooms** — today every event broadcasts to all clients and the
  client filters by run_id; scope server-side as it scales. *(M, both, new)*
- **G5 — Reconnect replays state** — a client reconnecting mid-build rebuilds full
  state from a snapshot/event-replay, not just live-forward from the moment it
  reconnected. *(M, both, new)*

---

## The spine (minimum path to the vision)

If draining in order, this sequence delivers the *feel* fastest and unblocks the
rest:

1. **B4 + B2 + G1** — make the build legible and honest (live todo, progress/ETA,
   parks surfaced). Small, high-signal.
2. **B1 + B3** — the component plan board + streamed artifacts. This is the single
   biggest okay→great jump; the build stops being a black box.
3. **D1 → D3 → D2** — turn the human gate real: park for the human, block on todos,
   then upgrade parks to decision cards.
4. **C1 + C3** — steer without stopping. Now the human can leave notes and walk
   away, which is the whole async-first promise.
5. **B6** — notifications close the async loop (walk away, get pulled back).
6. **A1/A2** and **E1/E2** — richen the two ends (spec collaboration, premiere).

Everything in Epic F and the rest of G is polish that makes the spine feel
seamless.
