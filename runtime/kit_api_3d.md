# Kit API — 3D games

A 3D game is the SAME shape as a 2D one, with `config.mode: "3d"` and one extra optional hook.
You write PURE simulation — mutate entity positions in 3D — and TAG each entity with a shape.
**You never write any three.js / WebGL.** The runtime renders your entities as meshes.

```js
export function createGame(kit) {
  return {
    config: { mode: "3d", width: 1280, height: 720, background: "#101018", seed: 1 },
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
`color` is a CSS string and MUST include the leading `#` (`"#c33"`, `"#33cc55"`) — a bare hex like
`"cc3333"` renders as the wrong color. `y` is the entity's CENTER. Entities with no `shape` are
invisible (pure logic markers).

**The player IS a world entity.** The character you control must be a SHAPE-TAGGED entity pushed into
`state.world`, and `state.player` must reference that SAME object — create it once with
`this.state.player = kit.spawn(this.state.world, { shape:"box", x,y,z, w,h,d, color })`. A bare
`state.player = { x, y, z, … }` kept OUTSIDE `state.world` is invisible AND the movement gate can't see
it move (dead-controls). One object, in the world, referenced by `state.player`.

**If a `world.ts` file is provided (a generated village), BUILD ON IT — do not author terrain/town
yourself.** Import it and use its API; the town, streets, and terrain already exist:
```ts
import { WORLD, spawnWorld, heightAt } from "./world.ts";
// init(kit): spawn the village, then put the player ON the ground at the plaza
spawnWorld(this.state.world);
const px = WORLD.plaza.x, pz = WORLD.plaza.z;
this.state.player = kit.spawn(this.state.world, { shape:"box", x:px, y:heightAt(px,pz)+0.9, z:pz, w:0.8,h:1.7,d:0.8, color:"#28303a" });
// place NPCs/items relative to WORLD.buildings (each has {x,z,w,d,label}) or on WORLD.grass ([x,z] cells)
// update(dt,input,kit): after kit.drive, keep the player on the terrain EVERY frame:
this.state.player.y = heightAt(this.state.player.x, this.state.player.z) + 0.9;
```
`WORLD.buildings` (`[{id,label,x,z,w,d,h,color}]`, `label` = kind e.g. "market stall"), `WORLD.plaza
{x,z}`, `WORLD.gate {x,z}`, `WORLD.grass [[x,z],…]`. `heightAt(x,z)` is the ground height — every entity's
`y` should be `heightAt(x,z) + halfHeight`. Use `controls:"orbital"`, no camera hook.

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
   selected?, title?, at?, color?}` (a numbered choice list). `at` is an anchor: `"top-left"`, `"top"`,
   `"top-right"`, `"left"`, `"center"`, `"right"`, `"bottom-left"`, `"bottom"`, `"bottom-right"` (default
   `"top-left"`); same-anchor items stack. `hud()` reads state, never mutates. Omit it if the game needs no HUD.

   **Conversation, quests, shops, upgrades — build them from `panel` + `menu` (this is how a game gets
   DEPTH).** The engine only DRAWS these; the SIM owns every bit of state and choice (sim/render law).
   The pattern: when the player is near an NPC and presses an interact key, open a talk state; render it;
   read a number key to pick a choice; branch. NEVER `console.log` dialogue (invisible) or cram a
   conversation into a `banner`.
   You MUST wire the WHOLE loop — open, advance the lines, and ACT on the choice. A menu that only
   renders (no `kit.menuPick` branch) is a dead menu: the player sees choices but can't pick them. When
   `state.talk` is set, DON'T just `return` after an Escape check — read `kit.menuPick(input)` and branch.
   ```ts
   // update(): open → advance → CHOOSE. The SIM drives it all, on plain state.
   const t = this.state.talk;
   if (t) {
     const npc = t.npc, atChoice = t.line >= npc.lines.length - 1;   // last line shows the choices
     if (!atChoice && input.pressed("e")) { t.line++; return; }      // advance dialogue
     if (atChoice) {
       const pick = kit.menuPick(input);                             // 0-based: which number key
       if (pick === 0) { this.state.quests.push(makeQuest(npc)); this.state.talk = null; }   // Accept
       else if (pick === 1) this.state.talk = null;                  // Decline / Leave
     }
     if (input.pressed("Escape")) this.state.talk = null;
     return;                                                         // movement paused while talking
   }
   const near = this.state.npcs.find(n => Math.hypot(n.x-p.x, n.z-p.z) < 3);
   if (near && input.pressed("e")) this.state.talk = { npc: near, line: 0 };
   // hud(): render the current talk state as a panel (the line) + a menu (the choices, only at the end)
   hud() {
     const t = this.state.talk; if (!t) return [/* normal hud */];
     const items = [{ kind: "panel", title: t.npc.name, text: t.npc.lines[t.line], at: "bottom" }];
     if (t.line >= t.npc.lines.length - 1)
       items.push({ kind: "menu", options: ["Accept the quest", "Not now"], at: "center" });
     return items;
   }
   ```
   A SHOP/UPGRADE is the same shape: a `menu` of `["Speed +1 (10 coins)", "Bag +1 (15 coins)", "Leave"]`,
   and in update() `const pick = kit.menuPick(input)` → if `pick===0 && state.coins>=10` spend and bump the
   stat. A QUEST LOG is a `panel` whose `text` is your active quests joined with `\n`. Real depth = these
   small interactive loops (talk → choose → consequence), not more collectibles.
2. **Only position + `ry` update live** for WORLD entities. Each frame the renderer re-reads an entity's x/y/z and `ry`
   only — NOT its size (w/h/d/r) or color (those bake when the entity first appears). To change how
   much health shows, ADD or REMOVE entities (splice pip boxes from `state.world`); to show a hit,
   MOVE the entity (a lunge/recoil), never recolor or resize it.

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
- `kit.physics3(e, dt, gravity=20, ground=0)` — integrate3 + land on the y=ground plane; sets
  `e.grounded`. For a 3D platformer/collectathon where things fall and stand on the floor.
- `kit.spawn(world, {...})`, `kit.cull(world)`, `kit.rng`, `kit.V` (clamp/len/norm), `kit.aabb`
  (works on x/y as before — for 3D distance use `Math.hypot(dx,dy,dz)`), `kit.win(msg)/kit.lose(msg)`.
- Randomness ONLY via `kit.rng` — `kit.rng.range(lo,hi)`, `kit.rng.int(lo,hi)`, `kit.rng.pick(arr)`,
  `kit.rng.chance(p)`. NEVER `Math.random()` (it breaks the deterministic headless gate).

## Effects, collision & bounds  (3D-specific — READ THIS, the 2D kit misleads here)
- **NO particles in 3D.** `kit.burst` / `kit.stepParticles` are 2D-only — they spawn shape-less x/y
  particles the 3D renderer can't draw, and a wrong arg count spawns millions and OOMs. For a 3D
  "pop"/"poof", spawn a few short-lived `sphere` entities yourself with an upward `vy`, `integrate3`
  them, and `kit.cull` when a `life` counter expires — or just skip the effect. Do NOT call `kit.burst`.
- **There is NO 3D solid-collision primitive.** Do NOT hand-roll an AABB loop against the world — a
  town's road/ground slabs are huge boxes the player overlaps every frame, which pins the player in
  place (the #1 broken-3D-movement bug). Two correct options: (a) leave the town OPEN (drive freely
  past buildings — fine for an explore/collect game), or (b) keep the player inside the map with
  `player.x = kit.V.clamp(player.x, -HALF, HALF)` (same for z). Collect/trigger on DISTANCE only:
  `if (Math.hypot(px-e.x, py-e.y, pz-e.z) < R) { ...collect... }`.
- **Animate decoration/pickups by MUTATING position** (allowed live; color/size bake). A floating
  balloon or a hovering marker bobs: `e.y = baseY + Math.sin(kit's t * 2) * 0.5` (keep a `t += dt` in
  state). A spinning pickup turns: `e.ry += dt`. This is how a scene feels alive.

