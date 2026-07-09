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

## Epic A — Dirty core (backend foundation) — ✅ DONE (backend, commits 7d1f055 + 03edeac)

The whole model is "dirty is an error." This epic makes that literal.

- [x] **A1 — Dirty sidecar store.** A set of asset idkeys + `review_note`s, persisted in
  `RunState` next to waivers. Extend the `human` module's existing HITL store — same
  shape as waivers, zero component-schema change. *(S, BE — `maestro/modules/human.py`,
  `maestro/state.py`)*
- [x] **A2 — Dirty-emits-error check.** One `Check` on the `human` module sweeps the
  store and emits one `Error` per dirty idkey; its `Fix` routes to the owning
  module's rewrite/author path (`review_note` = instruction). The existing loop drain
  clears it — no loop change. *(M, BE — `human.py`, `maestro/modules/checks.py`)*
- [x] **A3 — Human-only dirty/thumb tools.** `set_dirty(idkey, note)`,
  `thumbs_up(idkey)` (clear), `thumbs_down(idkey, note)` (= set_dirty). Human-only,
  like `force` — NOT in agent `TOOL_SCHEMAS`. *(S, BE — `maestro/tools.py`)*
- [x] **A4 — Thumbs-down note → rewrite instruction.** The `review_note` flows into the
  rewrite prompt (`rewrite_node` already takes a note). *(reuse/S, BE —
  `maestro/rewrite.py`)*

## Epic B — Propagation (the dependency DAG) — ✅ DONE (commit 7d1f055; hook narrowed in 03edeac)

Reflag the downstream closure on any edit. The graph already exists — reify it.

- [x] **B1 — Reify the dependency graph.** Expose a queryable edge set keyed by asset
  idkey, assembled from `ir_crossref`'s `{path, ref, kind}` records + the `state`
  module's producer→consumer pairs. *(M, BE — new `maestro/depgraph.py`,
  `maestro/ir_crossref.py`, `maestro/modules/state.py`)*
- [x] **B2 — `mark_downstream_dirty(idkey)`.** Transitive downstream-closure walk; set
  dirty on every reachable dependent (blessed-long-ago included). *(S, BE —
  `depgraph.py`)*
- [x] **B3 — Hook propagation into edits.** After any human-initiated
  `edit_node` / `edit_component` / `rewrite_node`, call `mark_downstream_dirty`.
  *(S, BE — `maestro/tools.py`)*

## Epic C — API + events (plumbing the model to the client) — ✅ DONE (backend, commit 7d1f055)

The UI shell is component-blind; it needs a uniform, event-driven data surface.

- [x] **C1 — Uniform asset API.** `list assets of type X` + `get asset detail`, generic
  over every component type (drive off the composed module set + decomposed on-disk
  components). *(M, both — `api/routers/games.py`, `frontend/src/api/client.ts`)*
- [x] **C2 — Dirty/thumb endpoints.** POST set_dirty / thumbs_up / thumbs_down / note →
  the A3 tools. *(S, both — `games.py`, `client.ts`)*
- [x] **C3 — Uniform edit endpoint, un-gated.** Edit any asset's content; **drop the
  paused-build gate** (`_require_editable`) — edits are allowed mid-build (they just
  reflag). *(M, both — `games.py`)*
- [x] **C4 — Dirty + asset-state events.** Emit on dirty set/cleared, asset generated,
  asset state change, so the browser updates live without refetch. *(M, BE —
  `maestro/agent_loop.py`, `api/websocket/event_bus.py`, `maestro/run.py`)*
- [x] **C5 — Attach live state to build events.** Put the effective todo/component-state
  on `build_step`/`build_started` (fixes the null-`todo` dead wire so the board is
  live). *(S, BE — `agent_loop.py`)*

## Epic D — The component browser UI (the rebuild) — ✅ DONE (commits dcac91a + this branch)

The user's core vision: every component, real-time, exact state, touchable, editable,
modular. `frontend/src/components/browser/` is the run's primary view; `GamesPanel.tsx`
now hosts it (list rail + build controls + progress header) instead of the old tab/log body.

