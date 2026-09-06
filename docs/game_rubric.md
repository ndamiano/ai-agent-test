# Grading a generated game

A form for the owner to fill in after playing a build. It exists so that "is the output getting
better" is answered by a written record instead of a memory of how the last few felt.

One grader, on purpose. Surveys and other people's opinions are a later problem; this instrument is
built for a single person grading his own output.

## What this is not

- **Not a gate.** Nothing here blocks a build, holds it, or re-enters the fix machine. A gate may
  only detect broken; every dimension below is a judgement about *good*, which is precisely what no
  gate may act on.
- **Not an input to the model, ever.** No score, note or dimension name is fed back into a build, a
  prompt or a planner. The moment the model can see the rubric, it optimises the rubric.
- **Not a fix note.** A grade says what is WRONG, not what to DO. Measured 2026-08-08
  (`docs/experiments.md`): a 1/1 grade transcribed into a fix note produced a correct fix,
  `core_loop` 1 → 2, `legibility` DOWN, and not a better game. Write the fix note from what the game
  needs, not from the form.
- **Not a per-game payoff.** The value is in the aggregate, and the aggregate aims at the LOOP: find
  what recurs across a battery, turn it into a prompt-time change measured against a control arm. A
  dimension low across many games is a work item; low on one game is noise.
- **Not a sum.** The dimensions do not add up to either verdict and there is no total. A game can be
  genuinely good while scoring low on half the list — an open-world game has no core loop worth the
  name, a story-driven one can have flat visuals and slack moment-to-moment feel and still be the
  best thing we have made. The verdicts are separate holistic calls; the dimensions explain them,
  they do not compute them.

## What it is for

1. **Hill climbing.** A change to the loop is worth keeping if games graded after it read better
   than games graded before it. That comparison leans on the notes more than the numbers.
2. **Finding the lever.** Every dimension names what could plausibly move it. Consistently weak
   across many games is a work item; weak in one game is a story about that game.
3. **Knowing when stage 2 has cleared.** The vision's bar is "the owner plays a battery and is
   happy". That is a verdict, not a number, and it is recorded below.

---

## The order, and why it is this order

**first impression → verdict → dimensions → considered verdict → reveal the request → fidelity**

- **Two verdicts, not one.** The first is a gut call taken the moment play ends, before any
  analysis. The second is taken after working through every dimension. Both are kept. The gap
  between them is real signal: it says how much the considered look changed the call, and early on
  it will also say how much the grader is still finding his footing.
- **Dimensions before the request is revealed.** They judge the game *as it is*, not as an answer to
  a brief. A game is not more legible because nobody asked for legibility. Keeping the request out
  of view here is what makes dimension scores comparable across different requests.
- **The request comes last, and only then fidelity.** Reading it earlier tints the whole session —
  you start hunting for things instead of playing, and a system you would never have found becomes a
  system you found.

---

## Protocol

- **Play before reading anything.** No source, no transcript, no asset manifest, no build log, and
  not the request either. The game gets met the way a player meets it.
- **Time-box it.** 10 minutes of play, or until you have clearly seen everything, whichever comes
  first. Record which happened — it is itself the depth signal, and **abandoned** is a third
  outcome, not a variant of the other two: a game you stopped playing because you wanted to stop is
  the loudest thing a grade can record.
- **Every score carries a note.** A number without a sentence is unusable in three months. The note
  survives; the number sorts.
- **Grade the build you got.** Not the one a re-roll might produce. Variance is real, and the way to
  see it is more grades, not a kinder grade.
- **When comparing arms, stay blind.** Which arm produced the build is not looked up until the form
  is submitted.

---

## The form

The web form on a game's page stores one filled copy per grading (`src/grading.py`); this is the
blank.

```
run_id:
date graded:
play: time-box | saw everything | abandoned, at <n> min

--- LAYER 0 — did it survive contact -------------------------------
loads:                      y / n
takes input:                y / n
  (both must be y to grade at all — a game that never opens or never
   responds is not a game yet)

crashed during play:        y / n   · where, and what was reachable
                                      before it went
  (a crash is a fact about the build, NOT a reason to stop grading.
   Grade what you could reach. A good game with a bug is a good game
   with a bug.)

--- LAYER 1 — FIRST IMPRESSION -------------------------------------
Taken the moment play ends. Do not think about it.

  first impression:  _/10  ·

--- LAYER 2 — THE VERDICT ------------------------------------------
Q1. Would I show this to someone whose opinion I care about,
    without apologising for it first?

    no / with caveats / yes / yes and I would point at it

Q2. In one or two sentences — what IS this game, and what did it
    feel like to play?

Q3. The single biggest thing standing between this and a game
    someone would choose to play:

--- LAYER 3 — DIMENSIONS (1-10 + note each; NEVER summed) -----------
Judge the game as it is. The request is still not visible, and a
dimension is not excused by "it wasn't asked for".

Mark `n/a` when the dimension is not what this game is for, and say
why. `n/a` is not a zero and never counts against the game.

  core loop            _/10  ·
  moment-to-moment     _/10  ·
  legibility           _/10  ·
  interface            _/10  ·
  depth                _/10  ·
  stakes               _/10  ·
  visual coherence     _/10  ·
  art integration      _/10  ·
  sound                _/10  ·
  character            _/10  ·

--- LAYER 4 — CONSIDERED VERDICT -----------------------------------
Taken after the dimensions. May differ from the first impression;
if it does, say what moved it.

  considered score:  _/10  ·
  what moved it (if anything):

=== THE REQUEST IS REVEALED HERE ===================================

--- LAYER 5 — REQUEST FIDELITY -------------------------------------
Now read the request. List every concrete system, object or
behaviour it named, and tick each from what you saw in play.

Judge as the player you were, not as someone who knew to look. A
system that exists but that you would never have found is `absent` —
that is what it is worth to a player.

  <claim>              delivered / partial / absent   · note
  ...

  claims delivered: __ / __
  anything present that was never asked for:
```

