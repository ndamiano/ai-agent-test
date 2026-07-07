# Finished — shipped work ledger

Append-only. When a **section** of a task file goes all-`[x]`, cut it from the active file and
drop a one-line entry here (newest at top). Keeps active task files free of dead checklists while
preserving the record. One line each — git has the detail.

Format: `- **YYYY-MM-DD** — <what shipped> (<key files/settings>)`

---

## Build system + engines

- **2026-07-05** — 3D gen model: Hunyuan3D-2.1 image→mesh, then TRELLIS.2-4B won the bake-off (native PBR, MIT); selectable `settings.comfyui.mesh_backend`, TRELLIS standalone server `tools/trellis_server.py`
- **2026-07-05** — 3D spec-selectable: proposer authors `presentation` (2d/hd2d); `_resolve_presentation` forces godot on hd2d+world; mesh gen gated on hd2d
- **2026-07-05** — HD-2D 3D presenter: `godot/runtime/overworld3d.gd` renders the same game.json in true 3D (presentation-neutrality proof)
- **2026-07-05** — progression decoupled from combat: `level_var`/`per_level` IR primitive (any effect feeds the pool); `wild_encounters` extracted to a thin selectable module
- **2026-07-04** — depth: progression + wild encounters (IR `progression`, `combatant.xp_yield`, `place.encounter_table`; persistent player combatant carries damage/XP/levels across fights + saves)
- **2026-07-04** — walkable Godot overworld: WASD/arrow tile grid, `map_builder.py` deterministic rasterize (region plan → tiles/anchors/footprints), presenters registry keyed on `place.kind`, ideogram4 tile pipeline, combat battle UI, player chrome (title/pause/save/ending/inventory)
- **combat authoring module + Godot auto-routing** — `maestro/modules/combat.py`, one slice per step in dependency order; `(godot, combat)` projection auto-selects Godot via `Module.engine_for`
- **parallel fixes** — slot-guarded creates batch up to `parallel_fixes` concurrent LLM calls, each pinned to its own slot; tool writes serialize on a lock

## Generation quality + prompts

- **outline / beat-sheet stage** — outline component between premise and nodes: logline + ordered beats (purpose + tension) + an `ending_paths` entry per premise ending; ≥5 beats forced; injected into node authoring context
- **prompt DRY** — `{{include:NAME}}` partials in `render_template` (`tool_call_rule`, `conditions_ref`, `effects_ref`, `derive_from_request`); skeleton shape single-sourced from `SKEL_*`
- **spec prompt single-source** — one `propose_spec.txt` generated from the composed module set; `propose_spec_pnc/card.txt` deleted; baselines are the single contract source
- **tool gating onto `Module`** — `mode_tools`/`mode_prompt`/`prompts`/`target_*` moved off `agent.py` globals; adding a module needs no `agent.py` edit
- **context-bloat trims** — per-mode `skeleton_guide` scoping, upstream trimmed to used fields, SPEC block scoped/dropped in the render path
- **born-compliant write fixes** — VN character floor relaxed to min 2; `each_node_has_location` check; `classify_genre` emits `card_ante`; narration-alias speakers normalized to null at write time
