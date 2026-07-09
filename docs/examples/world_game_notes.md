# The Low Water — author's notes (world_game.json)

Companion to `docs/examples/world_game.json`, the hand-authored open-world gold game (W7 in
`tasks/world_first.md`). It is the "One Last LAN" of world games: the reference the generated
world builds are graded against, and the concrete artifact the W6 runtime contract is designed
around. This file records (1) what the current IR/runtime **could not express** while authoring
it — feeding W2/W6 — and (2) a hand-sketched `objectives` component for the three quests in the
W2 shape (a proposal, NOT schema-valid).

---

## The game

**Premise.** The barony of Vessle is dying of thirst. Two dry summers, and then the baron threw
up a new weir upriver to keep his moat pretty and his stocked carp fat — and charges the town a
"water-right levy" for the trickle that reaches them. A wanderer arrives who can open the gate.

**The tension web (one axis, three pulls).** Everything hangs off the weir/water:
- **The guild** (Hessa the smith) wants the gate open — but half the guild owes the baron, so
  they need an outsider to do it.
- **The keep** (Steward Doryn, Sergeant Kel) wants order and the levy paid. Kel guards the
  sluice though his own cousin drinks mud downstream; Doryn keeps the ledgers and will not be
  argued out of them.
- **The drowning** (Tomas the tenant farmer, Perrin the well-keeper) are the collateral: a farm
  about to be seized for the levy, a well gone foul because the drought left nothing to wash the
  rot out of the backwater.

**Three quest chains, all built from flags** (there is no objectives module yet — this is the
hand-faked version W2 will own):

1. **The Weir (main).** Hessa asks you to open the gate (`weir_heard`). You need the mill's
   crank (`sluice_crank`, taken in `old_mill`) and Kel out of the way (`kel_defeated`, set by
   winning `enc_kel`). The sluice `use` is a two-key gate — crank AND Kel — that sets
   `weir_open`. Returning to Hessa unlocks her acknowledgement (a menu choice gated on
   `weir_open`). `weir_open` is the win goal; the win interactable is the water returning to the
   dry ford in `riverside`.
2. **Bad Water (side).** Perrin sends you to kill the thing rotting the backwater
   (`rot_heard` → win `enc_bog` → `rot_cleared`). Turn-in gives `antitoxin`, which feeds combat
   (the `quaff` ability consumes it to heal). `rot_cleared` also flips the town well from foul
   to clean (a `use` clause), so the cure is visible in the world.
3. **The Collector's Due (side, moral fork).** Tomas can't pay tomorrow's levy (`debt_heard`).
   You either give him the miller's hidden `coin_pouch` (found in the mill → `debt_paid`) or tell
   him to refuse and stand with the guild (`debt_refused`). The giver's turn-in menu carries a
   gated re-show for each resolution, so re-talking always acknowledges the state you left it in.

**Systems exercised** (the "does the full capability set survive" checklist): walkable town /
world_map / interior zones with hand-painted tile grids + legends; talk / examine / take / use /
move / win / start_combat interactables; multi-zone move with explicit arrival spawns; flag +
item + variable gates and effects; `use` clause chains (the sluice, the well); dialogue nodes
with menus, per-choice `requires`/`effects`, emotions, and narration; a turn-based combat block
(stats, a status with a damage tick, abilities incl. a vigor-costed heavy and an item-gated
heal, combatants, two authored set-piece encounters); progression (a leveled `xp` variable +
per-level stat growth); and two wild `encounter_table`s (riverside dogs/scavengers, mill rats).

---

## Runtime contract gaps hit while authoring (feeds W2 / W6)

These are the places the drama-first IR fought the world-first content. Each is a thing I *wanted
to say plainly* and had to fake with flags + menus, or couldn't say at all.

1. **No quest owns its state — I hand-rolled every state machine out of flags.** A "quest" here
   is (giver node sets a `*_heard` flag) → (task sets a completion flag) → (giver menu choice
   gated on that flag). Nothing knows these flags are one arc. Consequences I hit directly:
   - **No producer-before-consumer ordering guarantee.** Nothing stops me gating Hessa's
     acknowledgement on `weir_open` while the only producer of `weir_open` sits behind an
     unreachable path. I verified reachability by hand + a script. This is exactly the
     `views.shortest_path` ordering check W2 makes deterministic.
   - **Turn-in acknowledgement is a manual menu variant.** To make a giver "notice" completion I
     add a choice `requires: {flag: done}` and, for re-talks, a second gated re-show choice per
     resolution. The Tomas node has FIVE choices to fake three quest states (offer-pay,
     offer-refuse, ack-paid, ack-refused, leave). W2's per-state giver variants + W3's turn-in
     check would author this from one archetype.

