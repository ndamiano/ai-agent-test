# renpy_vn_graph — Stage Reference

This pipeline produces a complete branching Ren'Py visual novel from a one-paragraph user brief. Eight stages run in dependency order; all LLM calls use a shared `_call_json` helper with a 3-attempt retry loop.

---

## Stage 1: `graph` — Build the DAG (no LLM)

**File:** `graph.py` → `generate_graph()`  
**Inputs:** `brief.json`  
**Output:** `graph.json`  
**Prompt:** none

Procedurally generates the branching structure of the story — a directed acyclic graph (DAG) — before any story content exists. The graph is built backwards: endings are created first, then branch and beat nodes are prepended layer by layer until a single root is reached.

**Node types:**
- `root` — single entry point, always present
- `beat` — linear scene, one child (no player choice)
- `branch` — choice point, two children
- `ending` — leaf node (good / neutral / bad)

**Parameters from brief:**
| param | default | meaning |
|---|---|---|
| `num_endings` | 4 | number of leaf endings |
| `depth` | 6 | approximate layers root→ending |
| `min_good_endings` | 2 | guaranteed good outcomes |
| `merge_probability` | 0.3 | per-layer chance adjacent paths reconverge |
| `seed` | none | optional RNG seed |

**Output shape:** `{ nodes, edges, topological_order, ending_ids, root_id }`  
Each node carries `reachable_endings` — which ending IDs can still be reached from this node. This is used by backward_fill to write beats that don't spoil dead ends.

---

## Stage 2: `premise` — Story Bible (LLM)

**File:** `fns.py` → `generate_premise()`  
**Inputs:** `brief.json`  
**Output:** `premise.json`  
**Prompt:** `prompts/premise.txt`

Generates the full story bible: characters, setting, tone, themes. This is the canonical source of truth that every downstream stage references. Nothing about the branching structure is known here — it's pure creative world-building.

**Template variables:** `genre`, `tone`, `setting`, `notes`, `character_count`

**Output shape:**
```
{
  premise: "one gripping paragraph (hook, stakes, urgency)",
  tone_directives: [{ adjective, explanation }],   // 3-5 entries
  setting: { name, physical_description, rules, atmosphere },
  characters: [{
    id, name, role, personality, voice, appearance, appearance_tags
  }],
  themes: ["action-driven thematic question"]       // 2-3 entries
}
```

**What makes this hard:** The `voice` field must be specific enough that an LLM writing dialogue later can impersonate each character distinctly without labels. Vague outputs ("speaks thoughtfully") degrade all downstream dialogue quality.

**Post-processing (Python):** Assigns `id` from name if missing; assigns a display `color` to each character for Ren'Py's text box.

---

## Stage 3: `endings` — Write Ending Beats (LLM)

**File:** `fns.py` → `generate_endings()`  
**Inputs:** `premise.json`, `graph.json`  
**Output:** `endings.json`  
**Prompt:** `prompts/endings.txt`

Writes the story content for every leaf node. The graph already determined how many endings exist and whether each is `good`, `neutral`, or `bad`. This stage fills in what actually happens at each ending.

**Template variables:** `premise`, `characters`, `ending_count`, `endings` (list of `{id, end_type}`)

**Output shape:**
```
{
  endings: [{
    id, end_type,
    title,               // short poetic title
    summary,             // 2-3 sentences: concrete events
    tone,                // one-word emotional register
    location_id,         // snake_case, no bg_ prefix
    characters_present,  // 1-3 char ids
    key_image_moment     // film-still description for CG art
  }]
}
```

**Why endings first:** Backward fill (stage 4) needs to know what each ending looks like so it can write beats that credibly lead toward multiple possible outcomes without resolving them prematurely.

---

## Stage 4: `beat_map` — Backward Fill (LLM, one call per node)

**File:** `fns.py` → `backward_fill()`  
**Inputs:** `premise.json`, `graph.json`, `endings.json`  
**Output:** `beat_map.json`  
**Prompt:** `prompts/backward_fill.txt`

Walks the DAG in reverse topological order (endings → root). For each non-ending node, writes a dramatic beat: what concretely happens, who is present, where it takes place, and — for branch nodes — what choice the player makes.

**Key constraint:** Each beat sees only its immediate children and the set of endings it can still reach. It must set up the transition to those children without resolving or spoiling any of the reachable endings.

