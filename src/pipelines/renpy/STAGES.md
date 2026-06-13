# renpy — Stage Reference

This pipeline produces a complete branching Ren'Py visual novel from a one-paragraph user brief. Eight stages run in dependency order. JSON-producing stages use a shared `_call_json` helper with retry-and-correct; `node_scripts` makes one plain-text LLM call per dialogue slot.

**The core design:** the story is planned top-down. The premise states a *central question* (a values tension with multiple defensible answers); endings are written first as distinct *answers* to that question; then the scene outline is written trunk-first with every prior scene in view. The branching graph is *assembled* from that outline — structure follows story, never the reverse.

---

## Stage 1: `premise` — Story Bible (LLM)

**File:** `fns.py` → `generate_premise()`  
**Inputs:** `brief.json`  
**Output:** `premise.json`  
**Prompts:** `prompts/premise.txt` + `prompts/voice_sheet.txt` (one call per character)

Generates the full story bible: the central question, characters, setting, tone. This is the canonical source of truth that every downstream stage references.

Runs in two passes: the premise JSON call produces everything including a one-line `voice` seed per character, then one plain-text call per character (`voice_sheet.txt`) expands that seed into a full multi-section voice sheet stored as `voice_mechanics`. Decomposed because full sheets inside one JSON call truncate on small models.

**The brief is binding.** The premise must be recognizably the story the user asked for — the prompt explicitly forbids grafting on villains, deadlines, or conspiracies the brief didn't imply.

**The central question** is a values tension with at least two defensible answers ("is holding the family together worth pretending nothing is changing?"), never a logistics problem ("can she finish in time?"). It is generation scaffolding — invisible in the output — and exists so that "endings must be fundamentally different" becomes checkable: each ending answers it differently.

