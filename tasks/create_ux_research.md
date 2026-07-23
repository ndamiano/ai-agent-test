# Create-Game UX — Research Deep-Dive

**Type: research, not build.** The ask is not "implement screens" — it's *figure out what a GOOD
experience for creating a game should be*. This file frames the research question and its
deliverable. Build tasks get carved out AFTER the research lands (they'll likely extend or
replace the current build-cockpit frontend).

## Why
Today the create flow is: chat request → `propose_game_spec` → freeze in Games → watch a log →
recompile. It works but was never designed as an *experience* — it grew from the plumbing. Before
polishing it we should decide what good looks like, or we'll polish the wrong shape.

## The research question
What is the ideal end-to-end journey for "user wants a game → a good game exists"? Covering:
- **Front door** — how the user expresses intent (freeform chat? guided interview? examples/templates? a mix?). How much scoping the system should pull out before drafting.
- **Spec review/freeze** — how the user sees + edits the drafted spec (modules + reasons, sizing, concept) and commits it. How much to expose vs hide.
- **Build legibility** — what the user watches during the (long, local-model) build: progress, per-component state, the ability to steer or interject. Trust-building for a slow black box.
- **Review + iterate** — seeing the finished game, thumbs/steer, request changes.

## Method
- Use the **deep-research** harness (skill: `deep-research`) — competitive scan of how other
  AI-generation products handle a long, multi-stage, human-gated create flow (AI game/app/site
  builders, image/video gen queues, agentic coding UIs). Fan-out → verify → synthesize.
- Synthesize a **proposed journey** (wireframe-level sketches + the key interaction decisions +
  rationale), not code.
- Identify the core tensions to resolve deliberately: power/legibility vs simplicity;
  chat vs form vs wizard; how to make a slow build feel trustworthy not broken.

## Deliverable
A written UX proposal (flow + sketches + decisions) that we review, then carve into build tasks.
Only after that do frontend tasks get created.

## Relationship to existing work
- The *current partial* UX is the SPA's chat front door + the persistent build cockpit (durable
  event feed, asset gallery, structured spec view in `frontend/src/components/GamesPanel.tsx`).
  The research decides whether to extend that or rethink it — do not build more of it until this
  lands.

## Parked
- Not designing now (owner's call). This is a placeholder + scoped research brief so it isn't lost.
