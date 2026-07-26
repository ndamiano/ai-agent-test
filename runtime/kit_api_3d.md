# Kit API — 3D games

A 3D game is the SAME shape as a 2D one, with `config.mode: "3d"` and one extra optional hook.
You write PURE simulation — mutate entity positions in 3D — and TAG each entity with a shape.
**You never write any three.js / WebGL.** The runtime renders your entities as meshes.

```js
export function createGame(kit) {
  return {
    config: { mode: "3d", width: 1280, height: 720, background: "#101018" },
    state:  { world: [] },
    init(kit)              { },   // spawn entities into state.world (each tagged with a shape)
    update(dt, input, kit) { },   // advance the sim: mutate e.x,e.y,e.z,e.vx,e.vy,e.vz. NO rendering.
    camera(cam, kit)       { },   // optional: each frame, set the camera (see below)
  };
}
```

## Coordinates
Right-handed: **+x right, +y UP, +z toward the camera.** The ground is the y=0 plane. Units are
world-units; velocities are units/SECOND (kit.integrate3 applies dt for you). Keep speeds modest
(a walker ~8 units/s, gravity ~20 units/s²) — the scene is tens of units across, not hundreds.

## Rendering: tag each entity with a shape (the runtime draws it)
Put these fields on a world entity and it renders automatically at (x,y,z):
- **box:**    `{ shape:"box",    x,y,z, w,h,d, color, ry? }`  — ry = yaw in radians (optional)
- **sphere:** `{ shape:"sphere", x,y,z, r,     color }`
- **ground:** `{ shape:"ground", size, color, y? }`          — a flat plane; y defaults to 0
<!-- world -->
- **heightfield / grassfield:** the terrain + grass that `spawnWorld` seeds into a generated village.
  You do NOT author these — `spawnWorld` pushes them. They are FULLY renderable; NEVER filter or
  remove entities from `state.world` after `spawnWorld` (a `shape`-allowlist filter deletes the ground).
<!-- /world -->
`color` is a CSS string and MUST include the leading `#` (`"#c33"`, `"#33cc55"`) — a bare hex like
`"cc3333"` renders as the wrong color. `y` is the entity's CENTER. Entities with no `shape` are
invisible (pure logic markers).
**Box axes:** `w` spans x, `h` spans UP (height), `d` spans z. A creature described as "3m LONG" is
`d:3` (or `w:3`), NOT `h:3` — `h:3` is a 3m-TALL monolith. A four-legged beast reads better as a low
long body box (`w:1, h:1.2, d:2.6`) plus a small head box than as one slab.

**The player IS a world entity.** The character you control must be a SHAPE-TAGGED entity pushed into
`state.world`, and `state.player` must reference that SAME object — create it once with
`this.state.player = kit.spawn(this.state.world, { shape:"box", x,y,z, w,h,d, color })`. A bare
`state.player = { x, y, z, … }` kept OUTSIDE `state.world` is invisible AND the movement gate can't see
it move (dead-controls). One object, in the world, referenced by `state.player`.

<!-- world -->
**A generated `world.ts` is on disk — BUILD ON IT, never author terrain or a town.** The PLACE
already exists: terrain, streets, buildings, the roads out, forest, and outlying sites. Spawn it and
put the player on it:
```ts
import { WORLD, spawnWorld, heightAt } from "./world.ts";

export function init(state: GameState, kit: Kit): void {
  spawnWorld(state.world);            // the whole place: terrain, town, forest, POI dressing
  const p = WORLD.plaza;
  state.player = kit.spawn(state.world, { shape:"box", x:p.x, y:heightAt(p.x,p.z)+0.9, z:p.z,
                                          w:0.8, h:1.7, d:0.8, color:"#28303a" });
  // ...then the spec's NPCs / creatures / items, ACROSS THE WHOLE MAP (see below)
}
```
The GENERATED main.ts owns the ENTIRE player loop on a world: it moves the player, stands them on
the terrain and slides them around buildings, every frame, already wired. So in `update` do NOT call
`kit.drive`, do NOT clamp `state.player.y`, do NOT `kit.avoidRects` the player. (Your own NPCs and
creatures ARE yours: after any steering, set `e.y = heightAt(e.x, e.z) + halfHeight` and
`kit.avoidRects(e, WORLD.buildings)` yourself.)

