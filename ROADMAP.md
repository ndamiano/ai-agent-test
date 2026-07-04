# Maestro Roadmap

Goal: user says "make me a game" → an hour later, a good game exists. AI quality is the product. Everything else is scaffolding.

The forward plan — what we still want to build. For what exists today and how it works, see `CLAUDE.md` (architecture + code standards) and `src/maestro/README.md` (the build system).

---

## Build quality + scale

- [ ] **Automated prompt optimization** — LLM-as-judge scorer + hill-climbing loop. Removed in the rebuild; eval currently grades finished artifacts only (`eval/cli.py score game`). Prompts are kept as swappable `.txt` files so climbing can return.
- [x] **Parallel fixes** — slot-guarded creates (nodes/places/abilities/combatants/encounters) batch up to `parallel_fixes` concurrent LLM calls, each pinned to its own slot; tool writes serialize on a lock. Edits/crossref/human notes stay serial (shared targets). Asset-generation concurrency still open.
- [ ] **Per-character dialogue agents** — replace the single content author with an orchestrator + one agent per character, each holding only its character's context. Long-term: try different models per character to match voice/capability to role. Validate via eval before/after.
- [x] **Combat authoring module + Godot auto-routing** — `maestro/modules/combat.py` is a selectable mechanic-module that authors the `combat` component (`combat.json`: stats/statuses/abilities/combatants/encounters) ONE slice at a time in dependency order: `set_combat_meta` lays the stat system, then `write_ability`/`write_combatant`/`write_encounter` grow the list slices, each id-merged and validated against the already-declared upstream ids at write time (the per-slice validators in combat.py; `v_combat` composes them as a whole-doc backstop). `get_errors` stages count targets (`min_abilities`/`min_combatants`/`min_encounters`), each fanned into per-slot create-errors (`checks.slot_errors`) so the outer loop authors ONE slot-guarded item per step, not the whole block (the monolithic one-shot was too hard: a single bad field re-authored ~8–10k tokens). Every `v_combat` id check is type-guarded (a mis-typed value yields an actionable message, never a `TypeError`). `assemble_ir` lifts the component (presence-driven) and `(godot, combat)` is registered while renpy is not, so `Module.engine_for` auto-selects Godot for any spec that picks combat. combat owns its slices' crossref refs (world's terminal skips them). `real_time_sim` substrate (designed, not built) remains separate.
- [x] **Walkable Godot overworld** — RPG places (`world_map/town/interior`) now play as a WASD/arrow-key tile grid (`godot/runtime/overworld.gd`): the avatar steps cell-by-cell (bounded by the new `place.grid` + `place.impassable` walls), walking ONTO a `move`/`start_combat` tile fires it (zone transitions carry `move.spawn`/`start.spawn` arrival cells), and `talk/examine/take/use/win` fire on E. `Game.gd` selects the presenter per place via a `PRESENTERS` registry keyed on `kind` (`room`=>pnc) and exposes one shared `run_action` both presenters call, so a new navigation modality is a presenter + a registry entry, not a router edit. `docs/examples/combat_game.json` is the hand-authored showcase: three zones, walls, the full combat spec (costs/scaling/status/AoE/world-bridge), and inventory↔combat (a potion taken on the map, drunk in a fight via an ability gated on `requires:{item}` that consumes it). NOTE: the `world` authoring module does not yet emit `grid`/`impassable` — authoring walkable maps from a spec is the follow-up; the showcase is hand-authored IR.
- [ ] **Game IR 0.2 — first-class `locations` with STATES** (PROMOTED — asset monotony is a ship-blocker, One Last LAN post-mortem 2026-07-03) — promote location to its own entity `locations: [{id, name, description, states: {<name>: description}}]` where states are story-defined (day/night/evening, busy/empty, before/after). A node references `location + state`; assets generate one image per state, **img2img off the location's base image** so every state is recognizably the same place. `node.location` repoints bg id → location id; PnC `places[].background` converges on the same concept. Companion: the assets module must demand **CGs at story peaks** (climax/ending beats) — schema + build_cg_job exist, generation never authors them. Ground truth to retrofit first: `one-last-lan` (4 room states + 3-4 CGs).
- [ ] **Better local TTS** — kokoro is insufficient even correctly cast (voice casting by card sex/tts_voice shipped 2026-07-03). Evaluate higher-quality local options; per-character voice identity + a narrator voice are already wired.

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
