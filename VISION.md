# Maestro — Vision and Non-Goals

## The goal

Maestro is a **locally runnable entertainment maker, focused on games.** A tool: ask for a game, get a runnable game — without spending years building it.

Not a demo. Not a proof of concept. A game someone would actually play. The bar is output that stands on its own — not "impressive for AI," just good.

AI quality is the product. Everything else (install experience, UI, speed) is scaffolding. The scaffolding matters, but it comes second. A beautiful UI delivering mediocre output is a failure. Ugly UI delivering excellent output is a success with a clear next step.

---

## What success looks like

The point is range — **any game you can imagine, made well.** A few shapes that bar takes:

- A branching visual novel whose characters feel like real people — scenes with tension, dialogue a human would be proud of
- A roguelike you want one more run of
- A point-and-click mystery whose puzzles actually click
- A cozy life-sim you lose an evening to
- A horror game that genuinely gets under your skin

Different genres, one bar: output that doesn't need the asterisk "AI-generated" to be appreciated. Work that surprises people — not "technically completed," *actually good.*

When the same machine makes a tabletop campaign, a novel chapter, or a song that stands on its own — bonus. Games are the proving ground; the breadth is welcome fallout.

---

## How we get there

**Specialization over generalism.** A single LLM call asked to "write a game" produces mediocre everything. The work decomposes: the story, the cast, each scene, each asset is its own small, focused, correctable call. The agent never writes the whole artifact in one shot.

**Small model compatibility as a forcing function.** If the build works well on a small local model, the prompt design is good. Small models fail loudly and specifically — they truncate, hallucinate structure, lose context. Designing for them produces a better build for every model.

**Agentic loop + frozen spec as the key abstraction.** The agent drafts a per-game **spec** — a contract of components with code-checkable done-conditions — and a human freezes it. Then a non-LLM loop builds against that frozen target until every check passes; "done" means the artifact satisfies the spec, never the agent claiming it. Minimal context is rebuilt from durable on-disk state each step, so the growing artifact never rots the window. This keeps the old pipeline's completion guarantee and context discipline without its rigidity.

---

## Non-goals

**Not a general-purpose AI assistant.**  
Maestro is for making things. If you want to answer questions, summarize documents, or write emails, use Claude or ChatGPT. Maestro's value is in the build architecture and the domain-specific quality it enables — that's only useful for creative production tasks.

**Not an agent.**  
Maestro doesn't know you, doesn't act on its own, and doesn't run unprompted. Request in → game out. It's a capability an agent *could* call to make games autonomously for someone — but building that agent is a separate project, out of scope here.

**Not a UI/UX product.**  
The frontend exists to drive builds and show output. It will improve, but UI quality is never the reason to ship or not ship. Pixel-perfect design before excellent output is the wrong order.

**Not optimized for speed.**  
A build that takes 45 minutes and produces a great game is better than one that takes 5 minutes and produces a mediocre one. Speed is a tiebreaker, not a goal. Don't sacrifice output quality for latency.

**Not a model training or fine-tuning platform.**  
Maestro works with models as-is. Improving output comes from better prompt design, better build architecture, and better decomposition — not from modifying the model.

**Not cloud-first.**  
Local model support is a core requirement, not an afterthought. Designing for local models (resource constraints, no API keys, OpenAI-compatible endpoints) keeps the architecture honest and the product accessible.

**Not a marketplace or platform (yet).**  
Plugin systems, third-party modules and engines, and contribution frameworks come after the core output quality is proven. Building platform infrastructure before the product works is the wrong order.

**Not trying to replace human creativity.**  
Maestro handles the parts of creative production that are slow, tedious, or technically complex — generating a consistent cast of characters, structuring a plot, writing 200 lines of Ren'Py script. The creative direction, judgment, and taste still come from the person using it.

**Not trying to support every LLM provider.**  
OpenAI-compatible API is the standard. Any endpoint that speaks it works. Native support for individual proprietary APIs is not worth the maintenance cost.

---

## The filter

When evaluating a feature or change, ask: does this make the output better, or does it make something else better?

If the output gets better: ship it.  
If something else gets better but the output doesn't: defer it until the output quality justifies the investment.
