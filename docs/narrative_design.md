# Narrative design — distilled conclusions

Design knowledge salvaged from the storyline-pivot work (the implementation plan targeted the
deleted IR stack and is gone; these conclusions are architecture-neutral and should seed any
future story/quest generation prompts or structures).

## Theme + tone spine, not a central question

Do not force a story to declare a single "central question" (a values-fork the plot pivots on).
Most good stories have none — a **theme** and a **tone** carry them (Foundation, Hocus Pocus,
Zombieland, Harry Potter). Forcing a fork, a back-loaded crisis, and a minimum ending count makes
a model manufacture fake forks, duplicate endings, and flat resolution tails.

The spine of a generated story is `{theme, tone, optional trope}` — e.g. theme "loyalty tested by
scarcity", tone "wry and warm, occasionally bleak", trope "heist". Individual storylines may
override theme/tone locally; otherwise they inherit the spine. Grade tone on **consistency**
(uniform tone is best), not variety.

## A graph of linear storylines, not one branching beat-sheet

A single monolithic beat-sheet that must hold a whole branching tree coherent is the wrong unit —
it overloads the author (human or model) with keeping every branch consistent at once. Instead:

- **One start storyline; branch points spin off new, also-linear storylines.** The linear beat
  sequence stays the authoring unit; a storyline is a named group of beats with a premise.
- **A storyline is not responsible for what it branched off to.** The spun-off line is authored
  separately, with only its originating branch point (source beat + the choice taken + any
  return point) plus the spine and an index of existing storylines as context.
- **Branching is demand-driven.** Declaring a branch creates the demand for its target storyline;
  the author writes ONE linear storyline at a time and never plans the whole graph up front.
  This is the shape that lets a small model handle multi-threaded plots: it only ever holds one
  linear thread in its head.
- **Convergence is free.** Any number of paths may merge into one point; merging needs no special
  machinery beyond "this line flows into that line at that beat".
- **Variable length.** A 3-beat side quest and a 15-beat main line coexist. Enforce a hard
  minimum floor per storyline, let the author choose a target length above it, and give the
  author an explicit "this storyline is finished" declaration so lines end at their natural
  length. The done-flag may never lower the floor.
- **Cap the graph.** A storyline can spin storylines, so an uncapped model runs away. A maximum
  storyline count (and/or spin depth) is mandatory; once hit, new choices must merge into or
  hand back to existing lines instead of spawning.

## Code-guarded termini — the model never decides "end vs hand back"

Every storyline ends in exactly one of three ways, and **code, not the model, sets which**:

1. **Game end** — terminal; these ARE the endings. The terminus carries the ending's concrete
   final scene.
2. **Handoff** — control returns to the storyline that spawned this one. The return point is
   **encoded at the branch point** (in the spawning line), never remembered at the terminus — a
   small model reliably gets "end vs hand back" wrong when asked to recall it at the far end.
3. **Merge** — flows into another storyline mid-stream at a named beat.

The type is derived from structure (a side line spun with a return point hands off; a main line's
final beat ends the game). Deterministically rewrite violations — a live run showed a model
re-asserting the same rejected transition hundreds of steps straight, so this must be a hard code
override, not a prompt instruction. A side quest must never be able to end the game.

## Endings: earned structurally, multiplicity optional

- **Endings are terminal storylines.** No prose field naming "the choice that earns this ending":
  an ending is earned by the path of branches (each optionally gated on accumulated state)
  that reaches it. This is what open worlds need — an ending earned by accumulated state, not
  one prose-named fork.
- **A single-ending game is valid.** Never require N distinct endings. Distinctness matters only
  IF several endings exist, and only against total collapse (several endings that are literally
  one identical outcome); clusters of similar endings — four flavors of "you died" — are fine.
  Note: distinctness has no structural guarantee — the only real lever is spinning each terminal
  line from a distinct branch with a distinct premise.

## One model for VN and open world

Theme+tone spine + a graph of linear storylines unifies the branching-VN shape and the
open-world shape: an open world is a main line to the end plus side-quest storylines that
connect back via shared state. No forced fork, no forced climactic multi-choice menu, no
minimum ending count. Design them as one thing.

## Cross-storyline state gating — the known gaps

- Shared flags/state are the callback substrate: a branch in one storyline can be gated on a
  flag set in another (spare the goblin → later doors open). Enforce use-it-or-cut-it: every
  flag needs both a producer and a consumer.
- **Existence checks are not enough.** A flag set only *after* its gate, or only on an
  unreachable path, passes producer+consumer existence yet never fires. A reliable callback
  needs a path-aware check: the producing line must be reachable *before* the gate.
- **Demand-driven side content under-generates.** If the main line never branches, zero side
  storylines exist — correct for a linear story, hollow for an "open world". Representability
  does not create the desire to branch; the pressure must come from somewhere else (a
  quest-giver floor per world, or a spec-level instruction).
