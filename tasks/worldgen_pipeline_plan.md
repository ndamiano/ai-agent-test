# Worldgen pipeline — integration plan

The lab proved the recipe end to end (2026-08-08, coastal-fishing-town cell). It is not
perfect; it is much better than what `compose_scene` ships today. This doc is the plan to
integrate it. Lab evidence and settled facts live in the appendix — don't re-learn them.

Lab code: `eval/worldgen/` (mesh_sprite.py, sprite_overlay.py, llm_constraints/arm.py, arms)
and `eval/worldgen/lab_scripts_20260808/` (session scripts + `town_cell/`, the whole working
cell with every intermediate; scripts hardcode a dead `/tmp/wb_full_town/` path — point
CELL_DIR at `town_cell/`). Sprite/mesh cache: `eval/worldgen/sprites/` (q3d_*.png + *.glb).
Lab code and renders stay OUTSIDE git — the service repo carries only service code; results
go to docs/experiments.md (battery entry 2026-08-08) and the cache moves to durable storage
when the store lands (until then it is disk-only: back it up before touching the box).

## Scope: local cell only

This pipeline renders ONE CELL — a single screen-scale map with walkable truth. World
structure (stitched cells, edge contracts, interiors behind doors, travel) is explicitly
OUT: global is a future producer of stage-1 inputs (anything emitting blockout.json +
scene.json slots in — possibly the resurrected `src/worldgen/`), not a change to this plan.

Not 2D-only either: blockout.json + scene.json + the store are renderer-agnostic. A 3D game
takes `mesh.glb` at solver positions and the terrain grid, and skips stages 3–5 (the 2D
projection). Only the tail of the pipeline commits to 2D.

## The pipeline (what gets integrated)

1. **Plan → solver → blockout** — qwen3.6 plans terrain zones + placeables + relations
   (counts, kinds, no positions); solver places, roads by construction, emits
   `blockout.json` (terrain grid + named boxes) and `scene.json` (walkable truth).
2. **Object sprites via 3D** — per unique TYPE: Qwen-Image 2512 subject render (~20s) →
   TRELLIS2 image→3D → GLB → headless three.js orthographic render, one shared camera
   (~12s). Store hit skips the whole leg.
3. **Terrain pass** — bare painted ground through DreamShaperXL Turbo img2img,
   DifferentialDiffusion strength map. Style words only.
4. **Composite** — pure code (`sprite_overlay.compose`), bottom-anchored, back-to-front.
5. **Embedding pass** — Qwen-Image-Edit-2511 sits objects into terrain, preserving
   where/what/size. The only pass that both blends and preserves.

Each stage talks to the next through a file (blockout.json / subject PNG / GLB / sprite PNG
/ ground PNG / final PNG) — any leg swaps independently as models improve. Camera params
live in code, never in a model.

## Integration shape

**Surface stays `compose_scene`.** The build tool keeps its face (bake a map into the game
folder: ground image + scene json); this pipeline replaces what's behind it. Today's bake is
synchronous pure CPU; the new one needs GPU stages, so it becomes ASYNC like `generate_media`:
the tool answers immediately with the paths the files will land at
(`assets/<id>_ground.png`, `<id>_scene.json`), and `scene.json` — the walkable truth the
model wires collision from — is written SYNCHRONOUSLY at call time from the solver
(stage 1 is a small llm job + pure code; see open question below). The picture arrives like
art does.

**Transport is the existing queue, billing comes free.** Every inference stage is a job:
llm queue (plan call), image queue (subject renders, terrain, embedding), mesh queue
(TRELLIS). `enqueue_job` admits against the game's compute budget at enqueue; the debit
lands at completion — only delivered work is billed. Chained jobs inherit `game_id` via
`metadata.then` (the mesh chain already works this way). NOTHING new to build for metering;
just always enqueue with the run's game_id.

**Orchestration is a job chain, not a resident loop** — same law as builds. Register the
continuation names in `asset_chain` (or a sibling `scene_chain`): subject→mesh→sprite-render
per type, fan-in when all types land, then terrain → composite (CPU) → embedding → final
save + re-stage. The fan-in is the one new shape: today's chains are linear per asset;
this one waits for N types before compositing. Batch bookkeeping (`batch_id`) exists —
finalize-when-batch-empties is the precedent.

**Safety seams unchanged**: every image render passes `_admit` (NSFW verdict, fail closed);
the plan call is a normal llm job. No new trust boundaries.

## Work items (order = dependency order)

