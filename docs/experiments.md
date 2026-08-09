# Experiments — what has been tried on the build loop, and what it measured

A record of shapes that were tried against real builds and the numbers they produced. An entry
belongs here once it has been RUN; a shape that has only been argued about stays out of the repo.

The rule this file exists to serve: **measure before changing the loop.** A change that cannot point
at a row here has not earned its place.

---

## A grade as a fix note (2026-08-08, local 5090, one game)

### The question
The rubric (`docs/game_rubric.md`) produces a written judgement of a played game. The obvious next
thought is to hand it straight back to the build. Does that produce a better game?

### The run
The first graded build — a curse-shop game, graded **1 first impression / 1 considered**. Its
complaints were transcribed into `run --fix` in the owner's own words: the dispel step unreachable,
the day never ending, buttons overlapping, some text unreadable.

The fix succeeded in 26 steps / 84 s and found the true cause, which no one had diagnosed: the
Examine and Cast Spell buttons were placed at fixed rows of a question grid whose height depended on
the question count, so Cast Spell sat underneath the questions. One layout bug had produced two
apparently separate complaints, and the dispel system and end-of-day tally — both graded `absent` —
turned out to exist and be unreachable.

### What it measured
`core_loop` 1 → 2. The `dispel the curse` claim moved `absent` → `delivered`. **`legibility` went
DOWN** — reaching the spell step exposed that you must cast with no information about which spell or
ingredient applies. Every other dimension unchanged. The owner declined to re-grade it: not a better
game.

### The ruling
**A grade says what is wrong, not what to do.** It carries symptoms, so the fix it produces clears
the reported blocker and stops there — behind this one was a design problem the grade had no way to
name.

Grades are an AGGREGATE instrument aimed at the LOOP: collect them across a battery, find what
recurs, and turn that into a prompt-time change measured against a control arm. A dimension low
across many games is a work item; low on one game is noise.

### Alongside: an LLM asked to fill in the same rubric
Both graded games, same rubric, source and manifest only, no ability to run them. It answered
**6/5 both times** against the human's **1/1 both times**.

Game 1 (a shop/deduction game, blocked by a layout bug): close on what is visible in code —
`art_integration` 6 vs 6, `legibility` 6 vs 7, `moment_to_moment` 5 vs 4 — and inverted on
everything requiring the game to run: `depth` **7 vs 1** ("30+ unique items… 20-30 minutes" — real,
in `items.js`, unreachable), `visual_coherence` 7 vs 1, `character` **8 vs n/a** (it praised writing
the player never reaches).

Game 2 (a 3D village, abandoned as unplayable because movement and camera turn against each other):
`moment_to_moment` **6 vs 1**, scored as *"movement is smooth and standard FPS controls work well"* —
an assertion about the one thing that made the game unplayable. `visual_coherence` 8 vs 2,
`art_integration` 7 vs 1, `character` 7 vs 1. Its stated biggest gap was the absence of a core loop:
articulate, plausible, and not the reason the game failed.

Both games passed the error gate — they load and throw nothing — and passed this review, and are 1s
to the person playing them. Both failures were embodied rather than textual: an unreachable state,
and a control scheme that is wrong only once a body is attached to it.

**Unresolved, and it undercuts the above:** 6/5 twice is suspiciously constant. Whether the model is
reading these games or emitting a default for any competent-looking codebase is not distinguishable
from two samples that failed. The test is to hand it a game the human scores WELL; if it still says
6/5, it is measuring nothing.

---

## Sizing the canvas to the window (2026-08-08, local 5090, qwen3.6_27b via ninfer)

### The question
"Fixed canvas with no window scaling" has sat on the roadmap's recurring-defect ledger since the
2026-07-27 grid without a prompt line, because `build.txt` is pinned against additions. A real
build the same day also shipped a map drawn with the camera offset applied to BOTH the source and
destination rectangles, so the ground rendered as a strip while the sprites floated correctly —
"add a camera line too" was the obvious response. Do either of those lines earn a place?

