# `maestro/` — the agentic build system

This package turns a frozen **spec** into a finished artifact (a game) by driving a
non-LLM loop that calls an LLM agent one tool at a time until every done-condition the
spec declares passes. It is engine-agnostic: it builds the engine-neutral **Game IR**;
a backend (`renpy/`, `web/`) projects that IR to a launchable project.

If you're new here, read `docs/game_creation_walkthrough.md` next — it traces one request
from chat to a packaged game, naming the function at each hop.

---

## The one idea

**Agentic loop + frozen spec.** A spec is a per-game contract: a list of components,
each carrying typed, code-checkable *done-conditions*. A human freezes it. Then a
non-LLM **executor** loops: rebuild a minimal context from durable on-disk state → ask
the agent for one tool call → run it → recompute the to-do with `validate`. "Done" is
decided by `validate` returning an empty failure list, **never** by the agent claiming
it. The growing artifact lives on disk, out of the context window, so context stays
roughly constant as the game grows.

Three layers:

1. **Spec layer** (agentic, human-gated) — the chat agent drafts a spec; the human
   reviews/edits/freezes it. Build tools refuse until `frozen: true`.
2. **Executor** (NOT an LLM) — drives the loop; completion via `validate`; minimal
   context rebuilt each step from durable state.
3. **Tools** — the bounded capabilities the agent composes (`write_node`, `validate`,
   `compile_*`, `generate_asset`, …). The agent chooses order, not the menu.

---

## Files, by role

### The loop
| File | Role |
|------|------|
| `run.py` | Orchestrator + CLI. `create_run` → `run_build` → packaged project. `python -m maestro.run "<request>"` proposes, freezes (with your ok), builds. |
| `executor.py` | The non-LLM loop. Stateless per step; rebuilds context; routes to the failing component lowest in dep-order; runs a **sub-loop** for components that iterate-until-done (nodes); decides completion via `validate`. Also the pause/cancel/awaiting-human boundaries. |
| `agent.py` | The LLM decider (`make_llm_decider`: one tool call per step) and the node **sub-loop** (`make_subloop`: a bounded tool conversation driving ONE target check to green). Also `rewrite_node` (regenerate one node from a human note). |
| `validate.py` | The steering signal + completion guarantee. Runs the spec's typed done-conditions against durable state → structured failure list (the to-do). Closed check set: `exists / count / distinct / each_has / refs_resolve / crossref / compiles`, plus registered engine checks. A check may **attribute** its failure to the component that can fix it. |

### Spec + state
| File | Role |
|------|------|
| `spec.py` | `Spec` — components, `frozen` flag, `dep_order()`. |
| `spec_tools.py` | `propose_spec` (draft story + `concept` hook → pick modules from the catalog → resolve params), `amend_spec` (un-freezes for re-approval), `freeze_spec` (the human's out-of-band approval — deliberately not a tool). |
| `chat_tools.py` | `propose_game_spec` / `amend_game_spec` — the chat-agent-facing wrappers. |
| `state.py` | `RunState` — the durable per-run dir `<working_dir>/runs/<run_id>/`: component JSONs, scratchpad, story state, human todos, waivers. The source of truth; the transcript is never memory. |
| `story_state.py` | The continuity bible (facts / entities / open threads / recent tail) — a snapshot, not a log. |

### Human-in-the-loop
| File | Role |
|------|------|
| `run_control.py` | Cross-thread `RunControl` (pause/resume/cancel + auto_pause) + per-run registry. The executor checks it at step boundaries. |
| `hitl.py` | Human-as-arbiter-of-done: human todos (block completion) + waivers (accept a red machine check). `effective_failures = validate − waivers + open todos`. |

### Composition + IR
| File | Role |
|------|------|
| `modules/module.py` | `Module` ABC + `MODULE_REGISTRY` + `compose()`/`resolve_modules()` + the per-engine projection registry. A `Module` is a set of `(error → fix)` over the shared components; `get_errors` is the only required method. |
| `modules/` | The mechanic-modules, each a direct `Module` subclass: `cast`, `story`, `scenes`, `world`, `assets`, `inventory`, `state`, `card_play`, `human` (+ inline validators/skeletons). `human`/`assets`/`state` are always-on (`selectable=False`); `__init__.py` registers them all. |
| `ir_assemble.py` | Lift the decomposed on-disk components → one engine-neutral IR dict. |
| `ir_crossref.py` | Gate that every id reference in the IR resolves; emits structured records routed by `slice_owner`. |
| `engines.py` | `compile_for(spec.engine)` — map engine tag → backend compile entry. |
| `tools.py` | The artifact tools (`build_tools`) + `TOOL_SCHEMAS`. `write_component` / `write_node` / `edit_node`, reads, `generate_asset`, `validate`, `compile_*`, `update_scratchpad`, `request_review`. |
| `prompts/` | One `.txt` per LLM call (climbable). Partials under `prompts/partials/` dedupe shared rules via `{{include:NAME}}`. |

---

## Substrate + modules, in one breath

A game = one **substrate** (execution model — `discrete_state` today) + a composed set
of **mechanic-modules** the spec selects. There is no genre/preset box and no exclusion:
the proposer picks modules from the catalog directly (`world`+`scenes` compose), and the
agent never branches on a component string — it reads the gating off the composed set.

- **content** modules author a component (`scenes`→nodes, `world`→places,
  `card_play`→matches, `cast`→characters, `story`→story, `inventory`→items).
- **cross-cutting** modules author none: `state` is the always-on wiring invariant
  (every declared flag/var/item needs a producer + consumer), `human` is the HITL channel.
- Engine **projections** register separately, keyed `(engine, module_id)` — a module's
  schema is substrate-agnostic; its renderer is per-engine. A module with no projection
  for the chosen engine makes the compile **fail fast**, never silently drop content.

Adding a module: see CLAUDE.md → "Adding a mechanic-module" (`inventory` is a small
worked example; `scenes`/`world` show an overridden `get_fix`).

---

## Run it

```bash
# CLI: propose → freeze (interactive y/N) → build → print project path
cd src && python -m maestro.run "make me a cozy mystery visual novel"

# Recompile a finished run without rebuilding (re-project on-disk JSON → engine)
cd src && python -c "from renpy.compiler import compile_renpy; print(compile_renpy('<run_dir>', distribute=True))"

# Tests
cd src && python -m pytest ../tests/ --ignore=../tests/integration -q
```