1. **Battery run** — DONE 2026-08-08 (`eval/worldgen/generalization_battery.py`, results in
   `eval/worldgen/battery_20260808/`): 6 biomes (river, desert, volcanic, snow, forest,
   farm), 6/6 end to end unattended, zero pipeline failures, ~35 min wall-clock, 26 new
   types deposited to the sprite cache. The RECIPE generalizes: solver layouts, terrain
   differentiation, contact shadows and object identity all held in every biome. What
   failed is content, not pipeline, and each failure lands in an existing work item:
   - **Type resolution is biome-blind** (store item): every "house/hut/quarters" in every
     biome collapsed to the one cached red-roof cottage — desert, volcano and snow all got
     it. 26 of 32 types also fell through the keyword table to raw flavor names. The store
     key needs a style/biome dimension (or reskin carries it) and resolution needs to be
     smarter than keywords.
   - **Scale lies multiplied** (polish item 1): telegraph-pole lamps, a barn-sized well.
   - **Forest cells have no trees** (new polish item): "forest" paints as flat dark green;
     the solver treats forest as terrain when it needs a tree-scatter rule.
   - **Terrain style prompt can lose to the bare-ground paint** (polish item 3):
     river_village asked lush green, denoise 0.55 kept the mud.
   - **Sparse density**: big empty stretches in most cells.
2. **Asset store** — promote `eval/worldgen/sprites/` to a platform store. Bigger than a
   cache; it is a shared platform system. The cache core: entry = type key →
   `{subject.png, mesh.glb, sprite.png, meta.json}`, shared across games (a game references
   entries, never owns them), every intermediate kept — dropping one forces re-paying the
   stage upstream of it. Store hit = zero-GPU object leg. On top of that, the store owns:
   - **Type resolution** — what maps a plan's flavor name ("The Salty Dog Inn") to a store
     key. The lab's keyword table already failed generalization on the first battery
     (26 of 32 types fell through to raw flavor names, 2026-08-08). Real answer is
     probably a small llm normalization call or embedding match, not keywords — and it
     decides the store's hit rate, which decides its whole value.
   - **Quality admission** — a bad mesh poisons every future game that hits it. A defect
     check on deposit (`check_render` spirit: broken, never bad) plus the admin gallery
     showing store entries; a human can evict a lemon.
   - **Model-stamp invalidation** — `meta.json` records which model produced each piece so
     an upgrade invalidates exactly its own leg (new subject model → regen subjects, keep
     GLBs whose subject didn't change; new camera → re-render sprites only).
   - **Concurrency** — two builds missing the same type at once must not render it twice
     or corrupt an entry; deposit is claim-then-fill like the job queue.
   - **Storage lifecycle** — the long-term plan: S3 (DO Spaces, S3-compatible — the
     existing `tools/s3.py` SigV4 client and the settings `s3` block work as-is) is the
     store's AUTHORITY; the droplet holds an LRU hot cache under data_dir with a disk
     watermark (~10GB), same verified-remote-before-evict rule as archive.py. The store
     is touched only at BUILD time (games get copies baked into their folder, /play never
     reads it), so a cache miss costs seconds of build wall-clock, invisible to players.
     Sizing: ~10MB/type → 1,000 types ≈ 10GB local, 25,000 ≈ one $5/mo 250GB bucket.
     Deposit path: pod render → control plane save (where `_admit` runs) → local + Spaces
     upload, marked verified. Store v1 may ship droplet-local — the entry format is what
     must be right first; where bytes sleep is swappable behind it. If the 35GB droplet
     pinches before the store exists, the lever already built is run eviction
     (archive.py --evict).
   - **Safety** — deposits pass the same render-verdict screen as every save; a store
     entry reaches many games, so fail closed matters more here, not less.
3. **Scene chain** — the continuation names + fan-in described above. Includes the
   image↔mesh VRAM handoff: ComfyUI holds ~27GB with Qwen loaded and TRELLIS OOMs behind
   it, so on a shared card all image work → `POST /free {"unload_models":true,
   "free_memory":true}` → all TRELLIS work. On the autoscaled prod shape (one card per
   queue) this is already solved by topology; the handoff matters for the single-box dev
   card.
4. **compose_scene rewire** — swap the bake behind the tool; sync scene.json, async
   picture. Preserve the tool's contract as the model sees it (paths answered immediately,
   PENDING read semantics).
5. **Drift check** — `scene.json` is solver truth but the picture passes through two
   diffusion passes after composite. A cheap post-embedding check (same spirit as
   `check_render`: compare object footprints against pre-embedding composite, record a
   `defect`, never act) so a doorway the edit pass moved is VISIBLE. Detects broken,
   never bad.
6. **Reskin per game** (after store exists) — keep the GLB, restyle only the 2D sprite
   render with a per-game style pass (edit model or img2img over the orthographic render;
   geometry and perspective already locked). Pays seconds instead of the subject→TRELLIS
   chain. UNTESTED — validate before the store advertises it.
7. **Height** — the maps are flat, and height is the biggest remaining believability gap.
   Code owns it end to end: blockout grows a `height_grid` beside `terrain_grid`
   (candidate producer: the unwired `src/worldgen` heightfield), quantized terraces with
   cliff edges + ramps by construction, walkable truth extends from height diffs
   (cliff edge unwalkable, ramp walkable), scene.json carries per-cell height. Visually:
   2D draws cliff-face strips code-first (terrain pass repaints material — cliffs join
   the protect mask like roads) and sprites paste with an elevation y-offset; 3D gets it
   nearly free (heightfield is the ground mesh, GLBs sit at x, y, height). Known risks:
   building placement wants flat footprints (solver constraint), and whether denoise 0.55
   respects painted cliff strips — both lab-testable. SEQUENCING: decide the height_grid
   format at item 4 (rewire), BEFORE blockout.json calcifies into compose_scene consumers
   — even if the visual passes land later.

