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
1. **No 2D HUD.** In `mode:"3d"` the runtime NEVER calls your `draw()` — there is no text/overlay
   layer. Leave `draw` empty and show EVERYTHING with 3D entities: no score text, no menus, no bars.
   (A "health bar" = a row of small box entities you add/remove; "whose turn" = a marker box you
   move above the active thing.)
2. **Only position + `ry` update live.** Each frame the renderer re-reads an entity's x/y/z and `ry`
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

## Movement  (steer the player — USE THIS, do NOT hand-roll WASD + dt)
Hand-rolled movement is the #1 source of broken games (keys that stick on, moving one frame instead
of while held, forgotten dt). Call ONE controller per steered entity, every frame in `update`:
```js
update(dt, input, kit) {
  kit.moveTopDown3(this.state.player, input, dt, 10);   // omni: WASD/arrows glide on the ground, faces travel
}
```
- `kit.moveTopDown3(e, input, dt, speed=8)` — omni-directional on the x/z plane (y untouched); sets
  `e.ry` to face travel. The default for a creature/hero you steer directly.
- `kit.moveTank3(e, input, dt, {speed, turn, back})` — W/S drive forward/back along `e.ry`, A/D turn.
  For a ship/car/shark where turning-then-driving feels right.
- For a flyer with pitch, use `kit.flyer` (below). These read HELD keys and apply dt for you — never
  reach into `input.pressed` for movement, and never accumulate keys into a set.

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