## Composing a 3D scene that READS as its subject (the gates can't see "looks good" — you must)
The scene is ONLY your shape-tagged entities, so BUILD it deliberately — a flat monochrome grid reads
as nothing. For a town: a grass `ground`, gray road strips, and buildings that VARY (`kit.rng` their
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
impossible). Pick the row that fits:

| `config.controls` | Feel | What you get |
|-------------------|------|--------------|
| `"orbital"` | third-person hero / platformer / RPG (Skyrim-ish) | drag orbits the view; WASD moves relative to it; chase cam. **Default 3D choice.** |
| `"follow"` | top-down-ish hero | WASD in world axes; camera trails your travel |
| `"vehicle"` | ship / car / shark | W/S drive along facing, A/D turn; chase cam behind heading |
| `"fp"` | first person (shooter/explorer) | mouse-look aims; WASD relative to aim; eye camera. Captures the pointer itself — no `pointerLock` needed |

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
- `kit.moveTopDown3(e, input, dt, speed=8)` — omni on the x/z plane in WORLD axes; sets `e.ry` to face travel.
- `kit.moveTank3(e, input, dt, {speed, turn, back})` — W/S drive along `e.ry`, A/D turn.
- For a flyer with pitch, use `kit.flyer` (below). These read HELD keys and apply dt for you — never
  reach into `input.pressed` for movement, and never accumulate keys into a set. Momentum/physics ball?
  Still call a controller for direction; do NOT hand-roll velocity from raw key checks.

## First-person  (set `config.pointerLock: true`)
For a first-person game, put `pointerLock: true` in config. Clicking the canvas locks the mouse;
mouse motion becomes look, mouse clicks become `input.pointer.down`. Steer with three primitives:
```js
update(dt, input, kit) {
  kit.mouseLook(this.state.player, input);        // turn player.yaw / player.pitch from the mouse
  kit.moveFP(this.state.player, input, dt, 6);    // WASD relative to facing (W = look dir, A/D strafe)
  if (input.pointer.down) { /* attack: hit along the aim */ }
}
camera(cam, kit) { kit.fpCam(cam, this.state.player); }   // eye at the player, looking along the aim
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

## Law
`update` mutates state and never renders; the runtime handles all drawing. Keep the sim pure so it
runs headless. Randomness only via `kit.rng`.