### The shape
Two requests neither line was written for (a top-down shepherd, a side-view platformer), built
twice each: once with two candidate lines added, once with the prompt untouched. Same model, same
box, same session. Scored mechanically on three checks — canvas sized to the window, camera clamped
to the world, and the ground drawn through the same offset as the sprites — with the checks written
before the control arm finished and deliberately generous to the control.

### What it measured
| check | with the lines | without |
|---|---|---|
| canvas sized to the window | 2/2 | **0/2** |
| camera clamped to the world | 2/2 | 2/2 |
| ground shares the sprite offset | 2/2 | 2/2 |

Screenshots at 2558×1319 confirm the first row: both control games render as a small fixed box in a
field of background colour, both treatment games fill the screen.

### The ruling
**The canvas line ships; the camera line does not.** The camera defect that motivated the whole
exercise did not reproduce in either control game — one broken game out of three top-down games
built that day is not a recurring defect, and a line that costs every turn of every build must
point at a number. The scorer was checked against the original broken call to prove it can see the
bug, so the 2/2 is a real negative and not a blind check.

The general law, and the reason the surviving line is worth its cost: **a defect that only appears
at window sizes larger than the authored one is invisible to every check that runs at the authored
size.** The first headless pass over the broken game, driven at 1100×760, reported it clean.

---

## The worldgen cell recipe, generalized (2026-08-08, local 5090, 6 biomes)

### The question
The map recipe validated on one coastal-town cell (blockout → Qwen-2512 subjects → TRELLIS
sprites at one shared camera → DreamShaper terrain img2img → composite → Qwen-Edit-2511
embedding) — does it survive biomes it wasn't tuned on?

### The run
Six requests — river village, desert bazaar, volcanic mine, snow monastery, forest camp,
farm hamlet — through the full pipeline unattended. Lab code outside the service tree;
~35 min wall-clock total, 26 new object types rendered (subject ~20s, TRELLIS lift + 45°
orthographic sprite ~30s each), 6 embedding passes at 134–172s.

### What it measured
**6/6 end to end, zero pipeline failures.** Layouts solved (no plan fallbacks), terrain
differentiated per biome (lava river, snow field, sand, grass), objects kept identity
through the embedding pass, contact shadows landed. The pipeline generalizes; the failures
are all content:
- **Type resolution is biome-blind.** Every "house/hut/quarters" in every biome resolved
  to the one cached red-roof cottage — desert, volcano and snow alike. 26 of 32 types fell
  through the keyword table to raw flavor names ("The Silent Peak Monastery"). Resolution
  needs a style/biome dimension and something smarter than keywords.
- **Uniform scale lies.** Telegraph-pole street lamps, a barn-sized well — one
  VISUAL_SCALE for every kind.
- **Forest is terrain when it should be trees.** The forest band painted as flat dark
  green; a forest cell needs a tree-scatter rule, not a label.
- **The style prompt can lose to the bare-ground paint.** river_village asked for lush
  green and kept its mud at denoise 0.55.
- **Sparse density** — big empty stretches in most cells.

### The ruling
Recipe is integration-ready; every failure maps to a known work item (store type
resolution, per-kind scale table, forest scatter, terrain palette, density). Lab code and renders live outside the repo — results
recorded here, service code arrives only with the integration itself.

## The NSFW render classifier's threshold (2026-08-03, local 5090, 290 renders)

### The question
Where does the refuse threshold go for the post-gen image verdict (`assets.render_verdict`,
scores from Marqo/nsfw-image-detection-384 in `worker/safety_vision.py`)?

### The run
Every rendered asset from the local run corpus — 290 webps across 30 runs, all of it innocent
game art — through the worker's own classify path. Two preprocessing variants (alpha composited
onto black, the RGB-convert production default for matted sprites; and onto white), plus a
divergence check of the worker's transform against timm's official pipeline for this model.

