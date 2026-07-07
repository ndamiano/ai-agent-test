# Maestro HITL — buildable backlog

> Derived from `hitl_architecture.md` (the settled model). **Supersedes the issue
> list in `hitl_vision.md`** (that one was built on patching the current UX; this is
> the rebuild). Tags: size (S/M/L), surface (FE/BE/both), files it touches.

## Build order (the spine)

Foundation is backend — the UI is a render of it, so it comes second.

```
A. Dirty core ─┐
B. Propagation ─┼─▶ C. API + events ─▶ D. Component browser ─▶ F. Chat ─▶ G. Delete old
               │                        E. Live legibility ┘
```

A + B + C are the model made real. D is the user-visible rebuild. E rides on C's
events. F/G are the seams and the teardown — G (delete the old UX) is **last**, once
the browser replaces it.

---

## Epic A — Dirty core (backend foundation)

The whole model is "dirty is an error." This epic makes that literal.

- **A1 — Dirty sidecar store.** A set of asset idkeys + `review_note`s, persisted in
  `RunState` next to waivers. Extend the `human` module's existing HITL store — same
  shape as waivers, zero component-schema change. *(S, BE — `maestro/modules/human.py`,
  `maestro/state.py`)*
- **A2 — Dirty-emits-error check.** One `Check` on the `human` module sweeps the
  store and emits one `Error` per dirty idkey; its `Fix` routes to the owning
  module's rewrite/author path (`review_note` = instruction). The existing loop drain
  clears it — no loop change. *(M, BE — `human.py`, `maestro/modules/checks.py`)*
- **A3 — Human-only dirty/thumb tools.** `set_dirty(idkey, note)`,
  `thumbs_up(idkey)` (clear), `thumbs_down(idkey, note)` (= set_dirty). Human-only,
  like `force` — NOT in agent `TOOL_SCHEMAS`. *(S, BE — `maestro/tools.py`)*
- **A4 — Thumbs-down note → rewrite instruction.** The `review_note` flows into the
  rewrite prompt (`rewrite_node` already takes a note). *(reuse/S, BE —
  `maestro/rewrite.py`)*

## Epic B — Propagation (the dependency DAG)

Reflag the downstream closure on any edit. The graph already exists — reify it.

- **B1 — Reify the dependency graph.** Expose a queryable edge set keyed by asset
  idkey, assembled from `ir_crossref`'s `{path, ref, kind}` records + the `state`
  module's producer→consumer pairs. *(M, BE — new `maestro/depgraph.py`,
  `maestro/ir_crossref.py`, `maestro/modules/state.py`)*
- **B2 — `mark_downstream_dirty(idkey)`.** Transitive downstream-closure walk; set
  dirty on every reachable dependent (blessed-long-ago included). *(S, BE —
  `depgraph.py`)*
- **B3 — Hook propagation into edits.** After any human-initiated
  `edit_node` / `edit_component` / `rewrite_node`, call `mark_downstream_dirty`.
  *(S, BE — `maestro/tools.py`)*

## Epic C — API + events (plumbing the model to the client)

The UI shell is component-blind; it needs a uniform, event-driven data surface.

- **C1 — Uniform asset API.** `list assets of type X` + `get asset detail`, generic
  over every component type (drive off the composed module set + decomposed on-disk
  components). *(M, both — `api/routers/games.py`, `frontend/src/api/client.ts`)*
- **C2 — Dirty/thumb endpoints.** POST set_dirty / thumbs_up / thumbs_down / note →
  the A3 tools. *(S, both — `games.py`, `client.ts`)*
- **C3 — Uniform edit endpoint, un-gated.** Edit any asset's content; **drop the
  paused-build gate** (`_require_editable`) — edits are allowed mid-build (they just
  reflag). *(M, both — `games.py`)*