`WORLD.buildings` (`[{id,label,x,z,w,d,h,color}]`, `label` = kind — the SET VARIES per world, so
NEVER `find(b => b.label === "...")` a guessed name (a miss silently drops your NPC/shop). Place
people by POSITION instead — e.g. the building nearest the plaza:
`const home = WORLD.buildings.reduce((a,b) => Math.hypot(a.x-WORLD.plaza.x, a.z-WORLD.plaza.z) <
Math.hypot(b.x-WORLD.plaza.x, b.z-WORLD.plaza.z) ? a : b);` — or just spread NPCs over
`WORLD.buildings[i]` by index), `WORLD.plaza {x,z}`, `WORLD.gate {x,z}` (where the roads leave town), `WORLD.grass [[x,z],…]` (open town ground),
`WORLD.pois` (`[{id,kind,label,x,z}]` — real outlying sites: a cave, ruins, a camp, far outside the
village, already dressed with props by spawnWorld), `WORLD.regions` (`{forest:[[x,z],…],
meadow:[[x,z],…]}` — wilderness spawn points). `heightAt(x,z)` is the ground height — every entity's
`y` should be `heightAt(x,z) + halfHeight`.

**Use the WHOLE map — this is what makes it a place and not a room:**
- **Every building gets a function.** Put an NPC in front of each one, matched to its `label` (a
  vendor at a market stall selling from a priced talk menu, a smith at a workshop, an elder by the
  well). Each NPC gets ITS OWN name and its own `kit.talkOpen` lines — never one shared script.
- **Send the player OUT.** Put at least one objective — the spec's boss/goal/destination — at or
  near a `WORLD.pois` entry, out past `WORLD.gate`, never in the plaza. Spawn roaming or hostile
  creatures at `WORLD.regions.forest` points and scatter items over both town grass and meadows.
- Mark the current objective with a `{kind:"marker", x, z, text}` HUD item so the player can find it.
<!-- /world -->

**Two hard rules that shape how you build a 3D game — internalize these:**
1. **A 3D game has NO `draw()`. The HUD is DATA you RETURN from `hud(kit)`.** The scene renders from
   entity shape tags; the HUD is a screen-space overlay the engine draws from the items you return —
   you never touch a canvas (so you cannot clear or occlude the scene). Return an array of items:
   ```ts
   hud(kit: Kit): HudItem[] {
     return [
       { kind: "text", text: `Score: ${this.state.score}`, at: "top-left" },
       { kind: "bar",  value: this.state.hp, max: 100, at: "top-right", color: "#e44", label: "HP" },
       ...(this.state.won ? [{ kind: "banner", text: "You win!" }] : []),
     ];
   }
   ```
   Items: `{kind:"text", text, at?, color?, size?}` · `{kind:"bar", value, max, at?, color?, label?}` ·
   `{kind:"banner", text, color?}` (centered) · `{kind:"panel", text, title?, at?, color?}` (a titled
   text card — dialogue line / narration / quest log; multi-line via `\n`) · `{kind:"menu", options,
   selected?, title?, at?, color?}` (a numbered choice list) · `{kind:"marker", x, z, text?, color?}`
   (a WORLD-anchored waypoint: the engine projects it to the screen with a distance and an edge arrow
   when off-screen — put one on the current objective so the player can FIND it). `at` is an anchor:
   `"top-left"`, `"top"`, `"top-right"`, `"left"`, `"center"`, `"right"`, `"bottom-left"`, `"bottom"`,
   `"bottom-right"` (default `"top-left"`); same-anchor items stack. `hud()` reads state, never
   mutates. Omit it if the game needs no HUD.

   **Conversation, quests, shops — the kit OWNS these loops (this is how a game gets DEPTH). Do NOT
   hand-roll a talk state machine.** Three primitives, all on plain state (sim/render law intact):
   ```ts
   // update(): ONE call runs the whole dialogue — advance on E, choose with number keys, Escape closes.
   const pick = kit.talkStep(this.state, input);        // null, or {npc, pick} when a choice was made
   if (pick) {
     if (pick.npc.role === "elder" && pick.pick === 0)   // "Accept the quest"
       kit.quest.add(this.state, { id: "beast", title: "Slay the beast", reward: 50 });
     if (pick.npc.role === "vendor" && pick.pick === 0 && this.state.gold >= 10) {
       this.state.gold -= 10; this.state.hp = Math.min(100, this.state.hp + 40);
       kit.notify("Bought a potion (+40 hp)");
     }
   }
   if (this.state.talk) return;                          // movement paused while talking
   // OPENING a conversation is not yours to write: the scaffold's activate key does it for any
   // entity carrying lines. A SHOP is the same loop with priced options —
   //   kit.talkOpen(state, vendor, ["Health potion (10g)", "Sharper sword (25g)", "Leave"])
   // is the ONE case you call yourself, from onActivate when the target is your vendor.
   // hud(): spread the ready-made items in — the panel/menu render themselves:
   hud(kit: Kit): Kit.HudItem[] {
     return [
       { kind: "text", text: `Gold: ${this.state.gold}`, at: "top-left" },
       ...kit.quest.log(this.state),                     // the quest list panel (auto-hides when empty)
       ...kit.talkHud(this.state),                       // the dialogue panel + choice menu while talking
       // NEVER build the dialogue overlay yourself: state.talk is the KIT's ({npc, line, options}),
       // and a game that read its own invented field off it drew the word "undefined" over the screen.
     ];
   }
   ```
   **Quests are how an open game progresses WITHOUT ending** — `kit.quest.add(state, {id, title,
   reward})` on accept, `kit.quest.complete(state, id)` when its condition is met (it announces
   itself; returns the quest — pay `q.reward` into your gold yourself), `kit.quest.isDone(state, id)`
   to gate follow-ups. `kit.notify("Got 10 gold")` is a transient toast for any small accomplishment.
   **Call `kit.win`/`kit.lose` ONLY for the spec's definite ending — they STOP the game.** Accepting a
   quest, finishing a side task, buying a sword: `quest.complete`/`notify`, never `kit.win`. Real
   depth = several NPCs with DIFFERENT lines/roles (a vendor who sells, an elder who quests, a smith
   who upgrades), not more collectibles.