### What it measured
- A hand-rolled resize/normalize sat up to **0.146** off the official pipeline's probabilities;
  `timm.data.create_transform` fed from the exported `data_config` matches to **5e-05**. The
  worker uses the latter.
- With exact preprocessing, the model still overcalls stylized game art: **28/290 (black) and
  31/290 (white) innocent renders scored ≥ 0.5** — a cow at 0.946, a cabbage at 0.923, a rock
  sprite at 0.933. Flux-schnell game sprites are out-of-distribution for a classifier trained on
  photographic/anime NSFW.
- The innocent ceiling across the corpus: **0.954**.

### The ruling
`NSFW_REFUSE_THRESHOLD = 0.98` — zero false positives on the corpus, while the pre-gen prompt
screen keeps owning steered content and the classifier owns only the unmistakable case. Verdicts
are stored on the manifest per render, so a future threshold (or a better-calibrated model for
stylized art) re-policies old assets without re-rendering anything.

---

## The audio candidates (2026-08-01, local 5090, 58 generations)

### The question
Games ship silent — "silent games (all four arcade + the deck-builder)" has sat on the open ledger
since the 2026-07-27 grid. Before spending a prompt line or a queue on it, which open-weight audio
models can we actually run, under a license we can use without thinking about it, fast enough that
art does not starve gameplay for GPU?

Two candidates were installed locally and run against a game-shaped battery. Licenses were read off
the HuggingFace repo `card_data`, not off blog posts — the trap here is models whose CODE is
permissive and whose WEIGHTS are not.

| candidate | license | what it is |
|---|---|---|
| ACE-Step 1.5 | **MIT** (code + weights) | text-to-music, native ComfyUI support since 1.5 |
| MOSS-SoundEffect v2.0 | **Apache 2.0** (code + weights) | text-to-SFX, DiT + flow matching, 48 kHz |

Ruled out on license, all of them weights-side: Stable Audio 3.0 / Small-SFX (Stability Community
License), MusicGen / AudioGen / AudioCraft (weights CC-BY-NC 4.0 against MIT code), AudioLDM 2 and
Tango 2 (NC). Permissive but wrong shape: HeartMuLa-oss-3B (Apache 2.0 both halves, but RTF ≈ 1.0 —
~90× slower than ACE-Step turbo — and lyrics-oriented), YuE (Apache 2.0, slow).

### Method
The two halves ran differently, and **that difference is itself the finding**. ACE-Step went through
ComfyUI's own native nodes over `POST /prompt` → poll `/history` → `/view`, the identical path the
production image worker already uses, with sampler settings lifted from ComfyUI's shipped templates
rather than guessed. MOSS has no native node: it ran in-process from its own pipeline in an isolated
venv, which is the TRELLIS shape — a server of its own.

ACE-Step: 5 game-music briefs (chiptune boss, pastoral village, menu loop, space combat, playful
puzzle) × 6 configs, plus a duration sweep — 34 runs. MOSS: 8 game sounds × 25/50/100 steps — 24
runs. Every cell succeeded. All 58 outputs verified byte-distinct.

### What it measured

**ACE-Step 1.5, 60 s of music, warm:**

| config | wall | peak VRAM |
|---|---|---|
| turbo-1.7b, **planner off** | **1.8 s** | 12.7 GB |
| turbo-1.7b | 4.3 s | 20.4 GB |
| turbo-4b | 5.4 s | 20.4 GB |
| xl_turbo-4b | 5.6 s | 29.3 GB |
| base-4b (50 steps) | 8.6 s | 20.4 GB |
| xl_sft-4b (50 steps) | 11.5 s | 25.4 GB |

