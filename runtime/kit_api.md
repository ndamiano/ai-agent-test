# Kit API — the primitives a game composes

A game is one ES module: `export function createGame(kit) { return { ...game } }`.
The game object has this exact shape:

```js
export function createGame(kit) {
  return {
    config: { width: 960, height: 540, title: "", background: "#111", gravity: 0 },
    state:  { world: [] },          // EVERYTHING VISIBLE lives in state.world — that is what renders
    init(kit)              { },      // one-time setup; spawn entities into state.world
    update(dt, input, kit) { },      // advance the sim ONE step (dt = seconds)
    hud(kit)               { return []; },  // OPTIONAL: RETURN screen-space HUD items (see HUD below).
  };
}
```

**THE GAME NEVER DRAWS.** There is no draw hook and no canvas — the ENGINE renders `state.world`
each frame, drawing every entity from its own `sprite` (asset id), `shape`, `color` and `parts`.
So an entity is visible IF AND ONLY IF it is in `state.world`: bullets, pickups, particles, walls
and the arena border are all entities, not draw calls. `layer` (default 0) orders them, low first.
`state.cam` (from `kit.makeCamera()`) scrolls the view, `state.tilemap` renders behind them,
`config.backdrop` is an asset id painted behind everything.

**Law: update mutates state; hud reads state and never mutates.** The sim must run with no canvas
(it's tested headless).

**HUD (score, health, timers, banners):** RETURN them from `hud(kit)`
as data and the engine draws them (anchored, no pixel math, can't occlude the scene). Items:
`{kind:"text", text, at?, color?, size?}` · `{kind:"bar", value, max, at?, color?, label?}` ·
`{kind:"banner", text, color?}` (centered) · `{kind:"icon", id, at?, size?}` (screen art — an
inventory icon, a life pip; `id` is a data row's id). `at` anchors to a screen region — `"top-left"` (default),
`"top"`, `"top-right"`, `"bottom-left"`, `"center"`, … — and same-anchor items stack. This is the
SAME HUD in 2D and 3D.

## UNITS — read this first
**All velocities are pixels per SECOND. All accelerations are pixels per second².** The kit
multiplies by `dt` for you inside `integrate`/`physics`. NEVER write per-frame magnitudes and
NEVER multiply by dt yourself. Rough feel: a walker ≈ 150 px/s, a bullet ≈ 400 px/s, a jump
impulse ≈ 600 px/s, gravity ≈ 2000 px/s². (A value like `speed = 3` is a per-frame number — wrong;
it makes things effectively motionless.)

## Entities & physics
- `kit.spawn(world, {x,y,vx,vy,w,h,...})` → entity. Missing fields default to 0/false. Returns it.
- `kit.cull(world)` — remove every entity with `dead:true`.
- `kit.integrate(e, dt, gravity=0)` — `e.vy += gravity*dt; e.x += e.vx*dt; e.y += e.vy*dt`.
- `kit.aabb(a, b)` → bool. Box overlap (x,y = top-left, w,h = size).
- `kit.resolveAabb(a, b)` → "left"|"right"|"top"|"bottom". Push `a` out of solid `b`, zero that
  velocity component, return the side hit.
- `kit.makeTilemap(rows, tile=32, solid="#")` → `{ at(cx,cy), solidAt(cx,cy), solidsNear(e), w, h }`.
  `rows` is an array of equal-length STRINGS, one per map row — `kit.makeTilemap(["#####","#...#","#####"])`
  — never a 2D char array (`string[][]` is a type error).
  `solidsNear(e)` returns the solid tile rects overlapping entity `e` (feed each to resolveAabb).

## Top-down movement  (USE THIS for WASD steering — don't hand-roll it)
`kit.moveTopDown(e, input, dt, speed=150)` — omni-directional WASD/arrow movement on the x/y plane,
diagonals normalized, dt applied, faces travel via `e.angle`. One call per steered entity in `update`.
Reads HELD keys — never drive movement off `input.pressed` or a key-set you forget to clear.

## Platformer physics  (side-view gravity/jump — USE THESE, don't hand-roll it)
- `kit.walk(e, dir, speed)` — set horizontal intent. `dir` = -1|0|1, `speed` in px/s.
- `kit.jump(e, speed)` — jump ONLY if `e.grounded` (sets `e.vy = -speed`, clears grounded). px/s.
- `kit.physics(e, dt, solids, gravity=2000)` — the whole step: applies gravity, moves x then y,
  resolves against `solids` (array of AABB rects — a platform list, or `tilemap.solidsNear(e)`),
  and sets `e.grounded` true when it lands on something. dt handled inside. Call once per frame
  per actor. Example:
  ```js
  if (input.down("ArrowLeft")) kit.walk(player, -1, 150);
  else if (input.down("ArrowRight")) kit.walk(player, 1, 150);
  else kit.walk(player, 0, 150);
  if (input.pressed(" ")) kit.jump(player, 600);
  kit.physics(player, dt, this.state.platforms, 2000);   // platforms: [{x,y,w,h}, ...]
  ```

## Solid collision (top-down/2D)
ONE call resolves all solid collision — entities vs walls AND entities vs each other. Tag every
entity that should block/be blocked with `solid: true` (the player, monsters, crates); leave
bullets, pickups and particles untagged. The scaffold calls
`kit.collideWorld(state.world, state.solidAt, state.cell)` after your update every frame — game
code normally NEVER calls it, it just tags entities and (optionally) provides the wall lookup:
- `state.solidAt(cx, cy)` → bool — solid cells (a tilemap game can omit it: the scaffold uses
  `state.tilemap.solidAt`). Absent ⇒ pair separation only.
- `state.cell` — px per cell (default 32; a tilemap game inherits `tilemap.tile`).
- `kit.collideWorld(world, solidAt?, cell?)` — the pass itself: solid entities are pushed out of
  solid cells (minimal axis, blocked velocity zeroed) and overlapping solid pairs are pushed apart
  half-and-half. Only for the rare unscaffolded game.
```js
state.player = kit.spawn(state.world, { x: 64, y: 64, w: 24, h: 24, solid: true });
kit.spawn(state.world, { x: 200, y: 64, w: 24, h: 24, solid: true, type: "monster" });
kit.spawn(state.world, { x: 90, y: 64, w: 6, h: 6, vx: 400, type: "arrow" });   // NOT solid — flies through
state.solidAt = (cx, cy) => state.tilemap.solidAt(cx, cy);   // or omit and keep state.tilemap
```
**Law: gameplay must never zero velocities or snap positions back to fake collision** — the pass
resolves overlap by pushing OUT; a hand-rolled undo kills movement.

## Steering  (enemy/NPC movement — chase, patrol; velocities in px/SECOND)
Set an entity's velocity toward or away from a target, then `kit.integrate` it. A `target` is any
`{x,y}` (an entity or a point); sized entities aim at each other's centres automatically.
- `kit.seek(e, target, speed)` — steer straight at the target. Returns the distance to it.
- `kit.wander(e, speed, turn=3)` — random drift (idle patrol).
```js
for (const ghost of this.state.enemies) {
  const dist = kit.seek(ghost, this.state.player, 90);   // chase the player at 90 px/s
  kit.integrate(ghost, dt);                               // apply the velocity (dt handled inside)
  if (dist < 16) kit.lose("caught");
}
```

## Pathfinding  (A* on a cell grid — creeps, chase-with-walls, tactics)
- `kit.astar(start, goal, passable, {cols, rows, diagonal=false})` — `start`/`goal` are `{x,y}` in
  CELL coords; `passable(cx,cy)` → bool (a wall is not passable). Returns the cell path (each `{x,y}`)
  from start to goal, EXCLUDING start / INCLUDING goal, or `[]` if unreachable. Recompute when the
  target moves; small grids only.
```js
const cols = tm.w, rows = tm.h, cell = tm.tile;
const passable = (x, y) => !tm.solidAt(x, y);
const from = { x: Math.floor(creep.x / cell), y: Math.floor(creep.y / cell) };
const goal = { x: Math.floor(base.x / cell),  y: Math.floor(base.y / cell) };
const path = kit.astar(from, goal, passable, { cols, rows });
if (path.length) {
  const to = { x: (path[0].x + 0.5) * cell, y: (path[0].y + 0.5) * cell };   // cell -> px centre
  kit.seek(creep, to, 60); kit.integrate(creep, dt);
}
```

## Grid / turn games  (roguelike, sokoban, tactics — discrete, one step per key press)
Position entities on a cell grid (`e.x = cx*cell`), and move ONE cell per `input.pressed(key)` (not
per frame). Keep the whole board in state; act on a keypress, then let enemies take their turn.
- `kit.gridMove(e, dx, dy, cell, passable)` — snap `e` one cell in (dx,dy) if `passable(cx,cy)`;
  returns whether it moved. `dx,dy` ∈ {-1,0,1}. Pair with `kit.astar` for enemy turns.
```js
update(dt, input, kit) {
  const cell = 32, pass = (x,y) => !this.state.walls.has(x+","+y);
  if (input.pressed("ArrowRight") && kit.gridMove(this.state.player, 1, 0, cell, pass)) this.enemyTurn(kit, cell, pass);
  // ...one branch per direction; enemyTurn moves each foe one astar step toward the player
}
```

## Particles / juice  (feel — cheap quality)
- `kit.burst(world, x, y, n=12, {speed,life,color,size})` — spawn a radial burst of short-lived particles.
- `kit.stepParticles(world, dt)` — advance + cull them each update. Draw survivors (`e.particle`)
  as `e.w`×`e.h` rects; fade with `e.life / e.maxLife`. Great for hits, pickups, explosions.
```js
if (hit) kit.burst(this.state.world, enemy.x, enemy.y, 16, { color: "#f80" });
kit.stepParticles(this.state.world, dt);
```

## Camera  (world larger than the screen)
- `kit.makeCamera()` → `{ x, y, follow(target, worldW?, worldH?) }`. Call `cam.follow(player,
  levelWidth, levelHeight)` in update; it centers on the target and clamps to the world bounds.
- Store it in `state.cam` — the engine renders the scene through it. The HUD is unaffected.

## Input  (read in update)
- `input.down(key)` — held right now. Use THIS for continuous movement (`if (input.down("d")) x += …`).
- `input.pressed(key)` — true only on the FRAME the key goes down (edge). Use THIS for one-shot actions
  (fire, jump, attack, menu select). Do NOT drive movement off `pressed` — it fires once, not while
  held; and never record `pressed` keys into a set you forget to clear (the key "sticks on" forever).
- `input.pointer` → `{x, y, down}` (mouse position + button).
- Keys: single chars are lowercase (`"w"`, `"s"`, `" "` for space); arrows are
  `"ArrowUp"`, `"ArrowDown"`, `"ArrowLeft"`, `"ArrowRight"`.
- Colors passed to entities/draw are CSS strings and MUST include the leading `#` (`"#f00"`).

## Actions (register key presses)
**Rule: EVERY non-movement control in the spec's `controls` map gets a `kit.register` in init.**
Movement keys stay with the control scheme (the scaffold wires them) — never registered. The engine
fires the handler on the PRESSED edge of any bound key, after update each frame; a re-register with
the same name replaces (safe on re-init). `kit.bindings()` → `[{name, keys}]`. Held mechanics like
charging may still read `input.down` per frame; registration targets EDGE actions.
```js
init(state, kit) {
  kit.register("attack", [" "], () => {           // spec: "SPACE: attack"
    const foe = nearestEnemy(state);              // handlers may read state for range/aim
    if (foe) { foe.hp -= 1; kit.burst(state.world, foe.x, foe.y, 10); }
  });
  // NOTE: an interact/use/talk key is the SCAFFOLD's `activate` — see "Activate" below. Register
  // your own only for a verb with no target (a self-verb like dash), never to re-do targeting.
}
```

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

## Scalars
- `kit.clamp(v, lo, hi)` — keep a number in range. Distances are `Math.hypot(dx, dy)`.

## Dialogue, quests & toasts  (depth without ending the game)
- `kit.talkOpen(state, npc, options?)` / `kit.talkStep(state, input)` / `kit.talkHud(state)` — the
  WHOLE talk loop as one primitive: npc = `{name, lines:[...], options?:[...]}`; talkStep (call every
  frame) advances on the activate key, returns `{npc, pick}` when a choice is made, closes on Escape;
  GENERATED main.ts already appends `kit.talkHud(state)` after your items, so hud() need not — and
  spreading it too is harmless, the kit draws the talk UI once. A shop = talkOpen with priced options.
  The scaffold already calls talkStep and opens a talkable target for you. `state.talk` belongs to
  the kit — `{npc, line, options}` — so never read a field of your own off it and never draw the
  dialogue yourself: one build invented `state.talk.currentLine` and painted "undefined" on screen.
- `kit.quest.add(state, {id,title,reward})` · `.complete(state,id)` (announces itself; returns the
  quest — pay its reward yourself) · `.isDone(state,id)` · `...kit.quest.log(state)` in hud().
- `kit.notify(msg)` — transient toast the engine draws for a few seconds ("Got 10 gold").

## Ending the game
- `kit.win(msg)` / `kit.lose(msg)` — end play (idempotent; first call wins). `kit.over` is
  `null` or `{won, msg}`. The runner stops calling update once over and shows `msg`. Call these ONLY
  for the spec's definite ending — a quest/milestone completing is `kit.quest.complete`/`kit.notify`
  and play continues.

## Making something visible
There is no draw api. An entity renders because it is IN `state.world` and carries its own look:
- `shape` ("rect"|"circle") + `color`, or `parts` for a compound look, or `sprite` (an asset id).
- `w`/`h` size it, `x`/`y` are its TOP-LEFT, `layer` orders it (low first).
A thing that is not an entity cannot be drawn, so make it one — the arena border, a health pip in
the world, an explosion flash are all entities with a `shape` and a lifetime.

## Data-driven entities  (a data row IS the thing's look)
When the game has DATA (`game/data/*.json` → `./data.ts`), a row already describes its own
appearance — `size` (PIXELS in 2D, use as-is), optional `shape` ("rect"|"circle") and `color` — and
its `id` is ALSO the art id the asset stage renders to. So spawn from the row and draw through the
kit, and the game is skinnable with no code change:
```ts
import { ENEMIES } from "./data.ts";
const e = kit.spawn(state.world, { type: "goblin", x: 100, y: 60 });  // "goblin" is a row id
// the row's size/shape/color came with it, and so did `sprite: "goblin"`. Nothing else to do:
// it is in state.world, so the engine renders it — the sprite if the art exists, the row's
// shape+color if it does not.
```
- **`type` is the row id.** That is the whole binding: name the row and the entity IS that thing,
  look and art included. Match it the same way — `e.type === "goblin"` — and never invent a second
  naming scheme for something that already has one.
- Anything you pass alongside WINS: position, velocity, per-instance stats, an override colour.
- A `type` with no row (a bullet, a particle) spawns exactly what you wrote — one call for
  everything, so there is no second spawn to remember and no way to build a row's entity unbound.
- Need a row whose id is not the type you want? Pass it: `kit.spawn(world, { row: ENEMIES[0], x, y })`.
A COMPOUND look is data too — `parts` (sub-shapes in fractions of the box) draws a ship's hull+fin
or a slime's eyes, and the engine renders them, so it is STILL skinnable (one sprite replaces
every part). NEVER re-scale a row's `size`, and never
hand-pick a color for a row that has one.

