# Tool Granularity Policy

When does a build tool deserve to exist? The agent composes a bounded set of tools to build an
artifact (`maestro/tools.py` `build_tools` + `TOOL_SCHEMAS`, scoped per module). Left ungoverned,
that set sprawls — a tempting `add_interactable`, then `add_character`, then `add_duelist`, one per
thing per genre. This is the rule that bounds it.

## The gate is model-relative — so name the floor

In the limit, an arbitrarily capable model needs **four tools**: `write_component`,
`read_component`, `validate`, `compile`. Read, reason, write the whole component, validate, fix.
Every *granular* tool (`write_node`, `edit_place`, `add_interactable`) is **scaffolding for a model
that cannot reliably one-shot a component without clobbering it.** There is no model-independent
justification for a granular tool; its value is entirely relative to how weak the model is.

That means the real decision is not "is this tool intrinsically valid" but **what model do we
standardize against.** We standardize against the **weakest model we commit to ship on** — the
**small local model** (qwen3.6-class). That is a deliberate product choice (local, private, cheap,
no API dependency — see the small-model strategy in `CLAUDE.md`), not an accident. The tool surface
is the price of that floor.

## The rule (falsifiable once the floor is named)

> A granular tool is justified iff **the committed-floor model cannot reliably clear a
> done-condition using the coarser tool**, AND the component it targets is a *collection the build
> loop grows or repairs incrementally* (not a one-shot).

- `add_interactable` → **justified at today's floor.** `places` is a repaired, growing collection;
  reachability/goal need "add one hotspot," and the floor model thrashes with the coarse tools
  (`write_place` clobbers siblings, `edit_place` cannibalizes an existing hotspot's route). Watched
  it fail; the tool clears `places_reachable` / `goal_reachable` / `each_place_min_interactables`.
- `add_character` → **not justified.** `characters` is a one-shot — the floor model writes it in a
  single `write_component` call. A granular tool buys nothing for the build.

The discriminator for the second clause: **one-shot vs. repaired collection.** One-shots (characters,
asset_manifest, matches) need no granular tools. Collections the loop builds/fixes item-by-item
under sub-loop targets (nodes, places, interactables) earn them.

## Tools are scaffolding pegged to the floor — prune as it rises

A granular tool is not permanent architecture. Raise the floor to a model that one-shots a clean
place, and `add_interactable` should be **deleted**, not kept. Module-ownership makes pruning local:
a mechanic-module owns its tools (`world` owns the place tools), so a mechanic sheds tools when
the floor outgrows them, without touching the core.

This *bounds* the surface and trends it **down**: tool count ≈ (mechanics in the library) ×
(granular ops the floor model needs per mechanic). The first factor is bounded (the substrate +
modules thesis — see `ir_architecture.md`); the second shrinks as models improve. A new *genre* adds
zero tools (it composes existing modules); only a new *mechanic* adds any.

## The one residue that survives any model

Not capability — **concurrency and cost.** Multi-agent editing (the per-character-agent direction)
and "don't regenerate a 2 KB component to change one field" survive even a perfect model. But those
argue for **one generic patch op** (`apply_patch(component, path, value)`), not named per-thing
tools. So do not justify `add_character`-style named tools on those grounds: if a strong model wants
surgical edits, give it a generic patch, not a proliferation of verbs.

## Summary

1. Granular tools are scaffolding for the **committed model floor** (small local model, by product
   choice), not intrinsic architecture.
2. Justify each against that floor: *coarse tool fails the floor model* **and** *target is a
   repaired collection*.
3. Tools are owned by modules and **prunable** — delete them as the floor rises.
4. Surgical edits wanted for a strong model (concurrency/cost) → a generic patch, never named
   per-thing verbs.