- [x] **D1 — Shell scaffold.** Tabbed component browser; **tabs driven by the composed
  module set** (new module → new tab automatically). Replaces the tab/log layout.
  *(L, FE — new `frontend/src/components/browser/` tree)*
- [x] **D2 — Central card + navigation.** Big central card, position indicator ("Scene 4
  of 12"), prev/next arrows within a type (mouse + keyboard ←/→), **jump-to-next-dirty**. *(M, FE)*
- [x] **D3 — Renderer registry + generic fallback.** `COMPONENT_VIEWS` map + a
  schema-driven `SchemaCard` so a brand-new component is fully usable (view/edit/thumb)
  day one, pretty renderer later. Mirrors the backend "module owns its presentation."
  *(M, FE)*
- [x] **D4 — Initial per-component renderers.** `SceneCard` (screenplay), `CharacterCard`,
  `PlaceCard` (rows+legend rendered as a colored map), `EncounterCard` (stat block), plus
  `AssetManifestCard` (image gallery). Each is independent — generic fallback covers the rest. *(M each, FE)*
- [x] **D5 — Inline edit on every field.** Edit any content in place; save → C3 →
  propagation (`flagged_dependents` reflag the board live). *(M, FE)*
- [x] **D6 — Thumbs + "change this" on every card.** Up = C2 clear; down = C2 set +
  capture the note. *(S, FE)*
- [x] **D7 — Dirty highlighting.** Tab-badge counts, card banner showing `review_note`,
  nav-strip markers so dirty cards are findable. *(S, FE)*
- [x] **D8 — Live wiring.** Subscribe to C4 events (`useAssetBoard`); update the board in real time. *(M,
  FE — `frontend/src/contexts/WebSocketContext.tsx`)*

## Epic E — Live build legibility (exact state, real-time) — ✅ DONE

- [x] **E1 — Component-state model.** Per-asset state from events + the dirty store: an
  asset present on disk is fresh, in the dirty store is dirty (badge + banner), arriving
  via `asset_updated` appears live; a light per-component done/failing summary drives the header. *(M, both)*
- [x] **E2 — Progress + elapsed.** step N/max bar + a running elapsed timer
  (`started_at`/`elapsed` off `build_started`/`build_step`). *(S, both — `run.py`, FE)*
- [x] **E3 — Park/thrash surfacing.** `error_parked` handled — shown as a "Parked — needs
  you" notice on the build header with a per-error waive, not a silent build-end. *(S, both)*

## Epic F — Chat front door — ✅ DONE

- [x] **F1 — Stream chat responses + tool progress.** `chat.py` SSE stream
  (`text/event-stream`) consumed by `ChatPanel` — streamed tokens + per-tool
  "Drafting the spec…" progress lines replace the bouncing dots. *(M, both —
  `api/routers/chat.py`, `frontend/src/components/ChatPanel.tsx`)*
- [x] **F2 — Chat→build continuity.** A chat-created run's `spec_proposed` event
  auto-switches to the games view and focuses the run — no tab switch + manual Refresh
  (`Layout.tsx`). *(S, both)*

## Epic G — Delete the old UX (last) — ✅ DONE

- [x] **G1 — Rip out the old surface.** The old Scenes/Contract artifact tabs and the
  log-as-primary body are gone — the browser is the primary run view. The pause-to-edit
  gate on content editing is removed (the browser edits any component mid-build per C3;
  compile/package/download still wait for the executor to park, a real write-race guard,
  not an edit gate). *(M, FE — `GamesPanel.tsx`)*
- [x] **G2 — Remove dead wires.** The never-fired `awaiting_human` UI/handler and the
  manual games-list Refresh are gone — the list and board update off C4/C5's live events. *(S,
  FE + BE)*

---

## Not in scope (settled out)

- **No `awaiting_human` state, no blocking/subtree-gating, no fingerprints.** Dirty
  is an error; the loop auto-rewrites; the human pauses explicitly if they want to be
  waited on. (See `hitl_architecture.md` §Settled policy.)
- **No `dirty` field on component schemas** — it's a sidecar in the `human` store.