- **C4 — Dirty + asset-state events.** Emit on dirty set/cleared, asset generated,
  asset state change, so the browser updates live without refetch. *(M, BE —
  `maestro/agent_loop.py`, `api/websocket/event_bus.py`, `maestro/run.py`)*
- **C5 — Attach live state to build events.** Put the effective todo/component-state
  on `build_step`/`build_started` (fixes the null-`todo` dead wire so the board is
  live). *(S, BE — `agent_loop.py`)*

## Epic D — The component browser UI (the rebuild)

The user's core vision: every component, real-time, exact state, touchable, editable,
modular. This replaces `GamesPanel.tsx` wholesale.

- **D1 — Shell scaffold.** Tabbed component browser; **tabs driven by the composed
  module set** (new module → new tab automatically). Replaces the tab/log layout.
  *(L, FE — new `frontend/src/components/browser/` tree)*
- **D2 — Central card + navigation.** Big central card, position indicator ("Scene 4
  of 12"), prev/next arrows within a type, **jump-to-next-dirty**. *(M, FE)*
- **D3 — Renderer registry + generic fallback.** `COMPONENT_VIEWS` map + a
  schema-driven `SchemaCard` so a brand-new component is fully usable (view/edit/thumb)
  day one, pretty renderer later. Mirrors the backend "module owns its presentation."
  *(M, FE)*
- **D4 — Initial per-component renderers.** `SceneCard` (screenplay), `CharacterCard`,
  `PlaceCard` (map/layout), `EncounterCard` (stat block). Each is independent — add
  incrementally, generic fallback covers the rest. *(M each, FE)*
- **D5 — Inline edit on every field.** Edit any content in place; save → C3 →
  propagation. *(M, FE)*
- **D6 — Thumbs + "change this" on every card.** Up = C2 clear; down = C2 set +
  capture the note. *(S, FE)*
- **D7 — Dirty highlighting.** Tab-badge counts, card banner showing `review_note`,
  nav-strip markers so dirty cards are findable. *(S, FE)*
- **D8 — Live wiring.** Subscribe to C4 events; update the board in real time. *(M,
  FE — `frontend/src/contexts/WebSocketContext.tsx`)*

## Epic E — Live build legibility (exact state, real-time)

- **E1 — Component-state model.** Derive per-asset state (missing / generating /
  fresh / dirty) from events + the dirty store; drive the board's badges. *(M, both)*
- **E2 — Progress + elapsed.** step N/max + an elapsed timer (emit `elapsed`, already
  computed server-side). *(S, both — `run.py`, FE)*
- **E3 — Park/thrash surfacing.** Handle `error_parked` (emitted, unhandled today);
  show it on the relevant asset card, not a silent build-end. *(S, both)*

## Epic F — Chat front door

- **F1 — Stream chat responses + tool progress.** Replace the block-on-`to_thread`
  bouncing-dots with streamed tokens + "drafting spec…". *(M, both —
  `api/routers/chat.py`, `frontend/src/components/ChatPanel.tsx`)*
- **F2 — Chat→build continuity.** A chat-created run auto-surfaces and auto-selects —
  no tab switch + manual Refresh. *(S, both)*

## Epic G — Delete the old UX (last)

- **G1 — Rip out the old surface.** The log-as-primary view, the old tab body, the
  contract tab, the pause-to-edit gate — all replaced by the browser. *(M, FE —
  `GamesPanel.tsx`)*
- **G2 — Remove dead wires.** The never-fired `awaiting_human` UI/handler, manual
  Refresh, optimistic-control desync patches — obviated by C4/C5's live events. *(S,
  FE + BE)*

---

## Not in scope (settled out)

- **No `awaiting_human` state, no blocking/subtree-gating, no fingerprints.** Dirty
  is an error; the loop auto-rewrites; the human pauses explicitly if they want to be
  waited on. (See `hitl_architecture.md` §Settled policy.)
- **No `dirty` field on component schemas** — it's a sidecar in the `human` store.