**Duration is linear, with no coherence cliff:** turbo at 30/60/120 s → 2.3 / 4.5 / 8.9 s;
xl_turbo → 3.0 / 5.8 / 11.7 s. **Model load is negligible** — cold 4.5 s against warm 4.3 s, so a
scale-to-zero audio pod pays pod boot and almost nothing else, unlike mesh's ~116 s.

**The `generate_audio_codes` toggle is a 2.5× lever** (4.3 s → 1.8 s, and 20.4 GB → 12.7 GB). It is
the planner LLM. Whether the quality is worth 2.5× is an ear question, and both arms are in the grid
for exactly that reason.

**MOSS-SoundEffect v2.0:** 2.1–3.1 s at 25 steps, 4.0 s at 50, 8.0 s at 100 — flat 19.6 GB
regardless of step count, 48 kHz, pipeline load 7.1 s.

**Three environment findings that will bite whoever integrates this:**
- **xformers has no kernel for ACE-Step's attention on Blackwell** (`NotImplementedError` on
  `memory_efficient_attention_forward`, bf16 `(1,375,16,128)`). ComfyUI needs
  `--use-pytorch-cross-attention`. The image worker's instance would need it too.
- **The box has no ffmpeg.** MOSS's `save_audio` routes through torchaudio → torchcodec → ffmpeg and
  failed on all 24 clips while the diffusion itself was fine; writing the waveform with `soundfile`
  fixed it. ACE-Step is immune — ComfyUI writes MP3 itself, and also offers Opus.
- **xl_turbo peaks at 29.3 GB of 32.6.** It cannot co-reside with anything. Only the non-XL turbo
  variants (12.7–20.4 GB) have room to share a card.

### Measurement hygiene, recorded because it cost a re-run
The first ACE pass was contaminated twice over: **ComfyUI caches node outputs**, so a byte-identical
graph returns in 0.25 s without generating, and VRAM carried between configs until one cell hit
32.0 GB of 32.6 and measured thrash (8.4 s) rather than the model (5.5 s). The numbers above come
from a clean re-run with an unload between configs. A grid driven through ComfyUI must free between
arms or it measures its own cache.

### Ladder position — unrun, NOT implemented
The integration shape was designed and is deliberately not built. For music it is a new queue and
worker but **no new server**, since ComfyUI already serves it: a `t2music.json` workflow, a
`build_music_payload`, `save_audio` in `asset_chain.OPERATIONS`, one `MEDIA_SCHEMA` enum value, an
`EXT = {"image":"png","mesh":"glb","music":"ogg"}` table replacing the `"glb" if mesh else "png"`
ternary at its four sites, and `comfy_image` widened to collect any ComfyUI output type rather than
only `["images"]`. A separate queue rather than riding `image` because prod is already
one-queue-per-card and `start_from_manifest` already records that a one-GPU box holds one model at a
time. **The number that would overturn that is unmeasured**: whether flux1-schnell-fp8 and ACE-Step
turbo co-reside on 32 GB. The attempt ran inside ninfer's headroom and measured thrash.

MOSS is further down the ladder than music, not beside it. It needs the full TRELLIS-shaped lift —
its own server, handler kind, queue, worker and pod class, at 19.6 GB resident — while a WebAudio
oscillator costs zero infrastructure and beats a diffusion model at a 0.2 s arcade blip. The open
ledger item is not "we cannot generate SFX", it is that games come out silent; if a prompt line does
not get a five-line beep out of the model, a MOSS pod renders nothing either. MOSS earns its pod for
foley and ambience, after music ships and not alongside it.

### The ruling
Both models are good and both are usable. Nothing is being implemented yet — this entry exists so
that when it is, it starts from numbers rather than from the survey again. Artifacts live outside
the repo in `/home/nick/audio-grid` (harnesses, `results_clean.json`, `moss_results.json`, and a
results page with every clip playable); weights in `Documents/models/Audio`, symlinked into
ComfyUI's own `models/` and deliberately not into the production `models/Image` tree.

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
