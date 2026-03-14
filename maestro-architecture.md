# Maestro — Architecture Reference

## Project Vision

The user types a single sentence. A team of AI agents autonomously accomplishes it. The system comes back with a final, coherent result. That's the "black magic" UX this system is designed to deliver.

The name **Maestro** reflects this: a maestro doesn't play every instrument — they know every instrument, coordinate every player, and ensure the performance lands as intended.

---

## How the Current Orchestrator Works

### The Flow

```
User → TaskRunner → Planner → [static DAG committed to DB] → Orchestrator → done
```

### Step by Step

1. **User submits a goal** via the FastAPI `POST /tasks` endpoint.
2. **TaskRunner** creates a task record in SQLite and kicks off a background thread.
3. **PlannerAgent** receives the goal, builds a prompt that includes the available agent roster, and makes a single LLM call. It parses the JSON response into a full subtask DAG and commits it to the database — all subtasks created upfront, with position-based `depends_on` resolved to real IDs.
4. **Orchestrator** takes over. It reads the task's `execution_mode` (`sequential` or `parallel`) and runs accordingly:
   - **Sequential**: iterates subtasks in position order, executes each one, blocks until complete.
   - **Parallel**: polls for "ready" subtasks (those whose dependencies are all completed), submits them to a `ThreadPoolExecutor`, and loops until nothing is left.
5. **Each subtask** is executed by `_execute_subtask`, which instantiates a `MainAgent` with the target agent's system prompt and tools, builds context from the context store (semantic RAG + explicit keys + dependency outputs), and calls `agent.chat(goal + context)`. The output is written back to the context store and SQLite.
6. When all subtasks are done, the task is marked `completed`.
7. **WebSocket** broadcasts subtask/task lifecycle events to the frontend in real time.

### Key Components

| Component | Role |
|---|---|
| `TaskRunner` | Entry point — creates task, launches background thread |
| `PlannerAgent` | One-shot LLM call that produces the full subtask DAG |
| `Orchestrator` | Pure executor — runs whatever the planner produced |
| `MainAgent` | Stateless agent runner — chat loop with tool calling |
| `ContextBuilder` | Assembles context (RAG + explicit keys + dep outputs) for each subtask |
| `TaskStore` | SQLite interface — tasks, subtasks, context, events |
| `AgentStore` | JSON file store for agent definitions (system prompt + tools) |

### What the Current Architecture Cannot Do

- **It cannot create tasks it doesn't know about upfront.** The planner runs once, before any work is done. If the story outliner produces 8 chapters, the planner couldn't have known that — so it either guesses or produces placeholder tasks.
- **The orchestrator has no judgment.** It executes a plan blindly. It cannot evaluate whether outputs are good, notice when something is wrong, or decide to try a different approach.
- **There is no coherent final output.** Tasks complete, but nobody synthesizes the results into a final deliverable and hands it back to the user.
- **The planner disappears after planning.** Once the DAG is committed, the planner is gone. There is no ongoing reasoning about the goal.

---

## How Maestro Will Work

### The Mental Model

> Maestro is a puppet master. The user gives Maestro a goal. Maestro gets out the right puppets and makes them dance. It knows up front which puppets will be needed, and as the performance evolves it brings in others as required. At the end, Maestro comes back and says: "Here's what you asked for. I made sure everything was done correctly."

### The New Flow

```
User → MaestroAgent (ongoing reasoning loop) → Synthesis Agent → User
            ↕              ↕              ↕
         Planner        Agents        Re-evaluation
```

Maestro **owns the goal from receipt to delivery**. It is the only entity that talks to the user. The planner becomes a tool Maestro calls internally, not a separate upfront step.

### The Loop

1. **Receive goal.** Maestro understands what "done" looks like and holds that throughout.
2. **Plan the first wave.** Maestro calls the planner to determine what can be done now, knowing some things cannot be planned yet (e.g. chapter tasks require an outline to exist first).
3. **Execute the wave.** Agents run, raw outputs are collected.
4. **Evaluate.** Maestro reads the raw outputs and reasons against the original goal: *What was produced? Does it meet the bar? What's missing? What comes next?*
5. **Plan the next wave** — or hand off to the Synthesis Agent if the goal is met.
6. **Repeat** until synthesis is triggered.
7. **Synthesis Agent** produces the final deliverable (a book, a codebase, a report — whatever was asked for).
8. **Maestro returns** that output to the user.

### What Changes

**`MaestroAgent` replaces both `PlannerAgent` and `Orchestrator`** (and `TaskRunner` largely disappears or becomes a thin wrapper).

- Maestro has its own LLM-powered identity: a strong system prompt that tells it who it is, what its job is, the full agent roster, and that it is responsible for quality — not just task completion.
- After each wave, Maestro makes an LLM call to evaluate outputs and decide what happens next.
- The planner becomes a helper Maestro invokes when it needs to think ahead — not a gatekeeper that runs before anything else.
- The re-planning decision lives in Maestro's loop, not in a sentinel subtask type.

### What Stays the Same

- `TaskStore` — no changes needed. Dynamic subtask creation already works; `get_ready_subtasks` handles it.
- `AgentStore` — agents are unchanged. Maestro reads from it to know what puppets are available.
- `MainAgent` / individual agents — unchanged. They do work; Maestro coordinates.
- `ContextBuilder` — unchanged. Context assembly per subtask is still the right pattern.
- `TaskStore` WebSocket events — unchanged. The frontend already handles task lifecycle events.