2. **Only position + `ry` update live** for WORLD entities. Each frame the renderer re-reads an entity's x/y/z and `ry`
   only — NOT its size (w/h/d/r) or color (those bake when the entity first appears). To change how
   much health shows, ADD or REMOVE entities (splice pip boxes from `state.world`); to show a hit,
   MOVE the entity (a lunge/recoil), never recolor or resize it.

## Sub-modes: combat / minigame overlays  (walk into an enemy → a turn-based fight opens)
A game with a battle screen, a puzzle overlay, or any "now we're doing something else" is ONE game
with a `state.mode` switch — never a second engine, never a canvas. The world keeps rendering behind
the overlay; the HUD becomes the sub-game's UI. The pattern:
```ts
// state: { mode: "world", combat: null, deck: [...], gold: 0, hp: 30, ... }
update(dt, input, kit) {
  const s = this.state;
  if (s.mode === "combat") {                 // the sub-game OWNS the frame: no movement below
    const c = s.combat, n = c.hand.length;
    if (input.pressed("ArrowLeft"))  c.selected = (c.selected - 1 + n) % n;
    if (input.pressed("ArrowRight")) c.selected = (c.selected + 1) % n;
    if (input.pressed(" ")) { /* play c.hand[c.selected], then the foe answers */ }
    if (c.foeHp <= 0) { s.mode = "world"; s.combat = null; kit.notify("Won! +10g"); }  // NOT kit.win
    if (c.myHp <= 0) kit.lose("Slain.");     // kit.win/lose ONLY for the spec's real ending
    return;                                   // ← the return IS the mode switch
  }
  kit.drive(s.player, input, dt, 8);          // world mode: move, steer NPCs, ...
  for (const e of s.enemies)                  // contact starts the fight
    if (Math.hypot(e.x - s.player.x, e.z - s.player.z) < 1.6) {
      s.combat = { hand: [...], selected: 0, myHp: s.hp, foeHp: e.hp, foe: e };  // deal from s.deck
      s.mode = "combat"; return;
    }
}
hud(kit) {
  const s = this.state;
  if (s.mode === "combat") return [           // the sub-game's UI is HUD items over the frozen scene
    { kind: "bar", value: s.combat.myHp, max: 30, at: "bottom-left", label: "You" },
    { kind: "bar", value: s.combat.foeHp, max: 16, at: "top-right", color: "#e44", label: "Foe" },
    { kind: "menu", title: "Hand (←/→ pick, SPACE play)", at: "bottom", selected: s.combat.selected,
      options: s.combat.hand.map(c => `${c.name} ⚔${c.attack}`) },
  ];
  return [ /* the world-mode HUD */ ];
}
```
What persists (deck, gold, hp) lives at the TOP of state, not inside `state.combat` — combat copies
in what it needs and writes back on exit. Different enemies = different deck arrays on the enemy
entities (data, not code). A shop that sells cards is the vendor pattern above: on the pick,
`if (s.gold >= 12) { s.gold -= 12; s.deck.push({name:"Cleave", attack:7}); }`.

