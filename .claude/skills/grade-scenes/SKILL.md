---
name: grade-scenes
description: Grade a generated run's dialogue against the One Last LAN gold standard — read order, calibrated pass/fail examples, and the slop taxonomy. Use after any build to judge scene quality.
---

# Grading generated scenes

The bar is `docs/examples/one_last_lan.md` (read its 18 WRITING RULES first, every time).
Nick's grade for that script is B- — "shippable, not I'd-ship-as-mine". Generated output at
that level is a win. Do not grade on "sounds writerly" — grand metaphor IS the default LLM
register and reads as slop unless the speaker, setting, and moment earn it.

## Procedure

1. Run the mechanical scan first: `python -m eval.slop_scan <run_dir>` (venv). It finds
   review targets (run-ons, name glue, diagnosis lines, theme closers, spec-vocab leaks,
   meta menu choices, circling openers, timeline mentions). Zero findings ≠ good scenes —
   it only means no *mechanical* defects.
2. Dump every scene: read `<run_dir>/nodes.json` in `node_ids` order, print
   `speaker: text` per line. Read ALL of it — slop concentrates at scene CLOSES and at
   emotional peaks, which a skim misses.
3. Grade each line by these questions, in order — and the FIRST one is the whole game:
   - THE WHY-CHAIN / NONSENSE TEST, before anything else: does the sentence survive
     "why does that follow?" — does it answer something that actually happened, do its
     objects belong in this room, does the causality parse? Fluent nonsense is the #1
     failure and the easiest to wave through. Calibration scars (2026-07-03, Nick's
     correction — all of these were graded BEST-OF before he called it):
       "You're holding it. Time isn't coming back until you let go."  (means nothing)
       "I don't want to tape you to the wall if you slice through the laminate again."
       "The stars aren't going anywhere... bracing for the crash before the car even—"
         (stars/car/crash free-association in a Halo session)
       "I didn't miss. The controller slipped."  (nothing had been missed — answers an
         event that never happened)
     A line can pass every register/structure rule and still be alien. If you wouldn't
     believe a human said it unprompted, it fails, whatever else it does right.
   - Does it react to the previous line's actual referent (same object, same claim)?
   - Would this person say it, in these words, out loud, in one breath?
   - Does the detail exist because the SITUATION produces it, or because the author
     needed it? (the operable slop test — applies at line, scene, and premise scale)
   - Does meaning stay under the line? A line that says what the scene means is a defect
     even when fluent.
4. Check the closes: the last lines of every scene. The known attractor is closing by
   SAYING the theme. A good close is an action or a short line.
5. Check timeline arithmetic by hand (slop_scan lists year/duration mentions): a 2004
   game + "in college" + "mid-30s now" must line up.
6. Check choices: menu text must be words the player's character would say or do.
   A choice must pay off later — a branch that only changes the corridor is not a choice.

## Calibrated examples (real generated output, graded)

PASS — earned, in-voice, object-grounded:
- "The red one's battery is dead. Give me the blue box."   (transactional, physical)
- "We should probably unplug it. The heat is bad for the capacitors."  (deflects a heavy
  moment into logistics — the gold Marc move; the weight stays under the line)
- "I'm still here though." / "Yeah. You're still here."  (deadpan echo carrying the payoff)
- "I think I'll save this for the nursery. Just in case."  (object stays a tool, rule 16)
- NARR: "The screw falls onto the carpet with a soft, dead thud. He doesn't pick it up."

FAIL — the significance machine (each was really generated):
- "You think if you control every variable, you won't mess this up... you're scared that
  once he's here, there's no room left for us."  (diagnosis — explains the other's
  psychology to them; the single most persistent attractor)
- "It wasn't just tactics. It was trust. I miss that dynamic, Sam."  (theme stated aloud
  at scene close; nobody says "dynamic" about a friend)
- "You're not packing yourself away. You're just changing levels... you don't have to
  carry the whole inventory alone."  (metaphor-trading: hobby-vocabulary-as-emotional-
  shorthand, licensed by a voice spec — an extended metaphor at every emotional beat)
- "Integration complete. Variance is zero."  (spec vocabulary leaked into dialogue)
- "The cardboard is warping; we need to move this to the drier room."  (inspection-report
  register — a person says "boxes" and "the damp gets to them")

## Known failure laws (hard-won; assume they still hold)

- Any quoted example in a prompt gets copied nearly verbatim — even FAILS-labeled ones.
  Negative examples must be described, never quoted.
- A voice/tic spec is a license the dialogue MAXIMIZES. A tic bounded by a mood
  ("when nervous") fires every line when the premise keeps the mood true all night;
  bounds must be trigger moments. "X-vocabulary as emotional shorthand" = metaphor-trading.
- Scene LENGTH is never a defect signal. Grade per-line work (does this exchange reveal,
  escalate, or shift a position?), never line count. NO line ceilings, ever.
- An unconditional content mandate ("give every cast a running bit") injects that content
  where it doesn't fit. Condition on the story warranting it.
- Slop is scale-invariant: hook is not premise, quirk is not character, tension is not
  scene purpose. The winning premise needs zero devices.

## Reporting

Report to Nick with: overall verdict vs the gold bar first, the best 1-2 passages QUOTED,
the worst 1-2 QUOTED with the named failure law, then root cause → proposed fix for each
defect (fix the license/plumbing, not the symptom). Terse. He red-pens from there.