### Agent-Level Task Spawning (Bounded)

Some agents will be given a `spawn_subtask` tool. This is **not** the same as Maestro-level re-planning.

- **Maestro-level spawning**: creates tasks that advance the *goal* (e.g. compile, format, synthesize). Requires global awareness.
- **Agent-level spawning**: creates tasks that advance the *quality of that agent's own output* (e.g. a code-writer spawning a code-review task before returning its output to Maestro).

**Constraint**: agents with `spawn_subtask` may only spawn tasks that feed back into themselves. They cannot inject tasks into the broader DAG. Maestro never sees the internal quality loop — only the final validated result.

---

## Pending Work — Full Backlog

### Immediate: Maestro Implementation

- [ ] **Design `MaestroAgent` system prompt** — identity, agent roster format, re-planning instructions, quality bar expectations, synthesis handoff criteria
- [ ] **Design Maestro's re-evaluation prompt** — what it receives after each wave (raw outputs, original goal, completed subtask list), what it must return (next wave of subtasks OR synthesis trigger)
- [ ] **Implement `MaestroAgent` class** — LLM-powered reasoning loop, wave execution, re-evaluation, synthesis handoff
- [ ] **Implement `SynthesisAgent`** — receives all context/outputs, produces the final deliverable, hands back to Maestro
- [ ] **Wire up `MaestroAgent` as the entry point** — replace `TaskRunner` → `PlannerAgent` → `Orchestrator` chain
- [ ] **Update `task_store` schema if needed** — e.g. tracking "waves", Maestro reasoning log
- [ ] **Update WebSocket events** — Maestro reasoning steps should be visible in the frontend activity feed

### Near Term: Agent-Level Spawning

- [ ] **Implement `spawn_subtask` tool** — callable by select agents, creates a quality-gate subtask that feeds back into the calling agent only
- [ ] **Define which agents get `spawn_subtask`** — likely: code-writer, writer, researcher (any agent where output quality is measurable before returning)
- [ ] **Enforce the boundary** — spawned subtasks must be scoped to the calling agent's output, not the broader DAG

### Backlog: Future Capabilities

- [ ] **Agent Creator** — an agent (or Maestro capability) that can define and register entirely new agents at runtime, so Maestro can invent tools it doesn't have yet
- [ ] **Task Creator Agent** — if Maestro's re-planning logic grows complex enough, extract it into a dedicated agent that Maestro feeds context to and receives a task list back; for now Maestro reasons about this itself
- [ ] **Structured agent output metadata** — agents return structured metadata alongside prose output (e.g. `{"chapters": 8, "titles": [...]}`) so Maestro can plan the next wave without parsing prose
- [ ] **Plan editing before execution** — allow the user to review and modify Maestro's initial plan before agents begin running (previously on Layer 11 roadmap)
- [ ] **Task interruption** — allow the user to pause/cancel a running task mid-execution (previously on Layer 11 roadmap)
- [ ] **Output export** — download final outputs (previously on Layer 11 roadmap)
- [ ] **In-UI agent editor** — create and edit agents from the frontend (previously on Layer 11 roadmap)
- [ ] **Keyboard shortcuts** — polish UX feature (previously on Layer 11 roadmap)

### Known Bugs / Tech Debt (from prior code review)

- [ ] **ToolManager singleton inconsistency** — identified in code review, not yet fixed
- [ ] **Hardcoded `SYSTEM_PROMPT` instead of `self.system_context`** in the MainAgent agentic loop — identified in code review, not yet fixed
- [ ] **`NameError` risk on `json.loads` failure** in tool call handling — identified in code review, not yet fixed
- [ ] **README is outdated** — does not reflect current architecture
- [ ] **`broadcast_fn` lambda captures unbound `task_id`** in `tasks.py` — identified in WebSocket debugging session, risk depends on thread timing

### Branding

- [ ] **Project name**: "Maestro" is confirmed. "Puppeteer" was considered but conflicts with https://pptr.dev/.
- [ ] **Rebrand sweep** — rename references from old orchestrator/planner framing to Maestro franding across codebase, README, and frontend UI when the time is right

---

## Appendix: Current Layer Roadmap (for reference)

Layers 1–5 are complete. Layers 6–11 were the original FastAPI/React roadmap; Maestro effectively replaces or reshapes Layer 6 (orchestration) significantly.

| Layer | Description | Status |
|---|---|---|
| 1 | Core agentic loop, tool manager, agent store | ✅ Done |
| 2 | SQLite task store, sequential/parallel execution | ✅ Done |
| 3 | sqlite-vec embeddings, RAG context retrieval | ✅ Done |
| 4 | LLM-powered planner, dependency-aware DAGs | ✅ Done |
| 5 | Orchestrator execution loop | ✅ Done |
| 6 | FastAPI backend | ✅ Done |
| 7 | React/TypeScript frontend (3-panel layout) | ✅ Done |
| 8 | **Maestro — dynamic re-planning orchestrator** | 🔲 Next |
| 9 | Agent-level task spawning (`spawn_subtask` tool) | 🔲 Backlog |
| 10 | Structured agent output metadata | 🔲 Backlog |
| 11 | Polish — interruption, export, agent editor, shortcuts | 🔲 Backlog |
