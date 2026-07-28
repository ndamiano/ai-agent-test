# The art arm: `generate_media` vs. a prompt line

CLAUDE.md's ladder is prompt line → snippet → primitive, and `generate_media` is a primitive. This
runs it against the prompt line it stands in for, on the same games and the same models.

**Arm A (the tool).** Nothing to change.

**Arm B (the prompt line).** Two edits, both revertable with `git checkout`:

1. `src/maestro/codegen/build_steps.py` — drop `MEDIA_SCHEMA` from `SCHEMAS`.
2. `src/maestro/codegen/prompts/build.txt` — replace the `generate_media` rule with the
   `manifest.txt` line beside this README, and say five tools, not six.

Arm B needs no asset lane: the model's `assets.json` is the same file `request_media` writes, so
`python -m maestro.codegen.run --assets <run_id>` renders it after the build. The battery question
is what the model DECLARES and whether its code uses those paths — the render is scored separately.

## Running it

    cd eval
    python run_battery.py run --harness maestro --model <model> --out grid_arm_a
    # apply the two edits, restart the backend + worker (settings are read once per process)
    python run_battery.py run --harness maestro --model <model> --out grid_arm_b

Two models, four games each, per CLAUDE.md's "validate on the battery" rule.

## What to score, per game

| | |
|---|---|
| **asked** | did it ask for art at all (a `generate_media` call / an `assets.json` entry)? |
| **path verbatim** | does the game's code reference the exact path it was given / declared? |
| **fallback** | does it draw a shape where the art goes, so the game reads right before the file lands? |
| **count** | how many assets — one per entity, or 40 up front? |
| **landed** | is the art actually visible in `/play` after the renders finish? |
| **drift** | did a 2D request go 3D because `three.module.js` sits in the seeded folder? |

`drift` is the one that indicts the seed rather than the tool: the vendored renderer is the only
thing pre-placed in the game folder, and everything pre-placed steers the first `list_files`.
