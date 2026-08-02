# Experiments — what has been tried on the build loop, and what it measured

A record of shapes that were tried against real builds and the numbers they produced. An entry
belongs here once it has been RUN; a shape that has only been argued about belongs in `tasks/`.

The rule this file exists to serve: **measure before changing the loop.** A change that cannot point
at a row here has not earned its place.

---

## The style anchor (2026-08-01, prod, qwen3.6_27b via ninfer, n_ctx 131072)

One line added to build.txt — *"A game's art shares one visual style. Pick the style before the
first generate_media call and name it in every prompt"* — after two screenshots showed the failure
it aims at: a flat-shaded procedural village hosting a photoreal TRELLIS mesh and a photoreal
portrait billboard. The hypothesis: the incoherence is per-asset style drift, not asset-ness, and
the style named in the image prompt is also what the mesh chain lifts from.

Ran as a three-game battery on prod (the mesh scale normalization and the binary/vendor read
guards landed the same day and are in every arm).

### What it measured
**Style naming: 15/15.** Snail race asked for 11 assets, every prompt carrying "cartoon (game
sprite) style"; Island Village asked for 4, every prompt carrying "warm sunset lighting / earthy
tones / minimalist". Owner judgment on the snail race: visually consistent in play.

**The 2D/3D split is WIRING, not asking and not style.** Snail race referenced 11 of 11. Island
Village referenced 0 of 4 — it asked for ground/wall/floor tiles and a sky, then shipped
flat-shaded materials anyway, past the done-nudge audit naming all four. In 2D, using art is
`drawImage`; in three.js it is TextureLoader + material plumbing the model never crossed. Ladder
position: a snippet-tier candidate, unrun.

**The model declined meshes on its own.** Island Village under the style line built its whole
village and cast procedurally — zero mesh asks, zero portrait billboards — independently arriving
at the owner's own judgment of the earlier screenshots ("way more coherent with the built 3D
models"). One game; not yet a law.

**Cursed Curation (the generate_media showcase, "dozens of distinct objects" in the request):
~15 distinct items, owner-judged best of the three.** The demand-side pull worked where
prompt-side pushes had rationed at 5.

### The ruling
The line stays — it moved style coherence at one line of per-turn cost and nothing regressed.
Coverage in 2D looks solved when the REQUEST wants art; 3D texture wiring is the open half, and
mesh usage in 3D is parked until a build shows a style-anchored mesh joining a scene it belongs in.

---

## The depth arms (2026-07-30, qwen3.6_27b, n_ctx 131072)

### The question
A game the platform builds is usually playable and shallow. The 2026-07-29 card RPG took 439 human
fix notes and still had no art, no resources and an unpolished UI — the request did not produce a
game that stood on its own. Five shapes were run against the same requests to see which, if any,
changes that.

| arm | what it changes |
|---|---|
| A | nothing — the naked driver, as baseline |
| B | the first `done` is answered with a nudge, not accepted |
| C | write `DESIGN.md` before any code, re-read it before `done` |
| D | write `PLAN.md`, then one build per numbered step |
| E | write `PLAN.md` as phases, then one build per phase |

Two rounds of two games. Round 1: an open-world card RPG (the 439-note request, verbatim) and a
colony survival sim. Round 2: a story RPG whose world must remember what the player did, and a
roguelike whose items must combine. Round 2 also ran the error gate and the batched-call line.

### What the counters said

| arm | game | turns | GPU s | files | art asks |
|---|---|---|---|---|---|
| A | narrative | 77 | 658 | 12 | 0 |
| A | roguelike | 38 | 300 | 7 | 0 |
| B | narrative | 70 | 456 | 13 | 0 |
| B | roguelike | 79 | 439 | 8 | 0 |
| C | narrative | 37 | 683 | 28 | 20 |
| C | roguelike | 55 | 359 | 18 | 8 |
| E | narrative | 367 | 2524 | 37 | 28 |
| E | roguelike | 506 | 2740 | 11 | 0 |

Round 1 (cardrpg / colony) ran the same five arms; D was run on the card RPG only, at 1222 turns and
7260 GPU seconds.

