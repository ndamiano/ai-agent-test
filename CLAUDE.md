# Maestro

An AI platform that makes things. The goal is simple: user says "make me a game", a while later a
good game exists. The AI quality is the product — everything else (UI, install, visuals) is
scaffolding.

The north star is **any game**: ask for a game you imagine, get a real, playable game in a browser.
"Good" is the constraint we consciously bend today — breadth comes first. Every build runs on an
open-weight model on a modest rented card (a 5090-class card, a ~30B model).

| Where to look | For |
|---|---|
| `docs/vision.md` | the destination and the non-goals |
| `docs/roadmap.md` | where we stand against it |
| `docs/build_path.md` | the build path module by module |
| `docs/architecture.md` | processes, state, trust boundaries |
| `docs/local_dev.md` | settings, model servers, how to run anything |
| `docs/experiments.md` | what each change to the loop actually measured |

This file is the doctrine: the laws the code exists to serve, and the standards for changing it.
Anything that reads as a description of a module belongs in `docs/build_path.md` or the module's own
docstring — the docstrings are the authority on contracts.

---

## How it works

The model writes **real browser game code** — plain HTML/CSS/JavaScript, no framework, no build
step, no engine of ours between it and the screen. Two stages:

1. **Prompt (human-gated):** the run keeps the user's words VERBATIM as `ask` and shows them; one
   inference — the DESIGNER (`maestro/codegen/design.py`) — turns them into a systems design, and
   the human reads THAT in the box it will be sent from. **THE PROMPT IS THE ARTIFACT** — what is
   on screen is byte for byte the build's one user message, so approving it and building it are the
   same act. The designer does not break this because the design is what the box shows and the
   design is what builds; nothing sits between the box and the build's input. A designer that fails
   or answers empty leaves the words themselves in the box. Pressing **Build** stores the edit and
   starts the build, so there is one action and one writer. The page renders the design as headed
   sections read from its shape, over the same string the edit box holds — a view, never a copy.
2. **Build:** a non-LLM **driver** (`maestro/codegen/build_chain.py`) hands the model eight tools —
   `list_files`, `read_file`, `write_file`, `edit_file`, `generate_media`, `compose_scene`,
   `compose_world`, `done` —
   and a running transcript, and lets it write the game. It decides the file layout, the data shapes,
   the systems, and what art gets drawn. It calls `done` when the game is playable. The build is not
   a resident loop: each llm turn is a job on the `llm` queue and its completion drives the next
   turn, so the driver holds no state between turns.

---

## The laws

These are measured, not preferred. Each one cost something to learn.

### Why no kit, no contract, no gates

Measured over a 25-game grid across two local models (2026-07-27): given the same requests, this
shape produced working games in 2–4 minutes, 4/4, while an interface-first pipeline (declare an
architecture → review it → author per function) produced unplayable ones in 5–37 minutes from the
*same model*. The decomposition's own contract was the failure: `draw_hand` declared and implemented
but never called, in all three models, because the schema had no boot-sequence contract. A local
model given the whole problem and a transcript decomposes it *by domain* and wires it up; given a
partial view and a schema, it fills the schema.

### The transcript IS the memory

No context-rebuilding, no per-step minimal window. When the prompt approaches the context limit,
the FILE BODIES in the old rounds are replaced by a stub naming the path and size — the round still
says what the model wrote, read and edited, and the bytes are on disk — and only if that is not
enough are the OLDEST WHOLE ROUNDS dropped and the model re-grounded on the current file listing
(`build_steps.compact`). Rounds are never split — a `tool` message whose assistant `tool_calls` is
gone is an orphan, and a chat template is entitled to 500 the turn. The re-grounding matters more
than the trim: the dropped rounds are where the model watched itself write the files, so without it
the model edits code it no longer remembers.

### A gate may only detect BROKEN, never "bad"

A constraint the model builds to satisfy is only safe when satisfying it IS the goal. "Must not
crash" can only be met by not crashing; "the game must DO X" is legitimately false for some games, so
no edit satisfies it and the loop grinds to its cap while the model contorts the design to appease
it.

Two things stand between a build and `built`: **`index.html` exists** — without one there is nothing
for a browser to open — and the **error gate**, which opens the staged game headless after a playable
finalize and re-enters the fix machine with one uncaught exception and its address. An uncaught
exception satisfies the guardrail: `this._doIdle is not a function` can only be met by defining it.
Measured 2026-07-30 after a 25-game day shipped five load-dead games the
pipeline never saw — nine of nine broken cells went to zero unattended, and the rules that made it
converge are all load-bearing.