2. **The player is never told what to do.** `ir.goal` (`{flag: weir_open}`) is an invisible
   win-gate — the runtime never surfaces it. There is:
   - **No journal.** Nowhere to record "Hessa wants the weir opened; you need a crank and to get
     past Kel." A first-time player learns the objective only by exhausting Hessa's dialogue and
     remembering it. W6's journal panel is the fix; the `journal` block in the objectives sketch
     below is the data it would render.
   - **No current-objective HUD line.** The only persistent on-screen string is the controls
     hint. A player who wanders into `riverside` first has no thread to pull.

3. **Gated-verb failure can't name the missing requirement — so I encoded the feedback by hand.**
   This was the sharpest gap. The sluice needs BOTH the crank and Kel down. A single `use`
   `fallback` can only say one generic line. To tell the player *which* key they're missing I had
   to author it as a **clause ladder**: `has-crank-but-Kel-up` → "Kel is still on his feet";
   `Kel-down-but-no-crank` → "the socket is bare, you need a crank"; both → open. That's three
   authored branches standing in for what W6 gets free from the data already in `requires`
   ("Requires: Sluice Crank"). A `move`/`start_combat`/`win` gate has no clause mechanism at all,
   so its denial is unavoidably the bare "Not yet."

4. **NPCs exist only as `characters` + a talk hotspot — there is no "resident."** I wanted the
   town to read *inhabited*: a smith who is a quest-giver, but also an innkeep and a child who
   are pure flavor. Today all eight are flat `characters` entries, and the map presenter binds an
   NPC token to a hotspot by **label string match** against the character name (overworld.gd) —
   fragile enough that I gave each talk interactable an explicit `sprite` to sidestep it. W4's
   resident id-binding (`{resident, character}`) is what the talk hotspot should carry. The
   ambient NPCs (Bly, Mella, Garrow) are the "verisimilitude tier" — they have real, short talk
   nodes and nothing mechanical, which is the furniture-for-people model working, but there's no
   module that *requires* them to exist, so a generator has no reason to author them.

5. **Dialogue can't vary by quest state except through a menu.** A talk always enters ONE node.
   To make Hessa/Perrin/Tomas/Doryn react to progress, the entry node's `end` menu does all the
   branching via per-choice `requires`. There is no "when `weir_open`, play node B instead of
   node A" at the hotspot. The W5 talk-variant IR addition (`talk` action carries
   `[{node, requires}]`, first match wins) is what would let a giver's whole demeanor change, not
   just the choices offered. Related: the entry line sets the `*_heard` flag every time it's
   replayed — harmless here (idempotent set_flag) but it means "first meeting" vs "you again"
   framing is unexpressible.

6. **`use` outcomes can't branch into dialogue.** Throwing the sluice is one of the game's two
   biggest beats; all it can do is print a text line. I wanted it to cut to a short scene (the
   gate shrieking up, the river turning). A `use` outcome is `{text, effects}` only — no
   `jump`/`node_end`. So the drama lives in one sentence. (`start_combat` and node ends CAN flow
   into nodes; the interact verb can't.)

7. **Ambient combat has no low-stakes resolution vocabulary.** A wild fight respawns you; an
   authored `on_defeat` is either `end: game_over` or a `jump`. For the side fights (Fen-Rot,
   Kel) I did NOT want a game-over on loss, so I jump to a "you're beaten back, it'll keep" node
   that `return`s. That works, but "you lost but the world continues" is a hand-built idiom, not
   a first-class outcome — and there's no way to say "you lost, so this consequence happens"
   (the farm gets seized, the well stays foul) without inventing more flags.

8. **No spatial reachability guarantee for hand-authored tiles.** I placed every interactable on
   a hand-verified open cell and confirmed the move graph is strongly connected with a script.
   The rasterizer (`map_builder`) guarantees this for generated layouts; a hand-authored `tiles`
   grid gets `snap_to_open` for hotspots but no zone-connectivity check. Fine for a careful
   author, a landmine for a generator writing raw grids (which is why W-side generation goes
   through the layout planner, not raw tiles).

Minor: `variables` `level`/`xp` are engine-plumbing the player never sees a name for; a leveled
var's derived `level` must be separately declared or crossref fails (correct, but easy to miss).
Emotions exist only for VN sprite swaps — on the walkable map the token is static, so all the
`emotion` tags I wrote on these nodes are latent until a scene/combat portrait renders them.

---

## Hand-sketched `objectives` component (W2 shape) — NOT schema-valid

A **proposal** for the three quests above, authored in the `objectives` shape from
`tasks/world_first.md` W2. This is authoring-state (lifted to the IR for the journal render, per
W2), citing `bible` tension ids that don't exist in `world_game.json` (bible is authoring-only
and never lifted). The flags below are the SAME flags the shipped IR already uses, which is the
point: objectives compile down to the ordinary flag substrate the runtime already has.