---

## The dimensions

Scored 1–10 with a note. Deliberately **unanchored today** — see "Anchoring" below.

**core loop** — Is there a thing to do, does doing it lead to doing it again, and does it build?
Factorio's is the clean example: build the factory → unlock new things → build more factory. Not
every good game has one — an open-world game is deliberately shapeless and should be marked `n/a`
rather than punished for it. Low means a toy: systems present, no reason to keep going.
*Levers:* `build.txt`, staged construction — stage 1 is defined as a complete playable game of the
core system, so a weak core loop is the strongest argument that the staging was mis-planned.

**moment-to-moment** — How it feels second by second. Pressing W walks you forward at a snail's
pace. There's a long delay between firing an arrow and anything happening. The blow-up-the-enemy
button is enormously satisfying to press. That is this dimension: response, weight, timing, and
whether the game gives anything back for an input.
*Levers:* prompt lines. This is the family fixed-canvas came from, and where a control-arm
measurement is cheapest.

**legibility** — Can you tell what is happening, what you control, and what you are meant to do,
without being told? Low means it may be a fine game that nobody can find.
*Levers:* prompt lines, and whatever the game does about a first-run state.

**interface** — The craft of the UI itself: layout, controls, readability, and whether the
information you need to make a decision is on screen when you need it. Distinct from legibility,
which is about understanding the GAME — a game can be perfectly understandable and still put its
buttons on top of each other, show text that cannot be read against its background, or ask for a
choice while withholding what the choice is between.
*Levers:* prompt lines today. A `create_ui` tool is the candidate primitive if a line fails twice,
and a slew of grades is what would justify building one.

**depth** — How much game is there before you have seen all of it? **Twenty minutes is roughly the
floor** — a two-minute game is not a short game, it is a bad one, and nobody would spend real money
on it. Expect this to be low for a long time.
*Levers:* staged construction — this is the dimension that measures dilution, which is what staging
was built to fix.

**stakes** — Can things go wrong, and does it land when they do? Note that harshness is not the
scale: Halo reloads a checkpoint and loses nothing by it. Low means nothing you do can go badly, so
nothing you do matters. Expected to be one of the noisier dimensions; kept because when it is
genuinely absent, it is genuinely felt.
*Levers:* prompt lines, request concreteness.

**visual coherence** — Does it look like one thing made on purpose? Belonging matters more than
fidelity — a consistent flat palette beats beautiful renders sitting on programmer-art rectangles.
Expect low for a while.
*Levers:* the style anchor, per-kind image routing.

**art integration** — Is generated art load-bearing in the game, or decoration bolted beside code
primitives? This is the human half of the coverage question the static audit can only count: the
audit says an asset was referenced, only a person can say it was used well. Expect low for a while.
*Levers:* `generate_media`, the done-nudge art audit, the asset store.

**sound** — Is there any, does it respond to what you do, does it fit? Currently absent on nearly
every build, which is the point of tracking it. Expect low for a while.
*Levers:* a `build.txt` line first; a music queue only after a line has failed twice.

**character** — Is there anything memorable here? A voice, a joke, an idea, one moment worth
describing to someone. **Nothing built so far would score above a 2.** That is a problem, and
surfacing it is exactly why the dimension exists — a rubric with no room for this would rate a
competent, forgettable game a success.
*Levers:* mostly the request itself today. If this ever moves on a loop change, that is a large
result and belongs in `docs/experiments.md` immediately.

---

## Anchoring

The 1–10 scales have **no fixed anchors yet, on purpose**. Anchors invented before any grading
exists would describe imagined games. After 10–15 graded builds there will be real examples to pin
each scale to, and this section gets filled in with them: a named run_id at roughly 3, 5, 7 and 9
per dimension, so a later grade can be checked against an actual game rather than a remembered
standard.

Expect early grades to cluster in the middle and to be somewhat arbitrary. That is normal and it is
survivable, for three reasons: the notes carry the meaning while the numbers settle, working through
the dimensions drags the considered verdict away from the gut one, and the first-impression score is
recorded separately so the drift between them is visible rather than hidden.

Two anchors already exist, from the definitions above: **depth** has a floor at roughly 20 minutes,
and **character** has a current ceiling of 2 across everything built so far.

**The notes stay after anchoring.** A number with an anchor is still not an explanation.

---

## The grader

`/grade/<run_id>` in the SPA is this form, with the game playable at the top of the same page.
Operator-only, and ownership still applies — it is not a way to reach someone else's run.

It walks the steps in the order above and **does not go back**: a first impression you can return
and rewrite after scoring the dimensions is not a first impression. The page is handed the run's
request (it needs it for the reveal) and deliberately nothing else — not the model, not the arm,
not whether the run was staged, not the turn count or the fix rounds. What identifies the arm never
reaches the browser.

Grades are written to `<data_dir>/grades/<run_id>__<timestamp>.json`, one file per grading. Grading
a run again after a fix build keeps both files.

## Recording

Filled forms are control-plane state under `<data_dir>/grades/`, never repo content. When a set
of grades supports a conclusion about a change to the loop, the conclusion and its
numbers go to `docs/experiments.md`, the same rule as every other measurement. This file is the
instrument; `experiments.md` is the record.
