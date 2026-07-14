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
   `{kind:"banner", text, color?}` (centered). `at` is an anchor: `"top-left"`, `"top"`, `"top-right"`,
   `"left"`, `"center"`, `"right"`, `"bottom-left"`, `"bottom"`, `"bottom-right"` (default `"top-left"`);
   same-anchor items stack. `hud()` reads state, never mutates. Omit it entirely if the game needs no HUD.
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

## Control schemes  (PICK ONE complete camera+movement combo — do NOT hand-roll)
Hand-rolled movement/camera is the #1 source of broken games (keys that stick, forgotten dt, a camera
that doesn't track the player, movement that ignores where the camera points). Do NOT invent your own —
CHOOSE the scheme that fits the game and wire its matched pair. Each row is complete and tied together:

| Feel | Movement (in `update`) | Camera (in `camera` hook) |
|------|------------------------|---------------------------|
| **Orbital** (3D platformer, marble, collectathon) — drag orbits the view, WASD moves relative to it | `kit.moveRelative(player, input, dt, 10)` | `kit.chaseCam(cam, this.state.player)` |
| **Follow-travel** (top-down-ish hero) — camera just trails wherever you move | `kit.moveTopDown3(player, input, dt, 10)` | `kit.chaseCam(cam, this.state.player, { faceYaw: true })` |
| **Vehicle** (ship/car/shark) — turn then drive | `kit.moveTank3(player, input, dt, {})` | `kit.chaseCam(cam, this.state.player, { faceYaw: true })` |
| **First-person** (set `config.pointerLock:true`) | `kit.mouseLook(player, input); kit.moveFP(player, input, dt, 6)` | `kit.fpCam(cam, this.state.player)` |

```js
update(dt, input, kit) {
  kit.moveRelative(this.state.player, input, dt, 10);   // orbital: WASD relative to the camera
}
camera(cam, kit) { kit.chaseCam(cam, this.state.player); }
```
- `kit.moveRelative(e, input, dt, speed=8)` — WASD relative to `input.camYaw` (the camera's heading,
  filled by the runtime): W into the screen, A/D strafe. Movement and camera stay tied as you orbit.
- `kit.moveTopDown3(e, input, dt, speed=8)` — omni on the x/z plane in WORLD axes; sets `e.ry` to face
  travel (so `chaseCam faceYaw` trails you). Use when no manual camera orbit is wanted.
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
