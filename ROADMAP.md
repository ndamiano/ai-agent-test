# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

The forward plan — what we still want to build. For what exists today and how it works, see `CLAUDE.md` (architecture + code standards) and `src/maestro/README.md` (the build system).

---

## Build quality + scale

- [ ] **Automated prompt optimization** — LLM-as-judge scorer + hill-climbing loop. Removed in the rebuild; eval currently grades finished artifacts only (`eval/cli.py score game`). Prompts are kept as swappable `.txt` files so climbing can return.
- [ ] **Parallel fixes** — the loop authors one target at a time; run independent targets (separate nodes, asset generations) concurrently where they share no state.
- [ ] **Per-character dialogue agents** — replace the single content author with an orchestrator + one agent per character, each holding only its character's context. Long-term: try different models per character to match voice/capability to role. Validate via eval before/after.
- [ ] **Combat authoring module + Godot auto-routing** — the Godot engine now RENDERS `turn_based` combat (encounters/abilities/stats/statuses the IR already models) via `godot/runtime/combat.gd`; web/renpy still stub it. Today Godot is forced-only (appended last in `ENGINE_TAGS`, never auto-selected) and combat is proven by feeding a whole-IR dict. What's left: a selectable `combat` mechanic-module that authors encounters on disk + the `assemble_ir` lift of combat components + a `(godot, combat)` projection — then `engine_for` auto-selects Godot for any game that picks combat. `real_time_sim` substrate (designed, not built) remains separate.
- [ ] **Game IR 0.2 — first-class `locations`** — today a VN `node.location` points straight at an `asset_manifest.backgrounds` id (1:1 place↔image; the background is generated from that entry's description). Promote location to its own entity `locations: [{id, name, description, mood?, music?, time_variants?}]` so a place can carry day/night background variants, ambient music, and mood; the background image(s) generate *from the location*, and `node.location` repoints from a bg id → a location id. PnC `places[].background` should converge on the same location concept.

## Human-in-the-loop UX

- [ ] **Mid-build steering** — optimistic interjection ("I don't like this") via a director's note / steer-next-target; `revise_component` / `fork_run`; chat-spawned builds; an LLM quality-gate.
- [ ] **Close the chat↔games seam** — when a chat turn creates/amends a run (`propose_game_spec` / `amend_game_spec`), surface the new run inline in the chat reply (a "Spec ready — Review →" card / deep-link that selects the run in Games) and carry the run_id back to the client. Today the chat gives no signal, so the user must know to open Games and hit Refresh.
- [ ] **Post-compile refiner** — a hand-edit to a node leaves `story_state` stale; heal it after a manual edit/recompile (see `docs/quality_todo.md` §8).
- [ ] **Frontend improvements** — the chat + Games tabs work (browse runs, freeze a spec, kick a build, stream progress over the websocket); polish and extend from here.

## New artifact types — require composition

- [ ] **World building** — world bible → factions → nations/cities → characters, each layer its own sub-build. Eventual target: explorable artifact.
- [ ] **Long-form fiction / novel** — outline → chapters → continuity tracking across generations.
- [ ] **Comic / manga** — story → scenes → panels → dialogue + images per panel.
- [ ] **Music generation** — integrate with a music model (MusicGen, Suno API) for score / ambient.

## Accessibility
*Once quality and coverage are there, make it easy for everyone.*

- [ ] Auto-install and manage ComfyUI / the local model + TTS servers
- [ ] Model selection assistant (help the user pick the right model)
- [ ] Plugin / contribution system for third-party modules and engines

---

## North Star — VR World Generation

*"I want to explore a world where magic is real in VR" → hours/days later, a playable world.*

**Stages:**
1. **World bible** (LLM) — magic system, history, factions, geography, tone, key locations
2. **World layout** (LLM + procedural) — regions, cities, dungeons, roads, points of interest as structured data
3. **NPC generation** (LLM, sub-build) — who lives here, roles, dialogue trees, daily schedules
4. **Quest / story generation** (LLM) — main quest, side quests, random encounters, magic interactions
5. **Asset specification** (LLM) — enumerate every 3D asset needed: buildings, props, creatures, items
6. **Asset generation** (3D model) — mesh + texture per asset
7. **World assembly** (code generation) — Godot project files, scene layout, scripting, VR config
8. **Build + export** — Godot CLI build to VR-ready binary

**Hard dependencies:**
- Sub-build support — NPC generation, world layout, asset generation are each their own builds
- Text-to-3D model — current quality (TripoSR, Shap-E) is rough but improving fast; this is the main blocker
- Godot integration — scenes, scripts, assets assembled programmatically
- VR headset build pipeline — OpenXR export via Godot CLI

**What's achievable today:** world bible, NPC dialogue, quest outlines, asset specification — all LLM stages.
**The blocker:** 3D asset quality. Text-to-3D models are improving fast; revisit when output is usable.
**Engine target:** Godot (open source, scriptable, OpenXR support, closest to Ren'Py in programmatic project generation).