## Camera  (optional `camera(cam, kit)` hook)
For a third-person follow, DON'T hand-roll the eye/look-at math — call the kit primitive:
```js
camera(cam, kit) { kit.chaseCam(cam, this.state.player); }   // eye behind+above, looks AT the player
```
`kit.chaseCam(cam, target, opts?)` sets ALL of the camera each frame — the eye AND the look-at — so
the view actually tracks the target. `opts = { back=16, up=12, lookUp=1.5, faceYaw=false }`; set
`faceYaw:true` to keep the camera behind the target's heading (`target.ry`). Reach for the raw fields
only for a non-follow shot.

`cam` is `{ x,y,z }` (the eye) plus `{ tx,ty,tz }` (the look-at point). If you set them by hand you
MUST set BOTH — moving the eye while leaving `tx,ty,tz` unset pins the view to the world origin.
If you omit the `camera` hook entirely, a game with a `state.player` gets a default chase cam; else a
fixed 3/4 view.

## Sim helpers (same kit as 2D; the 3D-specific ones)
- `kit.integrate3(e, dt, gravity=0)` — `y` is up; gravity pulls -y. Moves x/y/z by v*dt (dt handled).
- `kit.spawn(world, {...})`, `kit.cull(world)`, `kit.clamp(v,lo,hi)`, `kit.aabb`
  (works on x/y as before — for 3D distance use `Math.hypot(dx,dy,dz)`), `kit.win(msg)/kit.lose(msg)`.

## NPC / creature steering (3D — do NOT hand-roll dx/dz chase math or reuse the 2D seek/wander,
they move the WRONG axis; y is UP in 3D). These apply dt themselves and face the entity to its travel:
- `kit.seek3(e, target, speed, dt)` — walk straight at target's `(x,z)`; returns distance left.
  A wolf: `if (kit.seek3(wolf, player, 4, dt) < 1.5) bite();`
- `kit.wander3(e, speed, dt)` — amble around, slowly turning (a villager mooching about).
  Leash it home: `if (Math.hypot(v.x-v.homeX, v.z-v.homeZ) > 6) kit.seek3(v, {x:v.homeX, z:v.homeZ}, 2, dt); else kit.wander3(v, 1.2, dt);`
- `kit.patrol3(e, points, speed, dt)` — walk a looping route of `{x,z}` (or `[x,z]`) points (a guard).
- After ANY steering, keep the entity's `y` on the ground: on FLAT ground (no world.ts) that is a
  CONSTANT — `e.y = halfHeight` — never a function call.
<!-- world -->
- In a WORLD game (a world.ts exists): after steering ANY entity of yours —
  `e.y = heightAt(e.x, e.z) + halfHeight` — and `kit.avoidRects(e, WORLD.buildings)` so walkers
  slide around buildings instead of through them. Not the player: the scaffold does both for them
  once you set `state.ground` / `state.walls`.
<!-- /world -->

## Effects, collision & bounds  (3D-specific — READ THIS, the 2D kit misleads here)
- **NO particles in 3D.** `kit.burst` / `kit.stepParticles` are 2D-only — they spawn shape-less x/y
  particles the 3D renderer can't draw, and a wrong arg count spawns millions and OOMs. For a 3D
  "pop"/"poof", spawn a few short-lived `sphere` entities yourself with an upward `vy`, `integrate3`
  them, and `kit.cull` when a `life` counter expires — or just skip the effect. Do NOT call `kit.burst`.