The gate loads the game, shows the model one screenshot, and presses what the model says the title
screen offers — nothing more. Teaching it to play would be a "must DO X" gate in disguise.
Everything past that is a HUMAN judgement: a game that runs but plays wrong is
obvious to a person and near-impossible for code, so the gap stays VISIBLE rather than filled with a
proxy.

### The one exception is SAFETY, and it is not a gate the model builds against

It acts after the fact and the model is never told the rule to satisfy. Every seam fails closed —
a flagged game is HELD rather than staged, a render without a clean verdict is never saved — and
every refusal is recorded. The whole of what the model hears is the same as a blocked prompt: draw
it with code instead. Mechanism: `artifact_screen.py` and `asset_chain.py` in `docs/build_path.md`.

### The FIRST `done` is answered, not accepted

One nudge back — what is unfinished, what is stubbed — and the second `done` ends the build. It costs
one turn, and it is asked ONCE: a model told twice that it is not finished starts inventing work.
Measured over a 17-cell grid (2026-07-30): best or joint-best on three of four game requests, while
every arm that bought depth by splitting authoring across builds shipped load-blocking defects
instead. That one turn also carries the ART AUDIT (`asset_use`), because it is the only moment a
build hears whether the art it asked for is in the game — and because a second turn spent on it would
be the second telling that starts the inventing.

### "Did it deliver?" is a HUMAN question

A build ends when the model calls `done` twice (or hits its step cap) and nothing machine-side judges
the result. The human plays it and says what to change — `run --fix <run_id> "<note>"` re-enters the
same turn machine with the note as its request, and the model lists and reads the files itself, so
there is nothing to hand it up front.

An automated judge-then-fix round spends a build's steps re-breaking what already worked, and
scores worse the second round than the first; any judge proposed here has to answer that
measurement first (`docs/experiments.md`).

### The game asks for its own art, as it writes the code that uses it

`generate_media(id, prompt, kind)` enqueues one render and answers IMMEDIATELY with the path the file
will appear at; the model writes that path into the game as it writes the code that uses it. No
planning call, no source rewrite, no static analysis of what the game spawns — the GPU draws art
while the llm turns keep writing code. `kind` (sprite, tile, scene, mesh) is the one thing the prose
cannot carry, because it decides what a landed render owes: a sprite is matted to its subject, a
tile or scene keeps its frame, a mesh lands at a known scale. The compute budget is the only cap on
how much art a build may ask for, and a refused enqueue is REPORTED as "draw this one with code
instead" — a build that cannot have art is told to draw one, never left waiting for a file that is
not coming. Mechanism: `assets.py` and `asset_chain.py` in `docs/build_path.md`.

### The request is a SYSTEMS DESIGN, and the model writes it

The ceiling of a build is the design it is given, not the model. A systems design is a list of
systems, one paragraph each: every thing in the game is a RECORD with named fields; anything with
structure (a track, a map, a deck, a wave list) comes from a SEEDED GENERATOR with a VERIFIER that
re-rolls on failure, never literal content; one record is the source of truth for each kind of
thing; numbers are GIVEN, never derived; art is named AS art ("ask for X as a sprite"); screens are
a state machine. Measured 2026-08-26 on one request: the plain request through three stages spent
153 turns on a track that was never a circuit; a hand-written design made a real circuit that was
"extremely shallow"; the model's own design of the same request made race select, an
unlock ladder, weather, fuel, damage and a podium — "a million times better". Numbers are given
because a clause the model must DERIVE ("no bend tighter than the car can drive") costs ~150K
characters of physics per turn; the same clause as a value ("no radius under 140 px") costs
nothing. Two weaknesses stand open: the model's numbers carry no units, and it spends the fewest
words on the system that needs the most (the hand design gave the track a third of its words and
its track was better).

### A system starves when it is one clause among many — the design stage is what feeds it

Measured 2026-08-02/03, as staged construction: a duel built alone earned a dedicated AI module; the
same duel inside the full request earned zero opponent code. Staging fixed that by giving the
hardest system a whole build; the systems design fixes it in one build, by giving every system its
own paragraph, and superseded staging.

---

## Adding a capability

The default answer is a line in `prompts/build.txt`, not code. The order is **prompt line → a snippet
in the repo → a primitive we own**, and a primitive only after a prompt line has failed twice, across
two models.

**A prompt line is measured against a control arm or it does not ship.** FIXED CANVAS is the shape of
one that earns its place: two requests it was not written for, built with and without, 2/2 against
0/2. A second candidate rode along in the same run — draw every layer through one world-to-screen
offset, written for a real map-drawn-as-a-strip bug — and was DROPPED, because the defect did not
reproduce in either control build, so the line pointed at no number.