**Open question for item 4**: stage 1's plan call is inference, and today's compose_scene
answers synchronously inside a build turn. Either the whole tool call becomes async
(scene.json also lands later — the model must then read it PENDING-style before wiring
collision), or the plan call rides the build's own llm turn budget synchronously. Decide at
item 4, informed by how builds actually consumed scene.json in the battery.

## Visual polish (post-integration, not gating)

- **Per-kind scale table** (pure code) — barrels/lamps/crates render building-scale;
  replace uniform VISUAL_SCALE 1.4 with world heights per type (barrel ~0.5 cell,
  lamp ~1.5, crate ~0.5, boat ~1.5, building from box). Biggest visual lie left.
- **Landmark sizing** — lighthouse gets cottage box; landmark kind needs bigger footprint,
  taller aspect, maybe ~30° camera for tall types (45° squashes towers).
- **Terrain palette** — olive-drab grass; friendlier green; tuft variety; the style
  prompt must beat the bare-ground paint (river_village stayed mud at denoise 0.55).
- **Forest scatter** — forest terrain cells need a tree-scatter rule (code), not a flat
  band; battery's forest_camp read as lawn.
- **Density** — most battery cells carry big empty stretches; solver should scatter more
  fabric (decorations, ground clutter) into open ground.
- **Bottom-edge artifact** — edit model smears last ~40px both runs; pad-then-crop.
- **Embedding strength** — grass-over-foundation effect faint; push instruction or accept.

---

## Appendix: settled lab facts (don't re-learn)

### Stage recipes as validated

- **Subject prompt** (Qwen-Image 2512, NOT NetaYume): "A single {phrase}, standing upright
  on the ground, one isolated object centered on a plain pure white background,
  three-quarter view seen from slightly above, clean video game asset, bright even studio
  lighting, whole object fully visible" — euler 20 steps cfg 2.5, ModelSamplingAuraFlow
  shift 3.1, BiRefNet matte, autocrop. "Standing upright" fixed the sideways barrel; keep
  it. NetaYume subjects were the root cause of every earlier mesh failure (pot/blob/table);
  Qwen subjects fixed all four test objects with zero TRELLIS changes.
- **Sprite render**: TRELLIS2 (server port 8189) → GLB → headless three.js orthographic,
  45° elevation (`eval/worldgen/mesh_sprite.py`; playwright chromium
  `--use-gl=angle --use-angle=gl-egl`, vendored three + GLTFLoader + BufferGeometryUtils).
  One camera for every object = perspective consistency by construction.
- **Terrain**: DreamShaperXL Turbo img2img, denoise 0.55, 8 steps cfg 2.5 dpmpp_sde/karras,
  DifferentialDiffusion strength map: water/road/path/sand/dock/plaza 0.45, open ground
  1.0. Style-only prompt (grass/dirt/beach words, "no objects, empty ground"; negative
  carries buildings/houses/objects). ~20s.
- **Embedding**: Qwen-Image-Edit-2511 (fp8mixed, Apache 2.0, on disk;
  `TextEncodeQwenImageEditPlus`, same VL encoder + qwen VAE as 2512, shift 3.1, euler 20
  cfg 2.5, denoise 1.0 — the edit conditioning carries the image). Instruction: sit objects
  in terrain, grass slightly overlapping bottom edges, soft contact shadows, worn dirt at
  doorways, keep every object where/what/size it is. ~3min full-res (2304×1728).

### Denoise ladder

- 0.15/0.25/0.35 whisper over composite: objects safe, ground stays mud. Useless alone.
- 0.45: painterly ground arrives + market clutter emerges, but thin/dark objects drift
  (lamps→telegraph poles, lighthouse→vat). Protection masks at 0.2 effective didn't save
  them. The edit model is the only pass that both blends and preserves — this ladder is
  the acceptance test for any replacement.
- Content nouns in any repaint prompt summon content (measured: second lighthouse, market
  clutter). Style words only.

### Model roster

- **Qwen-Image 2512** (Apache) — object/scene/subject generation; the mesh chain's image
  leg. NetaYume stays for characters only.
- **Qwen-Image-Edit-2511** (Apache, fp8mixed 20GB) — scene blending / embedding.
  Drift-free instruction editing.
- **DreamShaperXL Turbo** — terrain/texture img2img (real cfg, negatives live).
- **TRELLIS2** — image→3D. Text-to-3D (TRELLIS v1) investigated and rejected: image stage
  is the chain's strength, v1 would inherit the weak stages with weaker geometry.
- **FLUX.2 klein 4B** (Apache) — untested candidate, reference editing; only if Qwen falls
  short somewhere.