- **A walled level (dungeon/maze/crypt/rooms) = `kit.wallsFromTilemap`, ONE call.** It spawns the
  ground AND every wall box and hands back the collision rects — never hand-assemble walls from a
  char map (the classic half-build spawns nothing and the level is an empty void):
  ```js
  const level = kit.wallsFromTilemap(state, ["########", "#..#...#", "#......#", "########"],
                                     { tile: 4, height: 3, color: "#665", ground: "#332" });
  // passing STATE spawns ground+walls into state.world AND sets state.walls — the scaffold then
  // keeps the player out of walls every frame; nothing else to wire.
  const start = level.at(1, 1);                    // tile → world center; place things with at()
  state.player = kit.spawn(state.world, { shape: "box", x: start.x, y: 0.9, z: start.z, w: 0.8, h: 1.8, d: 0.8, color: "#28303a" });
  ```
  In first-person keep the player SHORTER than the walls (h ≤ 1.8 under height 3) — a tall body
  puts the eye camera above the walls and the maze reads as a field of stubs. Push NPCs out of
  walls yourself with `kit.avoidRects(npc, state.walls)` after steering them.
- **Solid collision = `kit.avoidRects`, nothing else.** Do NOT hand-roll an AABB loop against the
  world — a town's road/ground slabs are huge boxes the player overlaps every frame, which pins the
  player in place (the #1 broken-3D-movement bug). For buildings/obstacles call
  `kit.avoidRects(e, rects)` AFTER moving (`rects` = `WORLD.buildings` in a world game, else your own
  `[{x,z,w,d}]` obstacle list); to keep the player inside the map clamp with
  `player.x = kit.clamp(player.x, -HALF, HALF)` (same for z). Collect/trigger on DISTANCE only:
  `if (Math.hypot(px-e.x, py-e.y, pz-e.z) < R) { ...collect... }`.
- **Animate decoration/pickups by MUTATING position** (allowed live; color/size bake). A floating
  balloon or a hovering marker bobs: `e.y = baseY + Math.sin(kit's t * 2) * 0.5` (keep a `t += dt` in
  state). A spinning pickup turns: `e.ry += dt`. This is how a scene feels alive.

## Composing a 3D scene that READS as its subject (the gates can't see "looks good" — you must)
The scene is ONLY your shape-tagged entities, so BUILD it deliberately — a flat monochrome grid reads
as nothing. For a town: a grass `ground`, gray road strips, and buildings that VARY (randomize their
size + pick from a palette of 4–6 warm colors), each a body `box` + a wider thin `box` roof + a small
dark door box. Scatter trees (brown trunk `box` + green `sphere` foliage) and lampposts (thin `box` +
small bright `sphere`) along the streets, and a landmark at the center (fountain/square). Tens of
placed props, not thousands. The same principle holds for any subject: enumerate its real objects and
compose each from a few shaded primitives.

## Control scheme  (ONE choice: `config.controls` + `kit.drive` — do NOT hand-roll, do NOT pair by hand)
A whole 3D control feel is a SINGLE decision. Set `config.controls` to a scheme name, call `kit.drive`
in `update`, and OMIT the camera hook — the runtime wires the matching camera from the same name. The
mover and camera are tied by ONE value, so they can't be mismatched (world-axis movement under a camera
that doesn't rotate — "left" always goes the same way — is the #1 broken-3D bug; this makes it
impossible).

**The choice comes from the spec's `control.scheme` — NOT a default. Map it:**
`first-person-3d`/first-person/walking-sim/FPS → **`"fp"`** · `orbital-3d`/third-person hero/RPG/platformer
→ **`"orbital"`** · `vehicle-3d`/ship/car → **`"vehicle"`** · top-down → **`"follow"`**. If the spec says
first-person, `controls` MUST be `"fp"` — orbital is a THIRD-person camera and is wrong for it.

**Outside `"fp"` there is NO mouse input.** In orbital/follow/vehicle the mouse is the camera
(drag = orbit); `input.pointer` is never set and there is no `"mouse0"`/`"click"` key. If the spec
says "click to attack/shoot/use", bind that action to a KEYBOARD key instead — `" "` (space) or
`"f"`, read with `input.pressed` — and show the key in the HUD. A mouse-bound action in these
schemes can NEVER fire, so the game becomes unwinnable. Pick the row:

