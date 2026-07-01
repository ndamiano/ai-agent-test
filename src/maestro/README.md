# `maestro/` — the agentic build system

This package turns a frozen **spec** into a finished artifact (a game) by driving a
non-LLM loop that calls an LLM agent one tool at a time until every done-condition the
spec declares passes. It is engine-agnostic: it builds the engine-neutral **Game IR**;
a backend (`renpy/`, `web/`) projects that IR to a launchable project.

If you're new here, read `docs/game_creation_walkthrough.md` next — it traces one request
from chat to a packaged game, naming the function at each hop.

---

## The one idea

**Agentic loop + frozen spec.** A spec is a per-game contract: a composed set of
modules, each emitting code-checkable *errors* over the shared components. A human
freezes it. Then a non-LLM **loop** (`AgentLoop`) runs: rebuild a minimal context from
durable on-disk state → collect every module's `get_errors` → ask the owning module to
fix the highest-priority one → run that fix through a budget-capped `Services`. "Done" is
decided by `get_errors` coming back empty (minus the human's waivers), **never** by the
agent claiming it. The growing artifact lives on disk, out of the context window, so
context stays roughly constant as the game grows.

Three layers:

1. **Spec layer** (agentic, human-gated) — the chat agent drafts a spec; the human
   reviews/edits/freezes it. Build tools refuse until `frozen: true`.
2. **Loop** (`AgentLoop`, NOT an LLM) — drives the build; completion via `get_errors`;
   minimal context rebuilt each step from durable state; each fix runs through a
   budget-capped `Services`.
3. **Tools** — the bounded capabilities the fixes compose (`write_node`, `edit_node`,
   `compile_*`, `generate_asset`, …). The module chooses order, not the menu.

---

## Files, by role

### The loop
| File | Role |
|------|------|
| `run.py` | Orchestrator + CLI. `create_run` → `run_build` → packaged project. `python -m maestro.run "<request>"` proposes, freezes (with your ok), builds. |
| `agent_loop.py` | The non-LLM loop (`AgentLoop`). Stateless per step; rebuilds context; collects every module's `get_errors`; prioritizes by error **type** (`HUMAN`→`BUILD`→`FIX`) then `Module.priority`; asks the owning module for a `Fix` and runs it. Keeps completion + cross-fix stall; auto-pauses a finished component. Also the pause/cancel/awaiting-human boundaries. |
| `services.py` | The bounded gateway a `Fix` calls through (connector + tool dispatch + pause/cancel checkpoint + **per-fix step budget**; `BudgetExhausted` is a `BaseException`, so a fix can't churn past its cap). `dispatch` enforces the step's tool scope; a count target is driven by per-slot create-errors (`checks.slot_errors`) one item per step, each write slot-guarded (`_create_guard`). |
| `context.py` / `views.py` | `Context` (durable per-step snapshot) + `render_dict` (the dict the per-module prompts consume); `views.py` `node_view`/`place_view` graph projections. |
| `rewrite.py` | `rewrite_node` — regenerate ONE node from a human note, outside the build loop (per-scene control). |

### Spec + state
| File | Role |
|------|------|
| `spec.py` | `Spec` — components, `frozen` flag, `dep_order()`. |
| `tools/spec_tools.py` | `propose_spec` (draft story + `concept` hook → pick modules from the catalog → resolve params), `amend_spec` (un-freezes for re-approval), `freeze_spec` (the human's out-of-band approval — deliberately not a tool). *(lives in `tools/`, not `maestro/`.)* |
| `tools/chat_tools.py` | `propose_game_spec` / `amend_game_spec` — the chat-agent-facing wrappers. |
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