**Characters from interiority, not function.** No role enum, no relationship-to-protagonist field, no thematic duty. Each character is `identity` + `situation` + `charge` (what's unresolved or magnetic — a want, lost purpose, secret, contradiction, pressure, or sheer vividness). Quality is enforced by the **Grimgong test** in the prompt: the situation must read as one particular person, not an archetype with adjectives.

**Template variables:** `genre`, `tone`, `setting`, `notes`, `character_count`

**Output shape:**
```
{
  premise: "one paragraph: who, what situation, why this moment is worth a story",
  central_question: "the question the story argues",
  protagonist_id: "char_id",
  tone_directives: [{ adjective, explanation }],   // 3-5 entries
  setting: { name, physical_description, rules, atmosphere },
  characters: [{
    id, name, identity, situation, charge,
    voice,                // one-line seed
    voice_mechanics,      // full sheet, added by voice_sheet.txt pass
    appearance, appearance_tags
  }]
}
```

**Post-processing (Python):** Assigns `id` from name if missing; assigns a display `color` to each character for Ren'Py's text box.

---

## Stage 2: `story` — Top-Down Outline (LLM, 4 calls)

**File:** `fns.py` → `generate_story()`  
**Inputs:** `brief.json`, `premise.json`  
**Output:** `story.json`  
**Prompts:** `prompts/story_endings.txt`, `prompts/story_spine.txt`, `prompts/story_scenes.txt` (×3: trunk + each arm)

Writes the whole story top-down, in branch-and-bottleneck shape: a common trunk ends at the **commitment choice** (the first moment the protagonist must commit to one approach to the central question); each option opens an **arm** (a distinct strategy with its own mini-arc); arms land on their assigned endings, via a **crisis choice** when an arm owns more than one.

**Call order — why it prevents the repeated-scene failure:**
1. **Endings** — each ending gets an `answer` field: its position on the central question. Two endings with the same answer are the same ending wearing different clothes; the answer field makes that checkable.
2. **Spine** — designs the commitment choice and partitions endings across the two arms. Python repairs invalid partitions (`_repair_spine`).
3. **Scenes** — trunk first, then each arm *with every previously written scene in the prompt* and the rule "no scene repeats a conflict." Context flows down the story, not up a random graph — the old backward-fill failure (six paraphrases of the same scene) cannot survive a writer who can see what's already written.

Each scene declares `scene_type` (confrontation / revelation / respite / complication / decision — no two consecutive alike), `whats_new` (a scene that changes nothing is cut before it's written), location, cast, tone. Scene casts are *collisions*: who's in a scene follows from whose unresolved business intersects the protagonist's path, not from assignment.

**Sizing from brief:** `num_endings` (default 4), `min_good_endings` (2), `depth` (6) → trunk = `depth // 2` scenes, arms = `depth - trunk - 1` scenes each.

**Output shape:**
```
{
  central_question,
  endings:           [{ id, end_type, answer, title, summary, tone,
                        location_id, characters_present, key_image_moment }],
  commitment_choice: { situation, options: [{ label, strategy, ending_ids, crisis_labels }] },
  trunk:             [scene...],
  arms:              [{ label, strategy, ending_ids, crisis_labels, scenes: [scene...] }]
}
// scene = { summary, scene_type, whats_new, when, location_id, characters_present, emotional_tone }
```

The stage runs `assemble_story()` on its own output before returning, so a structurally broken outline fails inside the stage and is retried.

---

## Stages 3+4: `graph` + `beat_map` — Deterministic Assembly (no LLM)

**File:** `graph.py` → `assemble_story()`, called by `fns.py` → `graph_from_story()` / `beat_map_from_story()`  
**Inputs:** `story.json`  
**Outputs:** `graph.json`, `beat_map.json`

Pure Python. Converts the outline into the two shapes downstream stages consume — node ids (`root`, `beat_NNN`, `branch_NNN`, `ending_NNN`), parent/child links, `reachable_endings`, topological order; and a per-node beat map (`summary`, `dramatic_purpose` ← `whats_new`, `scene_type`, `when`, location, cast, tone, `choice_labels` on branches; endings pass through verbatim). Node types: `root` / `beat` (linear) / `branch` (menu) / `ending` (return).

---

## Stage 5: `node_scripts` — Write Ren'Py Scripts (LLM, one call per dialogue slot)

**File:** `fns.py` → `write_node_scripts()`  
**Inputs:** `premise.json`, `graph.json`, `beat_map.json`  
**Output:** `node_scripts.json`  
**Prompts:** `prompts/scene_sketch.txt` (scene plan), `prompts/character_line.txt` + `prompts/character_line_system.txt` (dialogue), `prompts/narration.txt` + `prompts/narration_system.txt` (narration)

For every node in topological order, generates the scene content slot by slot. Python owns all Ren'Py syntax (label, scene, show, menu, jump); the LLM only ever writes a single line of dialogue or narration per call.

**Scene sketch:** One JSON call per node plans the scene before any line is written: an ordered list of `{speaker, intent, emotion, register}` (8–14 lines, narrator used 1–3 times) plus per-scene `character_objectives`. This gives the scene a conversational arc — questions get answered, escalation builds, the final lines steer into the exit. If the sketch fails or returns fewer than 4 valid lines, slot assignment falls back to the old weighted-random scheme.

**Register:** Each sketched line is `plain` (default — everyday spoken words, no imagery) or `charged` (max 2 per scene — emotion breaks through and the character's signature register may surface). The per-line prompt converts this into an explicit instruction, which keeps figurative language rare instead of in every line.

**Scene position:** Each line call carries where the scene sits in the playthrough ("scene 4 of this playthrough — midday, after lunch", combining ancestor count with the scene's `when` field), so dialogue can't act like the day is ending in scene two.

**Story-so-far:** Each node's recap contains only beats from *guaranteed ancestors* — nodes on every root→node path (`_guaranteed_ancestors`, a dominator computation). Sibling-branch events the player may never have seen are excluded, so dialogue can't reference them.

**Exit funneling:** Each node gets an exit note — branch: "ends with the player choosing between: …"; beat: "flows into: <next beat>"; ending: "land the ending with finality." The sketch sees it, and the final 2 line calls see it directly.

**Character lines:** Each character gets a cached system prompt (`character_line_system.txt`) holding their voice sheet, immediate goal, subtext, the premise, tone, and the other cast members. The per-slot user prompt (`character_line.txt`) carries the beat summary, the line's intent and emotion from the sketch, the character's per-scene objective, recent dialogue history (last 10 entries, display names), and the story-so-far recap. `*asterisk*` segments in the response become italic `act` action lines; speech is split at sentence boundaries so no text box exceeds 240 characters.

**Narration:** Same pattern via `narration_system.txt` / `narration.txt` — sketch intent included; opening narration establishes the scene instead of reacting.

**Assembly (Python, no LLM):** `label {node_id}:`, `scene bg_x with dissolve`, `show` for up to 3 present characters, then the generated lines, then the ending instruction — `menu:` with choice labels for branch nodes, `jump` for linear nodes, `"The End." / return` for endings.

**Single-scene dev CLI:** Regenerate one node from an existing run with current prompts — no full pipeline rerun. From `src/`: `python -m pipelines.renpy.scene <run_dir>` lists nodes; `python -m pipelines.renpy.scene <run_dir> <node_id>` prints the sketch and the regenerated script. Tweak a prompt, rerun the same node, compare.

---

## Stage 6: `asset_manifest` — Art Direction + Image Descriptions (LLM)

**File:** `fns.py` → `generate_asset_manifest()`  
**Inputs:** `premise.json`, `beat_map.json`  
**Output:** `asset_manifest.json`  
**Prompt:** `prompts/asset_manifest.txt`

Produces image generation prompts for every visual asset the game needs: background illustrations, character portraits, CG (cinematic) illustrations for ending key moments, and a title card.

**Template variables:** `premise`, `setting`, `tone`, `characters`, `location_summary` (locations + which beats happen there)

**LLM output:**
```
{
  art_direction: { style, palette, lighting },
  backgrounds: [{ id, name, description }],
  cgs: [{ id, description }],
  title_card: { description }
}
```

**Post-processing (Python):**
- Ensures all background IDs have `bg_` prefix
- Fills in any locations the LLM missed (derived from beat_map)
- Characters are always derived from premise, not LLM (reliable data)
- CGs get `cg_` prefix enforced
- Title card gets a fallback description if LLM omits it

---

## Stage 7: `images` — Generate Images (no LLM prompt)

**File:** `fns.py` → `generate_images()`  
**Inputs:** `premise.json`, `asset_manifest.json`  
**Output:** `images_result.json`

Builds one ComfyUI job per asset in the manifest (backgrounds, character sprites, CGs, title card) and runs them as a batch. The descriptions written in stage 6 become the image prompts. Failed generations fall back to solid-color placeholder PNGs so the build never blocks on image errors.

---

## Stage 8: `build` — Assemble the Game

**File:** `fns.py` → `build()`  
**Inputs:** `brief.json`, `premise.json`, `asset_manifest.json`, `node_scripts.json`  
**Output:** `build_result.json`

Stitches all generated scripts into a single `script.rpy` file, writes the Ren'Py project structure, runs final lint if SDK is available, and distributes the game.

**Steps:**
1. `_validate_and_repair` — patches invalid background/character references across all scripts
2. `_postprocess_script` — cleans each script (strips invalid speaker names, etc.)
3. `_stitch_script` — concatenates scripts in topological order, prepends character definitions and splash screen
4. Writes `game/script.rpy` and copies template files (screens.rpy, gui, etc.) — binary gui/ assets are gitignored, so missing templates are seeded from the SDK's stock project (`the_question`) when an SDK path is configured
5. Copies title card to `gui/main_menu.png`
6. If Ren'Py SDK path is set: runs `renpy lint`, then distributes (packages the game)

---

## Data Flow Summary

```
brief.json
    │
    └──→ [premise]    → premise.json         (story bible + central question)
              │
              ├──→ [story]     → story.json  (endings → spine → scenes, top-down)
              │         │
              │         ├──→ [graph]     → graph.json     (assembled DAG, no LLM)
              │         └──→ [beat_map]  → beat_map.json  (per-node beats, no LLM)
              │
              ├── graph.json + beat_map.json ──→ [node_scripts]  → node_scripts.json
              │                                                     (Ren'Py script per node)
              ├── beat_map.json ──→ [asset_manifest]             → asset_manifest.json
              │                                                     (image prompts)
              └── asset_manifest.json ──→ [images]               → images_result.json
                                                                    (generated art)

brief + premise + asset_manifest + node_scripts ──→ [build]     → game on disk
```

---

## Climbable Prompts

| Stage | Prompt file |
|---|---|
| premise | `prompts/premise.txt` (registered) + `voice_sheet.txt` |
| story | `prompts/story_scenes.txt` (registered) + `story_endings.txt`, `story_spine.txt` |
| node_scripts | `prompts/character_line.txt` (registered) + `scene_sketch.txt`, `character_line_system.txt`, `narration.txt`, `narration_system.txt` |
| asset_manifest | `prompts/asset_manifest.txt` |
| graph / beat_map | no prompt (deterministic assembly) |
| images | no prompt (ComfyUI jobs) |
| build | no prompt (assembly) |

Stages with multiple prompt files use the registered file by default; climb any other file with `--prompt-file`:

```bash
python eval/cli.py climb renpy/story --brief renpy_romance --prompt-file story_endings.txt
python eval/cli.py climb renpy/node_scripts --brief renpy_romance --prompt-file scene_sketch.txt
```