| `config.controls` | Feel | What you get |
|-------------------|------|--------------|
| `"orbital"` | third-person hero / platformer / RPG (Skyrim-ish) | drag orbits the view; WASD moves relative to it; chase cam |
| `"follow"` | third-person hero | W/S forward/back along facing, A/D turn; chase camera eases behind the heading |
| `"vehicle"` | ship / car / shark | W/S drive along facing, A/D turn; chase cam behind heading |
| `"fp"` | **first person** (shooter / explorer / walking-sim) | mouse-look aims; WASD relative to aim; eye-level camera. Captures the pointer itself — no `pointerLock` needed |

```js
const config = { mode: "3d", controls: "orbital" };   // ONE choice
// ...
update(dt, input, kit) {
  kit.drive(this.state.player, input, dt, 10);         // runs the scheme's mover
  // ...game logic...
}
// NO camera() hook — the runtime wires the scheme's camera for you.
```
`kit.drive(player, input, dt, speed=8)` dispatches to the scheme's mover; the runtime picks the scheme's
camera. That is the whole control rig — do not also write a `camera()` hook or call a raw mover unless
you need a bespoke rig. The raw movers `kit.drive` dispatches to (for that rare bespoke case):
- `kit.moveRelative(e, input, dt, speed=8)` — WASD relative to `input.camYaw`; W into the screen, A/D strafe.
- `kit.moveTank3(e, input, dt, {speed, turn, back})` — W/S drive along `e.ry`, A/D turn.
- For a flyer with pitch, use `kit.flyer` (below). These read HELD keys and apply dt for you — never
  reach into `input.pressed` for movement, and never accumulate keys into a set. Momentum/physics ball?
  Still call a controller for direction; do NOT hand-roll velocity from raw key checks.

## First-person aiming  (you already chose `controls: "fp"` above)
`controls: "fp"` gives you the whole first-person rig — mouse-look, WASD-relative movement, and the
eye-level camera, all from `kit.drive`. Do NOT also set `pointerLock` or call `mouseLook`/`moveFP`/
`fpCam` by hand — the scheme wires them. This section is ONLY the extra combat/interaction on top:
```js
update(dt, input, kit) {
  kit.drive(this.state.player, input, dt, 6);     // the "fp" rig: look + move + camera
  if (input.pointer.down) { /* attack / interact: hit along the aim */ }
}
// NO camera() hook — the "fp" scheme wires the eye camera for you.
```
`player.yaw`/`player.pitch` are the aim. The forward/aim direction is
`{ x: Math.sin(yaw)*Math.cos(pitch), y: Math.sin(pitch), z: -Math.cos(yaw)*Math.cos(pitch) }` — use it
to spawn a projectile or to test which enemy is in front. Show a crosshair with `hud()`: `{kind:"text", text:"+", at:"center"}`.

## Flight  (USE THIS for a plane/ship/flyer — don't hand-roll 3D orientation)
- `kit.flyer(e, input, dt, opts?)` — the whole flight step: steer `e.yaw`/`e.pitch` from input,
  thrust along the facing direction, apply drag, integrate. Defaults: ArrowLeft/Right = yaw,
  ArrowUp/Down = pitch, Space = thrust. `opts = { thrust=30, turn=1.5, climb=1.2, drag=0.4,
  keys={yawL,yawR,up,down,go} }`. Sets `e.ry = e.yaw` so a `box` entity renders turned. A "flying
  pony" is just a flyer with a different shape/skin — the mechanic is identical.
- `kit.heading3(yaw, pitch)` → unit `{x,y,z}` facing (+z forward at yaw 0). For spawning shots
  forward, or a chase-cam behind the craft.
```js
update(dt, input, kit) {
  kit.flyer(this.state.plane, input, dt, { thrust: 40 });   // WASD-less arcade flight
  if (this.state.plane.y < 0) kit.lose("crashed");
}
camera(cam, kit) {                                          // chase-cam behind the plane
  const p = this.state.plane, f = kit.heading3(p.yaw, p.pitch);
  cam.x = p.x - f.x * 20; cam.y = p.y + 8; cam.z = p.z - f.z * 20;
  cam.tx = p.x; cam.ty = p.y; cam.tz = p.z;
}
```

## Input  (same as 2D)
`input.down(key)`, `input.pressed(key)`; keys are lowercase chars or `"ArrowUp"`…`"ArrowRight"`, `" "`.
There is NO `"mouse0"`/`"click"` key, and `input.pointer` only works under `controls:"fp"` — every
action in the other schemes is a keyboard key.