### What playing them said
**The counters are anti-correlated with the result.** Arm C won turns, files and art asks in BOTH
rounds and played worst in both: a colony that announced "Colony lost" before any input, a card game
whose combat and shop buttons did nothing, a story RPG that threw while being played, a roguelike
full of undefined values. Arms A and B looked unremarkable on every counter and produced the games
worth playing.

Ranked by the human who played them: B/colony best overall, then B/narrative, then A/roguelike.
A and B were close enough on both rounds that nothing structural separates them.

### What came out of it, and what did not
**Adopted:** the done-nudge (arm B). Best or joint-best on three of four games, and it costs one
turn. Also the batched-call line in `build.txt`, and the turn cap at 120 — 80 capped mid-phase on
large games.

**Not adopted:** C, D and E. C is covered above. D and E buy depth by splitting authoring across
builds, and both shipped load-blocking defects that single-build arms did not: two files each
declaring the same top-level `const`, in separate transcripts, with no way for either to see the
other. A, B and C are one build each and collided zero times.

**Splitting does not prevent interface MISUSE, only redeclaration.** E's `combat_ui.js` and
`combat_engine.js` were written in the same phase and the same transcript, and `combat_ui` still
called `engine.playerPlayCard` on an object exporting `CombatEngine.playerPlayCard`. One transcript
prevents two files declaring the same name; nothing here prevents calling a name that was never
defined.

**A plan phase that says "testing" costs a full turn cap.** Three E runs, two of them capped at 120
without ever calling `done`, both on a phase whose text asked the model to verify behaviour — which
it has no way to observe, since nothing in the harness loads the page. The phase that said "polish"
without "testing" converged at 75.

---

## The error gate (2026-07-30)

### The question
Three of the nine round-1 games did not load at all, and nothing in the pipeline saw it. Every arm
is blind in the same place: nothing ever opens the page.

### The shape
Load the staged game in a headless browser after the build finalizes, collect uncaught exceptions
and unhandled rejections, and re-enter the fix machine with what threw. An uncaught exception is one
of the few signals that satisfies the BROKEN-not-bad guardrail — `this._doIdle is not a function` can
only be met by defining it.

404s are excluded deliberately: art lands after the code that draws it, and a missing sprite is a
fallback shape, not a broken game.

### What it measured
Every cell that threw went to zero, unattended — nine of nine across both rounds, including a game
with seven load-blocking defects. Three things had to be true, each measured separately:

**One error per fix build.** D/cardrpg: seven errors in one note closed NONE in two rounds; the same
seven sent singly closed all seven in five rounds.

**Every error needs an address.** A SyntaxError reaches the browser with an EMPTY stack, so node
supplies file and line, and a cross-file declaration scan supplies the pair for a redeclaration that
neither tool can locate alone.

**A parser reports one error per file, so a repeated defect grinds.** A/narrative had 67 broken
object literals plus 10 unquoted keys across four files. Located notes alone moved it 3 → 3 over six
rounds, fixing one INSTANCE each. Adding one sentence — a file stops parsing at its first error, so
fix every occurrence rather than the line named — moved it 3 → 0 in three rounds, clearing 13 then
52 then 10 instances. Convergence went from per-instance to per-file.

### Its limit, and the ruling
**The gate does not simulate play.** It loads the page and drives it only far enough to get past a
title screen. C/narrative threw repeatedly while being played and the gate called it clean; that
class belongs to a play-time listener where a person supplies the interaction. Teaching the probe to
click more would be a "must DO X" gate wearing a disguise.

So the gate moved three games from *does not open* to *opens and responds*, and did nothing for a
roguelike whose floors never vary, a card game whose combat button is inert, or a colony that loses
instantly. Those are still human findings.

---

## Recovering a tool call the model wrote as text (2026-07-30)

`_xml_calls` dropped any recovered call that carried no arguments, so `<function=list_files>` — the
only argument-less tool, and the first one a fix build reaches for — was reported to the model as
"no tool call landed", followed by a nudge telling it to write `index.html` over a game that already
existed. Three fix builds in a row died at step 4 before this was found. `_usable` already rejects a
name that is not an offered tool, so the guard bought nothing.