The snippet rung is `runtime/vendor/lib/` — files beside the game whose header comment is the API,
named in one line of `build.txt`. It closed the 2026-07-27 ledger (3D scenes lit near-black, silent
games, arrow-keys-only input) 6/6 against a control arm, with zero misuse, because the doc lands in
the window on `read_file` at the moment of use rather than in the prompt on every turn.

An unused schema costs every turn of every build, so a tool that fails to earn its place comes back
out. `generate_media` is the standing case — see the art ledger in `docs/build_path.md`.

---

## Code standards

### No half measures, no backwards compatibility
When a full fix is available, take it — never the partial patch that leaves the root cause in place.
We are both producer and consumer: no external callers, no published API, no old data to migrate. So
never add backwards-compat shims, deprecation paths, or "keep the old way too" code. Delete the old
way and move the call sites. Surgical means *small and complete*, not *small and half-done*.

### Minimal and surgical
Edit only what the task requires. No cleanup/refactoring/"while I'm here" changes unless asked. Three
similar lines beats a premature abstraction. No feature flags, compat shims, or half-finished stubs.

### Tests
Write tests for every non-trivial change — test behaviour and contracts, not implementation details.
Run tests before reporting done. Fix failures first.

### Code review
After any non-trivial change, self-review the diff: security, unintended scope creep, missing tests,
regressions, and whether the documentation the change invalidates is in the same diff. Do this before
declaring done.

### Documentation
Keep the docs a change invalidates in the same commit as the code. Doctrine here, modules in
`docs/build_path.md`, measurements in `docs/experiments.md`, operations in `docs/local_dev.md`. Task
lists and experiment harnesses live outside the repo entirely (`~/Documents/Labs`); only their
results come in.

### Comments
Default: none. Only when the WHY is non-obvious (hidden constraint, workaround, subtle invariant).
Never comment WHAT the code does.

### Error handling
Only validate at system boundaries (user input, external APIs, tool results). No defensive fallbacks
for things that can't happen.

---

## Keeping prompts hill-climbable

1. **One `.txt` file per LLM call.** Never inline prompt strings in Python. Each call gets its own
   file under `maestro/codegen/prompts/`.
2. **Load-bearing system prompts belong in a `.txt` too**, not a hardcoded string.
3. **A prompt fix states a general law; examples only illustrate.** Never encode the game that
   triggered it, and validate on the battery rather than the motivating case.
4. `build.txt` is the whole of what the model is told about how to make a game. Every line in it must
   earn its place — it is read on every turn of every build.

## Small model strategy

Small models aren't dumb — they're easily distracted, following the most recent, most concrete
instruction. Two things that DON'T follow from that, both measured:

- **Do not decompose the work for them.** Given the whole problem and a transcript they decompose it
  by domain and wire it up; given a partial view and a schema they fill the schema and never call it.
- **Do not hand them a rule they cannot satisfy.** A gate that is usually right costs more than no
  gate: when it is wrong it burns a hundred steps and never says it is unsure.

What does help:

1. **Output skeleton before field descriptions** — show exact structure first, let the model fill it.
2. **Say what is true, not what is forbidden.** "Bind WASD and the arrows" beats "don't use only
   arrows".
3. **Give them the whole window.** The input budget rides `llm.n_ctx`, not the model category, and a
   build's input IS its transcript — set `n_ctx` to what the server was launched with.
4. **Thinking ON, effort a setting, the per-turn cap riding the window.** Measured 2026-08-25 on
   qwen3.8_27b (ninfer): with thinking, 8 of 8 finished builds were real games of their design —
   notes tied to generated music, a village with streets, the first playable 3D dungeon in any
   arm — where the same designs without thinking made "something kinda close". It costs 3–4× the
   GPU seconds and it is worth it. Thinking is front-loaded — 25–36K tokens on turn 0, near zero
   on mechanical turns — and the model writes the whole game inside its think before the first
   tool call, so the cap must hold a whole think plus the answer: at 16K every opening turn ended
   at the cap with nothing; at 50K a turn that wanted 68K re-thought from zero on every retry, five
   minutes each. The cap is `n_ctx − prompt − 6K` (floor 16K): ninfer admits a request only when
   prompt + max_tokens fits its window, and with that cap the same turn stopped on its own at 47K
   and the build finished in 24 turns. The window is not a ceiling the model cannot reach: a
   fourteen-system design ran a 120K budget dry on turn 0 and called no tool, so what turn 0 costs
   is set by the design's breadth, and the fix for that is the design, not a bigger cap.
   Effort is `llm.reasoning`, forwarded as `reasoning_effort`;
   `none` DISABLES thinking on ninfer, and ninfer has no thinking budget, so the cap is the only
   bound. The llama.cpp path keeps `enable_thinking=false` — it has no budget either and was never
   measured with thinking on.
