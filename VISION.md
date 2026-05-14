# Maestro — Vision and Non-Goals

## The goal

User says "make me a game." An hour later, a good game exists.

Not a demo. Not a proof of concept. A game someone would actually play. A novel someone would actually read. A TTRPG campaign a group would actually run. The bar is output that stands on its own — not "impressive for AI," just good.

AI quality is the product. Everything else (install experience, UI, speed) is scaffolding. The scaffolding matters, but it comes second. A beautiful UI delivering mediocre output is a failure. Ugly UI delivering excellent output is a success with a clear next step.

---

## What success looks like

- A visual novel with characters that feel like real people, scenes that have tension, dialogue that sounds like a human wrote it
- A TTRPG campaign a group runs without having to redline half the content
- A novel chapter that could plausibly appear in a published book
- Output that doesn't need the asterisk "AI-generated" to be appreciated

The target is work that surprises people. Not "technically completed" — actually good.

---

## How we get there

**Specialization over generalism.** A single LLM call asked to "write a game" produces mediocre everything. Pipelines decompose the work: one stage for story structure, one for character identity, one for dialogue. Each call is small, focused, and correctable.

**Small model compatibility as a forcing function.** If a pipeline works well on a local 7B model, the prompt design is good. Small models fail loudly and specifically — they truncate, hallucinate structure, lose context. Designing for them produces better pipelines for everything.

**Pipelines as the key abstraction.** The main agent sees inputs and outputs, never intermediate steps. It fires `run_pipeline("renpy")` and gets back a completed game. This keeps the agent's context clean and lets pipelines be independently optimized, tested, and composed.

---

## Non-goals

**Not a general-purpose AI assistant.**  
Maestro is for making things. If you want to answer questions, summarize documents, or write emails, use Claude or ChatGPT. Maestro's value is in the pipeline architecture and the domain-specific quality it enables — that's only useful for creative production tasks.

**Not a UI/UX product.**  
The frontend exists to drive pipelines and show output. It will improve, but UI quality is never the reason to ship or not ship. Pixel-perfect design before excellent output is the wrong order.

**Not optimized for speed.**  
A pipeline that takes 45 minutes and produces a great game is better than one that takes 5 minutes and produces a mediocre one. Speed is a tiebreaker, not a goal. Don't sacrifice output quality for latency.

**Not a model training or fine-tuning platform.**  
Maestro works with models as-is. Improving output comes from better prompt design, better pipeline architecture, and better decomposition — not from modifying the model.

**Not cloud-first.**  
Local model support is a core requirement, not an afterthought. Designing for local models (resource constraints, no API keys, OpenAI-compatible endpoints) keeps the architecture honest and the product accessible.

**Not a marketplace or platform (yet).**  
Plugin systems, third-party pipelines, and contribution frameworks come after the core output quality is proven. Building platform infrastructure before the product works is the wrong order.

**Not trying to replace human creativity.**  
Maestro handles the parts of creative production that are slow, tedious, or technically complex — generating a consistent cast of characters, structuring a plot, writing 200 lines of Ren'Py script. The creative direction, judgment, and taste still come from the person using it.

**Not trying to support every LLM provider.**  
OpenAI-compatible API is the standard. Any endpoint that speaks it works. Native support for individual proprietary APIs is not worth the maintenance cost.

---

## The filter

When evaluating a feature or change, ask: does this make the output better, or does it make something else better?

If the output gets better: ship it.  
If something else gets better but the output doesn't: defer it until the output quality justifies the investment.
