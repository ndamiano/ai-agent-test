# Kit API — the primitives a game composes

A game is one ES module: `export function createGame(kit) { return { ...game } }`.
The game object has this exact shape:

```js
export function createGame(kit) {
  return {
    config: { width: 640, height: 480, title: "", background: "#111", gravity: 0, seed: 1 },
    state:  { world: [] },          // anything; put entities in state.world (or state.entities)
    init(kit)              { },      // one-time setup; spawn entities into state.world
    update(dt, input, kit) { },      // advance the sim ONE step (dt = seconds). NEVER draw here.
    draw(g, kit)           { },      // render state via `g`. NEVER change state here.
  };
}
```

**Law: update mutates state and never draws; draw reads state and never mutates.** The sim must
run with no canvas (it's tested headless). Keep all randomness in `kit.rng` (seeded/reproducible).

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
  `solidsNear(e)` returns the solid tile rects overlapping entity `e` (feed each to resolveAabb).

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

## Steering  (enemy/NPC movement — chase, flee, patrol; velocities in px/SECOND)
Set an entity's velocity toward or away from a target, then `kit.integrate` it. A `target` is any
`{x,y}` (an entity or a point); sized entities aim at each other's centres automatically.
- `kit.seek(e, target, speed)` — steer straight at the target. Returns the distance to it.
- `kit.flee(e, target, speed)` — steer directly away.
- `kit.arrive(e, target, speed, slow=80)` — seek but ease to a stop within `slow` px (no jitter).
- `kit.pursue(e, target, speed, lead=0.3)` — aim where a moving target is heading (intercept).
- `kit.wander(e, speed, rng, turn=3)` — random drift (idle patrol); pass `kit.rng`.
```js
for (const ghost of this.state.enemies) {
  const dist = kit.seek(ghost, this.state.player, 90);   // chase the player at 90 px/s
  kit.integrate(ghost, dt);                               // apply the velocity (dt handled inside)
  if (dist < 16) kit.lose("caught");
}
```

## Camera  (world larger than the screen)
- `kit.makeCamera()` → `{ x, y, follow(target, worldW?, worldH?) }`. Call `cam.follow(player,
  levelWidth, levelHeight)` in update; it centers on the target and clamps to the world bounds.
- In `draw`: `g.push(cam)` before drawing WORLD-space things, `g.pop()` before HUD/score. Store the
  camera in `this.state.cam`.

## Input  (read in update)
- `input.down(key)` — held right now.  `input.pressed(key)` — just pressed THIS frame (edge).
- `input.pointer` → `{x, y, down}` (mouse position + button).
- Keys: single chars are lowercase (`"w"`, `"s"`, `" "` for space); arrows are
  `"ArrowUp"`, `"ArrowDown"`, `"ArrowLeft"`, `"ArrowRight"`.

## Randomness
- `kit.rng.next()` 0..1 · `.range(lo,hi)` · `.int(lo,hi)` inclusive · `.pick(arr)` · `.chance(p)` bool.

## Vectors  `kit.V`
- `add,sub,scale(a,k),len(a),norm(a)` and `clamp(v,lo,hi)` (clamp a scalar).

## Ending the game
- `kit.win(msg)` / `kit.lose(msg)` — end play (idempotent; first call wins). `kit.over` is
  `null` or `{won, msg}`. The runner stops calling update once over and shows `msg`.

## Draw api  `g`  (draw only)
- `g.clear(color)` — fill the screen. (The runner already clears to `config.background`.)
- `g.rect(x,y,w,h,color)` · `g.circle(x,y,r,color)` · `g.line(x1,y1,x2,y2,color,width=1)`
- `g.text(str,x,y,color="#fff",size=16,align="left")`  (align: "left"|"center"|"right")
- `g.sprite(img,x,y,w,h)` — draw a loaded image (asset pipeline supplies these later).

## Audio  (stub for now — safe to call)
- `kit.audio.play(name)` — no-op until a backend is wired; never crashes.