```jsonc
// PROPOSAL — objectives.json (W2). Not validated by docs/game_ir.schema.json.
// archetype library assumed: fetch | escort | investigate | broker | moral_fork
{
  "objectives": [
    {
      "id": "obj_weir",
      "tension": "tension_water",          // bible id (authoring-only; not in the IR)
      "archetype": "fetch",                // "recover the means, then act on the target"
      "title": "The Low Water",
      "giver": "hessa",                    // a placed resident (W4 binds resident->character)
      "main": true,                        // resolving this is the game_end / win goal
      "steps": [
        {"id": "s1", "summary": "hear the guild's case at the forge",
         "advance_flag": "weir_heard"},
        {"id": "s2", "summary": "recover a crank that fits the sluice (the old mill)",
         "advance_flag": "has_crank"},     // maps to holding item sluice_crank
        {"id": "s3", "summary": "clear Sergeant Kel from the weir",
         "advance_flag": "kel_defeated"},
        {"id": "s4", "summary": "throw the sluice and give the river back",
         "resolutions": [{"id": "opened", "flag": "weir_open"}]}
      ],
      "rewards": {"opened": []},           // the reward IS the world state; no item
      "journal": {
        "offered":  "Hessa says the baron's weir is drying Vessle. She wants the gate opened.",
        "s1":       "The guild won't march. Open the sluice yourself: you'll need a crank and a way past the guard.",
        "s2":       "The mill's crank fits the weir. Garrow's squatting there — ask, don't crowd him.",
        "s3":       "Sergeant Kel holds the sluice. Move him — words or blows.",
        "s4":       "Crank in hand, Kel down. Throw the gate.",
        "resolved.opened": "The Vhel runs to Vessle again. The baron will answer it; that's the guild's weather now."
      }
    },
    {
      "id": "obj_water",
      "tension": "tension_water",
      "archetype": "investigate",          // "find the source, end it"
      "title": "Bad Water",
      "giver": "perrin",
      "main": false,
      "steps": [
        {"id": "s1", "summary": "hear the well-keeper", "advance_flag": "rot_heard"},
        {"id": "s2", "summary": "find and destroy what fouls the backwater",
         "resolutions": [{"id": "cleansed", "flag": "rot_cleared"}]}
      ],
      "rewards": {"cleansed": [{"add_item": "antitoxin"}]},
      "journal": {
        "offered":  "Perrin says something dead in the reeds is poisoning the town well.",
        "s1":       "It's laid up downriver, where the ford dies in the willows. Go armed.",
        "resolved.cleansed": "The rot is burned. The well runs clean; Perrin gave you reed-salve for it."
      }
    },
    {
      "id": "obj_debt",
      "tension": "tension_water",
      "archetype": "moral_fork",           // "two resolutions, no wrong answer, world reacts"
      "title": "The Collector's Due",
      "giver": "tomas",
      "main": false,
      "steps": [
        {"id": "s1", "summary": "hear Tomas out", "advance_flag": "debt_heard"},
        {"id": "s2", "summary": "answer the levy before the collector comes",
         "resolutions": [
           {"id": "paid",    "flag": "debt_paid",    "requires": {"item": "coin_pouch"}},
           {"id": "refused", "flag": "debt_refused"}
         ]}
      ],
      "rewards": {"paid": [{"remove_item": "coin_pouch"}], "refused": []},
      "journal": {
        "offered":  "Tomas can't pay tomorrow's water-levy. The baron's man seizes the farm if he can't.",
        "s1":       "He needs coin, or the nerve to refuse. The mill may still hide the miller's purse.",
        "resolved.paid":    "You covered the levy. Tomas keeps his land another year.",
        "resolved.refused": "You told him to stand. If the river returns, there's no levy owed on nothing."
      }
    }
  ]
}
```

### Notes on the sketch vs. the shipped IR

- **`has_crank` (obj_weir.s2)** is the one step whose `advance_flag` isn't a real flag in
  `world_game.json` — holding an item can't set a flag today (a `take` has no `effects`). Either
  W2 lets an objective step advance on an **item condition** (not only a flag), or the take needs
  an effects list. I flagged this rather than adding a dead `has_crank` flag to the shipped game.
- **The two-key sluice** (crank AND Kel) is a within-step dependency (`s2` and `s3` both gate
  `s4`). The archetype step template needs to express "several prerequisites converge on one
  act," which the linear `steps` list only implies by order. W2's ordering check should treat
  `s4`'s producer as reachable only once BOTH `s2` and `s3` are satisfied.
- **`resolutions[].requires`** (obj_debt: the "paid" branch needs `coin_pouch`) isn't in the W2
  skeleton but the moral fork needs it — the fork isn't free, one arm has a material cost. Worth
  folding into the archetype.
- Everything else maps 1:1 onto flags the runtime already honors, which is the load-bearing
  claim: **objectives is a state-machine skin over the existing flag substrate**, so W6 can ship
  the journal/HUD render without any new condition grammar.
