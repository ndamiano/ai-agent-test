---
name: iterate-dialogue
description: The babysat build-iterate loop for improving Maestro's generated game quality — launch, monitor, grade, root-cause, fix, rebuild. Use when working on generation quality.
---

# The dialogue iteration loop

Goal: generated output as good as `docs/examples/one_last_lan.md` (the gold standard).
The model is a small local one (Qwen via llama.cpp at `http://localhost:8080`) — bad output
is a prompting/plumbing bug, never "the model is too small". Builds are free; iterate fast.

## One iteration

1. **Launch** (background, absolute paths — background shells don't inherit cwd). Use
   `setsid nohup python -m maestro.run --yes "<premise>" > <scratchpad>/buildN.log 2>&1 &`
   so the build survives its wrapper shell being stopped.
   NEVER check build liveness with `pgrep -f "maestro.run"` — the pattern matches YOUR OWN
   wrapper shell's command line (this produced a false ALIVE for 2.5 hours once, and a
   self-kill twice). Check via /proc instead: iterate /proc/*/cmdline and require the
   executable to be python AND 'maestro.run' in its args. Same rule for kills.
   The gold A/B premise: "Two best friends in their 30s spend one last co-op game night —
   Halo, the hardest-difficulty run they never finished — before one of them becomes a dad.
   His wife is due any day, and his old gaming room is the nursery now."
   A build takes 8-18 min. `--yes` skips the interactive freeze gate.
2. **Monitor actively** (don't wait for the user to ask): watch the log for
   `write_scene|abandoning|Traceback|BudgetExhausted|unmet:` lines. Check the SPEC as soon
   as it prints — premise slop (grafted crime/rift/villain) means kill and fix
   `spec_write.txt` before wasting the build. Check `characters.json` as soon as it lands —
   a bad voice license poisons every scene after.
3. **Grade** with the `grade-scenes` skill (slop_scan + full read + calibrated bar).
   Artifacts live at `/home/nick/output/runs/<run_id>/` (nodes.json, characters.json,
   story.json, spec.json, story_state.json; playable at game_output/).
4. **Root-cause each defect** before touching anything. The fix hierarchy, best first:
   - CODE hygiene (parser, dedupe, sanitizer in `maestro/modules/scenes.py`) — beats prompt
     text every time it's applicable
   - the LICENSE (a card field, a spec example, a beat summary that commanded the slop)
   - the PROMPT (one line, tied to the observed failure — never speculative rules)
   Fix-over-reject: a patchable field never rejects the whole write (full regens degrade —
   scrambled speakers, prose drift). Sanitize/collapse in code instead.
5. **Every prompt line must solve a SEEN problem.** Quote the observed failure in the
   commit/comment. Delete lines that exist to sound thorough.
6. **Rebuild and diff.** Re-run the same premise before rejecting a change (seed variance
   is real). For single-module iteration use the climb harness:
   `python -m maestro.climb <src_run_id> <module_id> [--label tag]` (~8 min for scenes).

## Where things live

- Turn-loop authoring: `src/maestro/modules/scenes.py` (`scene_turn_loop`, `_parse_turn`,
  `_scene_brief`, dedupe with one-echo allowance for short lines, end sanitizer)
- Per-turn prompt: `src/maestro/prompts/scene_turn.txt`; closer: `scene_close.txt`
- Cards: `characters_write.txt`; story plan: `story_write.txt`; spec: `spec_write.txt`
- Mechanical scanner: `eval/slop_scan.py`
- Tests: `cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`
  (run after EVERY code change; add a test per non-trivial change)

## Standing rules (Nick's, non-negotiable)

- Never commit without his explicit request.
- NO line/length ceilings on scenes, ever — grade per-line work instead.
- When collaboration would beat solo (taste calls, premise choices, red-penning), ASK —
  tell him "it would really help if you could X".
- Talk terse. Lead with the verdict. Quote real lines as evidence.
- He distrusts LLM judges — prefer deterministic detectors (slop_scan) and his own read.
  Judge-territory automation only with his sign-off.

## Known-good state (2026-07-03)

Builds 1→3 on the gold premise: slop_scan counts went 33 findings → 12 → 1; build 3's
`beat_05a` and `ending_heroic` approach the bar. Persistent enemy: the significance
machine at emotional peaks (diagnosis lines, metaphor-trading) — every architecture so far
reproduces it; prompts reduce it, code detectors + rewrite_node are the planned endgame.
