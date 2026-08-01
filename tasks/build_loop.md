# Build Loop — the codegen pipeline itself

Verified: 2026-07-31

Rewritten 2026-07-31. The previous file planned against the kit era — `module.py`'s gate list,
`runtime/kit_catalog.md`, `src/maestro/codegen/audit.py`, `docs/codegen_rebuild_plan.md`, the
`spec_draft` prompt. None of those exist. B1 (retire the dead plan), B3 (kit widening) and B4 (the
audit's ceiling) went with them; B2, B5 and B6 are carried over as written.

## Why
The loop is the north-star work and needs a live plan of its own: a driver, six tools, a transcript,
and one gate. This file owns how that loop behaves — step economics, what a fix round is fed, what
survives a build. Whether the RESULT is any good is `quality_backlog.md`.

## Boundary with the other quality files (do not duplicate)
- **This file** — the LOOP: the gate, fix shapes, step economics, what a run keeps.
- **`quality_backlog.md`** — JUDGING output quality: the battery, the grading bar, the play-critic.
- **generation-quality group** (`world_first`, `game_style`, `asset_quality`, `game_media`) — what
  goes INTO a game: content, art, audio.

## Background (VERIFIED 2026-07-31 — points at code)
- Driver: `src/maestro/codegen/build_chain.py` (default `max_steps=200`), cursor in
  `build_state.py`, the turn machine in `build_steps.py`, the six tools in `tools.py`.
- The only gate is `index.html` exists (`build_chain._playable`).
- The conversation a build actually sent is on disk: `runs/<id>/turns.jsonl` (`turn_log.py`), and
  the per-build event log is in `events` (each row now carries its `build_id`).
- Attribution: NO TOOL. B2 below cannot run until one exists.

## Guardrails
- **A gate may only detect BROKEN, never "bad"** (`CLAUDE.md`). Proposing a "the game must DO X"
  check is the one reliably wrong move here.
- **Measure before changing the loop.** Attribution over real builds, not intuition.
- **A prompt line before a primitive** — `build.txt` is the first answer, a primitive the last
  (`CLAUDE.md`, "Adding a capability").
- Normalize model output to its evident intent; never add caps or rules that DROP it.

## B2 — Step-economics baseline
No current measurement of where a build's 200 steps go. Every loop change should be judged against
it, the way the probe deletion was.
- [ ] Write the attribution tool: per-build steps grouped by what the turn did (write, edit, read,
      failed call, repeat of a failed call), wasted-step rate, elapsed, and a diff of two runs of
      the same request. `turns.jsonl` holds everything it needs.
      → done when: this section holds a dated table covering ≥10 builds.
- [ ] Re-run after any loop change and diff, per the guardrail.
      → done when: standing.

## B5 — Snapshot the game folder at built states
A fix round re-edits code that already worked, so regressions are structurally likely, and nothing
preserves the last-green artifact.
- [ ] Snapshot `game/` at each finalize that produced a playable game, and before a fix build,
      retrievable per run.
      → done when: a run dir holds ≥2 recoverable built states and a test asserts one restores.

## B6 — The error gate: load the page, feed back what THREW
Measured 2026-07-30 across the 9-cell depth grid: 3 of 9 games did not load at all, and no check in
the pipeline saw it — every arm ships without anything ever opening the page. An uncaught exception
is the one signal that satisfies the BROKEN-not-bad guardrail: `this._doIdle is not a function` can
only be met by defining it.

Trialled on the grid with throwaway scripts that were NOT kept (numbers in `docs/experiments.md`;
the check has to be written into the harness properly, not lifted). It closed every load-blocking
error on all 9 cells. Two things had to be true, and both were measured:
- **One error per fix build.** D/cardrpg: 7 errors in one note closed 0 in two rounds; the same 7
  sent singly closed all 7 in five.
- **Every error carries an address.** The browser reports a SyntaxError with an EMPTY stack, so node
  supplies file/line and a cross-file declaration scan supplies the pair for a redeclaration.

- [ ] Move it into the harness: after a build finalizes and stages, load the page headless, collect
      uncaught exceptions + unhandled rejections (never 404s — art lands after the code that draws
      it), and re-enter `kickoff(kind="fix")` with one located error per round.
      → done when: a build that throws on load ends `built` only after the gate runs, and a test
      asserts a game throwing once is fixed without a human.
- [ ] Decide the playwright/chromium dependency for the prod droplet (CPU-only and lean today).
      → done when: either it is installed there with its footprint recorded, or the gate is scoped
      to local/eval only and that is written down.
- [ ] Play-time capture: the same listener inside the served page while a person plays, offering
      "we detected a game-breaking error, fix it?". BLOCKED on a channel — the `/play` cookie is
      scoped so it never reaches `/api` (`auth/deps.py`, CSRF-immunity), and the SPA opens the game
      with `rel="noreferrer"`, severing `window.opener`. An iframe in the play page is acceptable
      (Nick, 2026-07-30) and is the option that needs no new auth surface.
      → done when: a person playing a staged game can turn a thrown error into a fix build.

**RULED (Nick, 2026-07-30): the gate does NOT simulate play.** It loads the page and drives it only
far enough to get past a title screen. Errors that need real interaction to surface — C/narrative
threw `TypeError: n is null` repeatedly while being played and the gate called it clean — belong to
the play-time listener, where a person supplies the interaction. Do not answer a missed error by
teaching it to click more: a gate that guesses at play is a "must DO X" gate wearing a disguise,
and the guardrail above already rules those out.

### B6 repeated defects — SOLVED by one sentence in the note (measured 2026-07-30)
A parser reports the FIRST error in a file and stops, so a mistake made N times reads as one error:
fixing it exposes the next. A/narrative had 67 `X: "...";` object literals plus 10 unquoted
hyphenated keys, and 6 gate rounds moved 3 → 3, each correctly fixing ONE instance.

The note now says a file stops parsing at its first error, so what is reported may be one instance
of the same mistake repeated, and to fix every occurrence rather than the line named. Same game,
same errors, same cadence:

| note | rounds | result |
|---|---|---|
| located only | 6 | 3 → 3 (one instance per round) |
| + fix-every-occurrence | 3 | 3 → **0** (13 instances in one round, then 52, then 10) |

Convergence is now one round per FILE, not per instance, which is the floor a per-file parser
allows. The sentence states a general law about parsers and names no defect.

Two candidate fixes remain UNADOPTED, and the result above weakens both — the grind was the note,
not the message quality. A/B before any adoption:
- [ ] **babel expected-token messages.** `@babel/parser` reports `Unexpected token, expected ","` at
      file:line:col; `node --check` reports only `Unexpected token ';'`. The expected token is the
      fix instruction, derived rather than inferred. Measured caveat: a fully located note (file,
      line, column, source line) did NOT close A/narrative — the grind was the 77 instances, not the
      message, so better messages may buy nothing. `@babel/parser` currently only exists in
      `frontend/node_modules`.
      → done when: same cell, node-note vs babel-note, rounds-to-clean compared and recorded here.
- [ ] **Deterministic repair loop.** Where babel reports exactly `expected "X"` at a position,
      substitute X, reparse, repeat until clean — closes the class with no model turn.
      `expected ","` does NOT distinguish REPLACE the offending token from INSERT one before it, and
      getting that backwards corrupts a source file SILENTLY, which is the failure class that costs
      most. Guards are mandatory: write to a copy, reparse the copy, replace the original only when
      it parses clean AND the byte diff is confined to the reported positions.
      → done when: it runs over every grid game that threw a syntax error, every change is diffed by
      hand once, and behaviour before/after is compared — not merely that it parses.

## Parked
- Play-critic / vision-in-loop — `quality_backlog.md`; the honest end of the verification gap,
  deliberately last.
- Reasoning-only turns (~3-7%, clustered on big authoring prompts) — ruled: a failed turn is a
  failed turn, retry. No salvage-from-reasoning.
- Spec-richness measurement — it depended on the spec-vs-code audit and the `spec_draft` prompt,
  and a build's input is now the person's own words. Re-derive from scratch if the question returns.