## Actions (register key presses)
**Rule: EVERY non-movement control in the spec's `controls` map gets a `kit.register` in init.**
Movement keys stay with the control scheme (`config.controls` + `kit.drive`) — never registered.
The engine fires the handler on the PRESSED edge of any bound key, after update each frame; a
re-register with the same name replaces. `kit.bindings()` → `[{name, keys}]`. Held mechanics may
still read `input.down` per frame; registration targets EDGE actions. Register real state-mutating
actions, bound to REAL `KeyboardEvent.key` values (`" "`, `"e"`, `"1"`, `"Enter"`). There are NO
mouse keys — `"LEFT_CLICK"`/`"MOUSE_MOVE"`/`"mouse0"` do not exist. In "fp" a click-to-shoot is
`if (input.pointer.down)` in update, never a registration.
```js
init(kit) {
  kit.register("attack", [" "], () => {           // spec: "SPACE: attack" — keyboard, never mouse
    const foe = nearestEnemy(this.state);         // handlers may read state for range/aim
    if (foe && Math.hypot(foe.x - this.state.player.x, foe.z - this.state.player.z) < 3) foe.hp -= 1;
  });
  kit.register("interact", ["e"], () => {         // ONLY for a game the scaffold gave no activate key
    const near = this.state.npcs.find((n) => Math.hypot(n.x - this.state.player.x, n.z - this.state.player.z) < 3);
    if (!this.state.talk && near) kit.talkOpen(this.state, near);
  });
}
```

## Law
`update` mutates state and never renders; the runtime handles all drawing. Keep the sim pure so it
runs headless. `kit.win`/`kit.lose` END the game — milestones along
the way are `kit.quest.complete` / `kit.notify`, which keep it running.

<!-- data -->
## Data-driven entities  (a data row IS the thing's look)
When the game has DATA (`game/data/*.json` → `./data.ts`), a row already describes its own
appearance — `size` (WORLD UNITS in 3D: a person is `{w:0.8,h:1.7,d:0.8}`, use as-is), optional
`shape` ("box"|"sphere") and `color` — and its `id` is ALSO the mesh id the asset stage renders to.
Spawn from the row and the entity is skinnable with no code change:
```ts
import { ENEMIES } from "./data.ts";
const e = kit.spawnData(state.world, ENEMIES[0], { x, y: heightAt(x, z) + 0.85, z });
// -> { shape, w,h,d, color, mesh: "<row id>", x, y, z } — the renderer swaps in the GLB when it exists
```
- `kit.spawnData(world, row, {x,y,z, ...})` — anything in the third arg overrides.
NEVER re-scale a row's `size` (no `/100`), and never hand-pick a color for a row that has one.
There is no `drawEntity` in 3D — the scene renders from the shape tag, and `mesh` is one.
<!-- /data -->

## Activate — ONE key, whatever the player is facing
The scaffold owns a single `activate` key. Every frame the kit works out WHAT it would act on
(inside the player's facing cone, within reach, nearest wins) and the engine draws the prompt
("E — Talk to Oren"). You never write proximity maths, never read the key, never draw the prompt.

An entity opts in by carrying two fields:
```ts
kit.spawn(state.world, { ...visual, action: "harvest", label: "Ripe crop" });   // label is optional
kit.spawn(state.world, { ...visual, action: "talk", name: "Oren", lines: ["..."] });
```
- `action` — the verb id your `onActivate` switches on. No `action` ⇒ never targeted.
- `label` — what the prompt calls it (falls back to `name`, then `action`).
- `reach` — optional per-entity range, for something big like a bed or a door.

A talkable target (one carrying `lines`) opens ITSELF — dialogue is entirely the kit's. Every other
verb arrives at your hook with the target already chosen:
```ts
export function onActivate(state: GameState, target: Entity, kit: Kit): void {
  if (target.action === "harvest") { state.bag.produce++; target.dead = true; kit.notify("Harvested"); }
  if (target.action === "sleep") advanceDay(state, kit);
}
```
`state.focus` holds the same target (or null) if hud() wants to say more about it.

WHY one key: a spec that spends four keys on talk / use tool / tend animal / sleep gives the player
four things to remember and the build four bindings to get right — and the verb the player wants is
already unambiguous from what they are standing in front of.