**Template variables:** `premise_summary`, `node_id`, `node_type`, `is_branch`, `child_beats`, `child_ids`, `child_count`, `reachable_endings`, `available_characters`

**Output per node:**
```
{
  summary,          // one sentence: who does what to whom, stakes explicit
  dramatic_purpose, // one sentence: what player now knows that they didn't
  location_id,      // snake_case, reuse from children when possible
  emotional_tone,   // tense|melancholic|revelatory|hopeful|confrontational|tender|ominous
  characters_present,
  choice_labels     // one label per child, only on branch nodes
}
```

**Why this is the hardest stage:** Each beat must work as a stand-in for multiple narrative branches simultaneously. A bad beat is either too generic ("they talk about the plan") or too specific (it resolves something that should stay open). The `dramatic_purpose` field is the main quality signal: if it's vague, the beat is likely weak.

**Failure fallback:** If the LLM fails after 3 attempts, a placeholder beat is inserted so the pipeline doesn't abort.

---

## Stage 5: `node_scripts` — Write Ren'Py Scripts (LLM, one call per node)

**File:** `fns.py` → `write_node_scripts()`  
**Inputs:** `premise.json`, `graph.json`, `beat_map.json`  
**Output:** `node_scripts.json`  
**Prompts:** `prompts/node_script.txt` (user), `prompts/node_script_system.txt` (system)

For every node in topological order, writes the actual Ren'Py script: background commands, character show/hide, narration, dialogue, and the correct ending instruction (jump / menu / return).

**System prompt** (`node_script_system.txt`) contains the full story premise, tone directives, setting, and character profiles (personality + voice). This is injected once per node via a `PipelineAgent` (not shared across nodes — each node gets a fresh agent).

**User prompt** (`node_script.txt`) contains:
- The beat description from beat_map
- The node id, type, and background id
- Character variable declarations (`define char_id = Character(...)`)
- What comes next (jump target, menu options, or return)

**Output:** Valid Ren'Py script starting with `label {node_id}:`, 8-14 lines, ending with the correct instruction.

**Validation loop:** After generation, `_validate_scene_script` checks the script. On failure, the agent is prompted to retry up to 2 times with the error message. If still invalid, a minimal fallback script is inserted.

**Branch nodes:** The prompt specifies the menu structure; the LLM writes `menu:` with the player-facing labels and jumps to the correct child IDs.

**Ending nodes:** Prompt instructs `"The End."\n    return`.

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

Delegates to the existing `renpy` pipeline's image generation function. Calls an image generation API (Stable Diffusion / similar) for each asset in the manifest. The descriptions written in stage 6 become the image prompts.

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
4. Writes `game/script.rpy` and copies template files (screens.rpy, gui, etc.)
5. Copies title card to `gui/main_menu.png`
6. If Ren'Py SDK path is set: runs `renpy lint`, then distributes (packages the game)

---

## Data Flow Summary

```
brief.json
    │
    ├──→ [graph]      → graph.json           (DAG structure, no LLM)
    │
    └──→ [premise]    → premise.json         (story bible)
              │
              ├── graph.json ──→ [endings]   → endings.json      (leaf content)
              │
              ├── graph.json + endings.json ──→ [beat_map]       → beat_map.json
              │                                                     (every node beat)
              │
              ├── graph.json + beat_map.json ──→ [node_scripts]  → node_scripts.json
              │                                                     (Ren'Py script per node)
              │
              ├── beat_map.json ──→ [asset_manifest]             → asset_manifest.json
              │                                                     (image prompts)
              │
              └── asset_manifest.json ──→ [images]               → images_result.json
                                                                    (generated art)

brief + premise + asset_manifest + node_scripts ──→ [build]     → game on disk
```

---

## Climbable Prompts

| Stage | Prompt file |
|---|---|
| premise | `prompts/premise.txt` |
| endings | `prompts/endings.txt` |
| beat_map | `prompts/backward_fill.txt` |
| node_scripts | `prompts/node_script.txt` + `prompts/node_script_system.txt` |
| asset_manifest | `prompts/asset_manifest.txt` |
| graph | no prompt (procedural) |
| images | no prompt (uses renpy pipeline) |
| build | no prompt (assembly) |
