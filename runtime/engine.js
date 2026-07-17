// engine.js — the primitive kit. The fat library a generated game composes.
//
// Design law: SIM is separate from RENDER.
//   - A game's update(dt, input, kit) mutates plain state and NEVER draws.
//   - A game's draw(g, kit) only reads state and issues draw calls.
// So the sim runs headless in plain Node (zero deps) — that's where a weak model's
// logic bugs surface for free — while render only needs a browser canvas.
//
// A generated game is an object (or a createGame(kit) factory returning one):
//   {
//     config: { width, height, title, background?, gravity? },
//     state:  { ...anything },
//     init(kit)            // optional one-time setup; may mutate this.state
//     update(dt, input, kit)   // advance the sim one step. dt in seconds.
//     draw(g, kit)         // render this.state via the draw api `g`
//     onWin?() / onLose?() // optional; the game decides when to call kit.win()/lose()
//   }

// ── deterministic RNG (seeded — headless runs must reproduce) ─────────────────
export function makeRng(seed = 1) {
  let s = seed >>> 0 || 1;
  // The rng IS a function — `kit.rng()` returns a float in [0,1) — and also carries the named
  // helpers (`.next/.range/.int/.pick/.chance`). Both forms work: the model reaches for `kit.rng()`
  // as often as `kit.rng.next()`, so meet it instead of tripping a not-callable error.
  const next = () => {
    // xorshift32
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 0xffffffff;
  };
  next.next = next;
  next.range = (lo, hi) => lo + next() * (hi - lo);
  next.int = (lo, hi) => Math.floor(lo + next() * (hi - lo + 1));
  next.pick = (arr) => arr[Math.floor(next() * arr.length)];
  next.chance = (p) => next() < p;
  next.shuffle = (arr) => {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(next() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  };
  return next;
}

// ── vec2 helpers ─────────────────────────────────────────────────────────────
export const V = {
  add: (a, b) => ({ x: a.x + b.x, y: a.y + b.y }),
  sub: (a, b) => ({ x: a.x - b.x, y: a.y - b.y }),
  scale: (a, k) => ({ x: a.x * k, y: a.y * k }),
  len: (a) => Math.hypot(a.x, a.y),
  norm: (a) => { const l = Math.hypot(a.x, a.y) || 1; return { x: a.x / l, y: a.y / l }; },
  clamp: (v, lo, hi) => Math.max(lo, Math.min(hi, v)),
};

// ── entities + physics ───────────────────────────────────────────────────────
// An entity is a plain object. The kit only assumes optional fields:
//   x, y (position), vx, vy (velocity), w, h (AABB size), dead (cull flag).
export function spawn(world, ent) {
  const e = { x: 0, y: 0, vx: 0, vy: 0, w: 0, h: 0, dead: false, ...ent };
  world.push(e);
  return e;
}
export function cull(world) {
  for (let i = world.length - 1; i >= 0; i--) if (world[i].dead) world.splice(i, 1);
}
// Semi-implicit Euler integration with optional gravity (px/s^2).
export function integrate(e, dt, gravity = 0) {
  e.vy += gravity * dt;
  e.x += e.vx * dt;
  e.y += e.vy * dt;
}
// 3D integration (y is UP; gravity pulls -y). Units are world-units/SECOND, dt handled here.
export function integrate3(e, dt, gravity = 0) {
  e.vy = (e.vy || 0) - gravity * dt;
  e.x += (e.vx || 0) * dt;
  e.y += (e.vy || 0) * dt;
  e.z += (e.vz || 0) * dt;
}
// 3D ground physics: integrate + land on the y=ground plane (sets e.grounded). For a 3D
// platformer/collectathon where things fall and stand on the floor.
export function physics3(e, dt, gravity = 20, ground = 0) {
  e.vy = (e.vy || 0) - gravity * dt;
  e.x += (e.vx || 0) * dt; e.y += (e.vy || 0) * dt; e.z += (e.vz || 0) * dt;
  // y is the entity CENTER (that's where the mesh renders), so rest the BOTTOM on the ground —
  // landing the center at `ground` would half-bury every entity.
  const rest = ground + (e.h != null ? e.h / 2 : (e.r || 0));
  if (e.y <= rest) { e.y = rest; e.vy = 0; e.grounded = true; } else e.grounded = false;
}
// Unit facing vector from yaw (around +y) and pitch (up/down). +z is "forward" at yaw 0.
export function heading3(yaw = 0, pitch = 0) {
  const cp = Math.cos(pitch);
  return { x: Math.sin(yaw) * cp, y: Math.sin(pitch), z: Math.cos(yaw) * cp };
}
// Flyer: the whole 3D flight step a small model keeps getting wrong (orientation + thrust along
// facing + drag). Reads input, steers e.yaw/e.pitch, thrusts along its facing, integrates. Keys
// override defaults. Sets e.ry = yaw so a box entity renders turned. Speeds in units/SECOND.
export function flyer(e, input, dt, opts = {}) {
  const { thrust = 30, turn = 1.5, climb = 1.2, drag = 0.4 } = opts;
  const k = { yawL: "ArrowLeft", yawR: "ArrowRight", up: "ArrowUp", down: "ArrowDown", go: " ", ...(opts.keys || {}) };
  e.yaw = (e.yaw || 0) + ((input.down(k.yawL) ? 1 : 0) - (input.down(k.yawR) ? 1 : 0)) * turn * dt;
  e.pitch = Math.max(-1.2, Math.min(1.2,
    (e.pitch || 0) + ((input.down(k.up) ? 1 : 0) - (input.down(k.down) ? 1 : 0)) * climb * dt));
  const f = heading3(e.yaw, e.pitch);
  if (input.down(k.go)) { e.vx = (e.vx || 0) + f.x * thrust * dt; e.vy = (e.vy || 0) + f.y * thrust * dt; e.vz = (e.vz || 0) + f.z * thrust * dt; }
  const d = Math.max(0, 1 - drag * dt);
  e.vx = (e.vx || 0) * d; e.vy = (e.vy || 0) * d; e.vz = (e.vz || 0) * d;
  e.x += e.vx * dt; e.y += e.vy * dt; e.z += e.vz * dt;
  e.ry = e.yaw;
  return e;
}
// Axis-aligned bounding-box overlap (x,y is top-left).
export function aabb(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}
// Resolve `a` out of static `b` along the minimum-penetration axis; returns the hit side.
export function resolveAabb(a, b) {
  const dx1 = b.x + b.w - a.x, dx2 = a.x + a.w - b.x;
  const dy1 = b.y + b.h - a.y, dy2 = a.y + a.h - b.y;
  const px = Math.min(dx1, dx2), py = Math.min(dy1, dy2);
  if (px < py) {
    if (dx1 < dx2) { a.x += px; a.vx = 0; return "left"; }
    a.x -= px; a.vx = 0; return "right";
  }
  if (dy1 < dy2) { a.y += py; a.vy = 0; return "top"; }
  a.y -= py; a.vy = 0; return "bottom";
}

// ── platformer physics (px/SECOND; dt handled inside — the model never touches vx*dt) ──
// solids = array of AABB rects {x,y,w,h} (from tilemap.solidsNear(e) or a platform list).
// Axis-separated sweep with ground detection. This is THE primitive a small model
// keeps getting wrong when it hand-rolls gravity/jump in per-frame units.
export function walk(e, dir, speed) { e.vx = dir * speed; }          // dir -1|0|1, speed px/s
export function jump(e, speed) { if (e.grounded) { e.vy = -speed; e.grounded = false; } }
export function physics(e, dt, solids = [], gravity = 2000) {
  e.vy += gravity * dt;
  e.x += e.vx * dt;                                   // horizontal, then resolve
  for (const s of solids) {
    if (!aabb(e, s)) continue;
    e.x = e.vx > 0 ? s.x - e.w : s.x + s.w; e.vx = 0;
  }
  e.grounded = false;
  e.y += e.vy * dt;                                   // vertical, then resolve
  for (const s of solids) {
    if (!aabb(e, s)) continue;
    if (e.vy > 0) { e.y = s.y - e.h; e.grounded = true; } else { e.y = s.y + s.h; }
    e.vy = 0;
  }
}

// ── steering (px/SECOND; sets velocity toward/away a target — chase AI, creeps, patrols) ──
// A `target` is anything with x,y (an entity or a bare point). center() uses w,h when present so a
// sized chaser aims at a sized quarry's middle, not its corner. Set velocity here, then integrate.
function center(e) { return { x: e.x + (e.w || 0) / 2, y: e.y + (e.h || 0) / 2 }; }
export function seek(e, target, speed) {                 // steer straight at the target
  const d = V.sub(center(target), center(e)); const n = V.norm(d);
  e.vx = n.x * speed; e.vy = n.y * speed; return V.len(d);
}
export function flee(e, target, speed) {                 // steer directly away
  const n = V.norm(V.sub(center(e), center(target)));
  e.vx = n.x * speed; e.vy = n.y * speed;
}
export function arrive(e, target, speed, slow = 80) {    // seek, but ease to a stop within `slow` px
  const d = V.sub(center(target), center(e)); const dist = V.len(d); const n = V.norm(d);
  const s = dist < slow ? speed * (dist / slow) : speed;
  e.vx = n.x * s; e.vy = n.y * s; return dist;
}
export function pursue(e, target, speed, lead = 0.3) {   // seek where a moving target is headed
  const aim = { x: target.x + (target.vx || 0) * lead, y: target.y + (target.vy || 0) * lead,
                w: target.w, h: target.h };
  return seek(e, aim, speed);
}
export function wander(e, speed, rng, turn = 3) {        // drift, turning by up to `turn` rad/step
  e._heading = (e._heading ?? (rng ? rng.next() * 6.283 : 0)) + (rng ? (rng.next() - 0.5) * turn : 0);
  e.vx = Math.cos(e._heading) * speed; e.vy = Math.sin(e._heading) * speed;
}

// ── 3D steering (NPCs on the ground plane: x/z move, y untouched — set it from the terrain) ──
// All of these APPLY dt themselves (no integrate3 needed) and set e.ry to face travel (a mesh at
// rotation.y=ry points to (-sin ry, -cos ry)). After steering, keep the NPC on the ground:
// `e.y = heightAt(e.x, e.z) + halfHeight`.
export function seek3(e, target, speed, dt) {      // walk straight at target's (x,z); returns distance left
  const dx = target.x - e.x, dz = target.z - e.z, d = Math.hypot(dx, dz);
  if (d > 1e-6) {
    const s = Math.min(speed * dt, d);
    e.x += dx / d * s; e.z += dz / d * s;
    e.ry = Math.atan2(-dx, -dz);
  }
  return d;
}
export function flee3(e, threat, speed, dt) {      // walk directly away from threat's (x,z)
  const dx = e.x - threat.x, dz = e.z - threat.z, d = Math.hypot(dx, dz) || 1;
  e.x += dx / d * speed * dt; e.z += dz / d * speed * dt;
  e.ry = Math.atan2(-dx / d, -dz / d);
}
export function wander3(e, speed, dt, rng, turn = 2) {   // amble: drift on x/z, slowly turning
  e._heading = (e._heading ?? (rng ? rng.next() * 6.283 : 0)) + (rng ? (rng.next() - 0.5) * turn * dt * 6 : 0);
  const dx = Math.sin(e._heading), dz = Math.cos(e._heading);
  e.x += dx * speed * dt; e.z += dz * speed * dt;
  e.ry = Math.atan2(-dx, -dz);
}
export function patrol3(e, points, speed, dt, arriveAt = 0.8) {  // walk a route of {x,z} (or [x,z]) points, looping
  if (!points || !points.length) return;
  e._wp = e._wp ?? 0;
  const p = points[e._wp % points.length];
  if (seek3(e, { x: p.x ?? p[0], z: p.z ?? p[1] }, speed, dt) < arriveAt)
    e._wp = (e._wp + 1) % points.length;
}
// Push an entity out of centered footprint rects [{x,z,w,d}] (e.g. WORLD.buildings) — call AFTER
// moving it (player or NPC) so walkers slide around buildings instead of through them. pad stays
// SMALL: a town's streets are ~2 units wide, and pad + half the walker's width comes out of every
// gap on both sides — a fat pad seals the alleys and wedges walkers at spawn.
export function avoidRects(e, rects, pad = 0.1) {
  for (const r of rects) {
    const hw = r.w / 2 + pad + (e.w || 0) / 2, hd = r.d / 2 + pad + (e.d || 0) / 2;
    const dx = e.x - r.x, dz = e.z - r.z;
    if (Math.abs(dx) < hw && Math.abs(dz) < hd) {
      const px = hw - Math.abs(dx), pz = hd - Math.abs(dz);
      if (px < pz) e.x = r.x + (dx < 0 ? -hw : hw); else e.z = r.z + (dz < 0 ? -hd : hd);
    }
  }
}

// ── grid pathfinding (A* on a cell grid — tower-defense creeps, chase-with-walls, tactics) ──
// passable(cx,cy) -> bool. Returns the cell path from `start` to `goal` (each {x,y} in CELL coords),
// EXCLUDING start, INCLUDING goal — or [] if unreachable. 4-directional unless diagonal:true. Small
// grids only (open list is a linear scan); cheap enough to recompute when the target moves.
export function astar(start, goal, passable, { cols = 1e4, rows = 1e4, diagonal = false } = {}) {
  const key = (x, y) => y * cols + x;
  const h = (x, y) => Math.abs(x - goal.x) + Math.abs(y - goal.y);
  const open = [{ x: start.x, y: start.y, g: 0, f: h(start.x, start.y) }];
  const came = new Map(), gScore = new Map([[key(start.x, start.y), 0]]);
  const dirs = diagonal ? [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]]
    : [[1, 0], [-1, 0], [0, 1], [0, -1]];
  while (open.length) {
    let bi = 0;
    for (let i = 1; i < open.length; i++) if (open[i].f < open[bi].f) bi = i;
    const cur = open.splice(bi, 1)[0];
    if (cur.x === goal.x && cur.y === goal.y) {
      const path = []; let px = cur.x, py = cur.y, k = key(px, py);
      while (came.has(k)) { path.unshift({ x: px, y: py }); const p = came.get(k); px = p.x; py = p.y; k = key(px, py); }
      return path;
    }
    for (const [dx, dy] of dirs) {
      const nx = cur.x + dx, ny = cur.y + dy;
      if (nx < 0 || ny < 0 || nx >= cols || ny >= rows || !passable(nx, ny)) continue;
      const ng = cur.g + 1, nk = key(nx, ny);
      if (ng < (gScore.get(nk) ?? Infinity)) {
        gScore.set(nk, ng); came.set(nk, { x: cur.x, y: cur.y });
        open.push({ x: nx, y: ny, g: ng, f: ng + h(nx, ny) });
      }
    }
  }
  return [];
}
// px point at the centre of cell (cx,cy) — move an actor toward path[0] with kit.seek + this.
export function cellCenter(cx, cy, cell) { return { x: (cx + 0.5) * cell, y: (cy + 0.5) * cell }; }

// ── grid / turn movement (roguelike, sokoban, tactics — discrete, one step per input) ──
// Snap-move an entity ONE cell in (dx,dy) if the destination is passable; returns whether it moved.
// A turn game reads input.pressed (one move per key press) and positions entities on cell*cell px.
export function gridMove(e, dx, dy, cell, passable = () => true) {
  const cx = Math.round(e.x / cell) + dx, cy = Math.round(e.y / cell) + dy;
  if (!passable(cx, cy)) return false;
  e.x = cx * cell; e.y = cy * cell; return true;
}

// ── particles / juice (feel — cheap quality; the game draws them as small rects) ──
// Spawn a radial burst of short-lived particles into `world`. Pass kit.rng for varied spread.
export function burst(world, x, y, n = 12, { speed = 120, life = 0.5, color = "#fd0", size = 3, rng } = {}) {
  for (let i = 0; i < n; i++) {
    const a = (rng ? rng.next() : i / n) * 6.283, s = speed * (rng ? 0.4 + rng.next() * 0.6 : 1);
    spawn(world, { x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, w: size, h: size,
                   life, maxLife: life, color, particle: true });
  }
}
// Advance every particle and cull the expired ones. Call once per update; draw the survivors
// (e.particle) as size×size rects, optionally fading with e.life / e.maxLife.
export function stepParticles(world, dt) {
  for (const e of world) if (e.particle) { e.x += e.vx * dt; e.y += e.vy * dt; e.life -= dt; if (e.life <= 0) e.dead = true; }
  cull(world);
}

// ── camera (scroll a world larger than the screen) ───────────────────────────
export function makeCamera(config) {
  return {
    x: 0, y: 0,
    follow(target, worldW, worldH) {                 // center on target, clamp to world bounds
      this.x = (target.x + (target.w || 0) / 2) - config.width / 2;
      this.y = (target.y + (target.h || 0) / 2) - config.height / 2;
      if (worldW != null) this.x = Math.max(0, Math.min(this.x, worldW - config.width));
      if (worldH != null) this.y = Math.max(0, Math.min(this.y, worldH - config.height));
    },
  };
}

// Third-person chase camera for 3D games: place the eye behind + above `target` and look AT it.
// Sets all six cam fields — eye (x,y,z) AND look-at (tx,ty,tz). This is a kit primitive precisely
// because the hand-rolled version reliably falls into the half-set trap: move the eye but forget the
// target, and the view stays pinned to the world origin. opts: {back, up, lookUp, faceYaw}.
export function chaseCam(cam, target, opts = {}) {
  if (!target) return;
  const back = opts.back ?? 16, up = opts.up ?? 12;
  const yaw = opts.faceYaw ? (target.ry || 0) : 0;  // follow the entity's heading, or a fixed rear view
  const tx = target.x || 0, ty = target.y || 0, tz = target.z || 0;
  cam.x = tx - Math.sin(yaw) * back;
  cam.y = ty + up;
  cam.z = tz + Math.cos(yaw) * back;
  cam.tx = tx; cam.ty = ty + (opts.lookUp ?? 1.5); cam.tz = tz;
}

// ── movement controllers: input → motion, correct by construction ────────────
// Movement is where hand-authored code breaks most (latched keys, edge-vs-held input, forgotten dt).
// These read HELD keys only, normalize diagonals, apply dt, and face the travel direction — so the
// game composes ONE call instead of re-deriving it. WASD + arrow keys both drive them.
function inputDir(input) {
  let x = 0, y = 0;
  if (input.down("a") || input.down("ArrowLeft")) x -= 1;
  if (input.down("d") || input.down("ArrowRight")) x += 1;
  if (input.down("w") || input.down("ArrowUp")) y -= 1;   // up/forward is -y (screen) / -z (world)
  if (input.down("s") || input.down("ArrowDown")) y += 1;
  const L = Math.hypot(x, y);
  return L > 0 ? { x: x / L, y: y / L, moving: true } : { x: 0, y: 0, moving: false };
}

// 2D top-down / omni: move on the x/y plane, face travel via `e.angle` (radians). speed = px/second.
export function moveTopDown(e, input, dt, speed = 150) {
  const d = inputDir(input);
  if (!d.moving) return;
  e.x += d.x * speed * dt; e.y += d.y * speed * dt;
  e.angle = Math.atan2(d.y, d.x);
}

// 3D top-down / omni: move on the ground plane (x/z; y is up, untouched), face travel via `e.ry`.
// speed = world-units/second. The natural control for a walker/creature you steer directly.
export function moveTopDown3(e, input, dt, speed = 8) {
  const d = inputDir(input);
  if (!d.moving) return;
  e.x += d.x * speed * dt; e.z += d.y * speed * dt;
  // face travel: a mesh at rotation.y=ry points to world (-sin ry, -cos ry), so this yaw makes the
  // model look where it moves.
  e.ry = Math.atan2(-d.x, -d.y);
}

// 3D tank: W/S drive forward/back along the current facing, A/D turn. opts {speed, turn(rad/s), back}.
export function moveTank3(e, input, dt, opts = {}) {
  const speed = opts.speed ?? 8, turn = opts.turn ?? 2.5, back = opts.back ?? 0.5;
  if (input.down("a") || input.down("ArrowLeft")) e.ry = (e.ry || 0) + turn * dt;   // A = counter-clockwise
  if (input.down("d") || input.down("ArrowRight")) e.ry = (e.ry || 0) - turn * dt;   // D = clockwise
  let f = 0;
  if (input.down("w") || input.down("ArrowUp")) f += 1;
  if (input.down("s") || input.down("ArrowDown")) f -= back;
  // drive along the mesh's actual facing (a mesh at rotation.y=ry points to world (-sin ry, -cos ry)),
  // so forward is where the model looks and away from a behind-camera — not toward it.
  if (f) { const ry = e.ry || 0; e.x -= Math.sin(ry) * speed * f * dt; e.z -= Math.cos(ry) * speed * f * dt; }
}

// ── first-person: mouse-look + eye camera + strafe movement (yaw 0 looks -z) ──
// Turn the player's aim from mouse-look deltas (needs config.pointerLock so run3d fills input.lookDX/DY).
export function mouseLook(player, input, sens = 0.0025) {
  player.yaw = (player.yaw || 0) + (input.lookDX || 0) * sens;
  player.pitch = Math.max(-1.4, Math.min(1.4, (player.pitch || 0) - (input.lookDY || 0) * sens));
}
// Put the camera at the player's eyes, looking along their yaw/pitch. Call in the camera(cam,kit) hook.
export function fpCam(cam, player, opts = {}) {
  const eye = opts.eye ?? 1.6, yaw = player.yaw || 0, pitch = player.pitch || 0, cp = Math.cos(pitch);
  cam.x = player.x || 0; cam.y = (player.y || 0) + eye; cam.z = player.z || 0;
  cam.tx = cam.x + Math.sin(yaw) * cp;
  cam.ty = cam.y + Math.sin(pitch);
  cam.tz = cam.z - Math.cos(yaw) * cp;
}
// WASD move relative to the player's yaw: W/S along look, A/D strafe. dt applied, y untouched.
export function moveFP(player, input, dt, speed = 6) {
  const yaw = player.yaw || 0;
  let f = 0, s = 0;
  if (input.down("w") || input.down("ArrowUp")) f += 1;
  if (input.down("s") || input.down("ArrowDown")) f -= 1;
  if (input.down("d") || input.down("ArrowRight")) s += 1;
  if (input.down("a") || input.down("ArrowLeft")) s -= 1;
  const L = Math.hypot(f, s);
  if (!L) return;
  const fx = Math.sin(yaw), fz = -Math.cos(yaw), rx = Math.cos(yaw), rz = Math.sin(yaw);
  player.x += (fx * f + rx * s) / L * speed * dt;
  player.z += (fz * f + rz * s) / L * speed * dt;
}

// ── third-person ORBITAL control: WASD moves relative to the CAMERA, not the world ──
// The player drags to orbit the view (run3d fills input.camYaw with the camera's ground heading);
// W drives away from the camera (into the screen), S toward it, A/D strafe. Pairs with any chase
// camera (chaseCam / the default follow) — movement and camera share one yaw, so they stay tied no
// matter where the player rotates the view. Sets e.ry to face travel. This is the "3D platformer"
// feel; use moveTopDown3 instead when the camera should just trail travel with no manual orbit.
export function moveRelative(e, input, dt, speed = 8) {
  const yaw = input.camYaw || 0;
  let f = 0, s = 0;
  if (input.down("w") || input.down("ArrowUp")) f += 1;
  if (input.down("s") || input.down("ArrowDown")) f -= 1;
  if (input.down("d") || input.down("ArrowRight")) s += 1;
  if (input.down("a") || input.down("ArrowLeft")) s -= 1;
  const L = Math.hypot(f, s);
  if (!L) return;
  const fx = Math.sin(yaw), fz = -Math.cos(yaw), rx = Math.cos(yaw), rz = Math.sin(yaw);
  const dx = (fx * f + rx * s) / L, dz = (fz * f + rz * s) / L;
  e.x += dx * speed * dt;
  e.z += dz * speed * dt;
  e.ry = Math.atan2(-dx, -dz);   // face travel (a mesh at rotation.y=ry points to (-sin ry, -cos ry))
}

// ── atomic 3D control scheme (one name → coherent mover + camera) ─────────────
// A whole 3D control feel is ONE choice: `config.controls`. `kit.drive` (called in update) runs the
// matching MOVER; run3d reads the SAME `config.controls` to wire the matching CAMERA (see engine3d
// schemeCamera). Because both derive from one name, the mover and camera can't be mismatched — the #1
// source of broken 3D (world-axis movement under a camera that doesn't rotate). Pick a name, call
// kit.drive, omit the camera hook.
export const CONTROL_SCHEMES = ["orbital", "follow", "vehicle", "fp"];

export function driveScheme(scheme, e, input, dt, speed = 8) {
  switch (scheme) {
    case "follow":  return moveTopDown3(e, input, dt, speed);   // WASD in world axes; camera trails travel
    case "vehicle": return moveTank3(e, input, dt, { speed });  // W/S drive along facing, A/D turn
    case "fp":      mouseLook(e, input); return moveFP(e, input, dt, speed);  // mouse aims, WASD relative
    case "orbital":                                              // WASD relative to the orbited camera
    default:        return moveRelative(e, input, dt, speed);
  }
}

// The camera half of a scheme (run3d calls this in render when config.controls is set and the game
// declares no camera() hook). Same `scheme` value as driveScheme, so the pair is coherent by name.
export function schemeCamera(scheme, cam, player) {
  if (!player) return;
  if (scheme === "fp") return fpCam(cam, player);
  if (scheme === "follow" || scheme === "vehicle") return chaseCam(cam, player, { faceYaw: true });
  return chaseCam(cam, player);   // orbital + default
}

// ── tilemap (rows of chars; solid set decides collision) ─────────────────────
export function makeTilemap(rows, tile = 32, solid = "#") {
  const solids = new Set([...solid]);
  return {
    rows, tile,
    w: rows[0]?.length || 0, h: rows.length,
    at: (cx, cy) => rows[cy]?.[cx] ?? " ",
    solidAt: (cx, cy) => solids.has(rows[cy]?.[cx]),
    // world-space AABB vs the grid; returns array of solid tile rects hit
    solidsNear(e) {
      const out = [];
      const x0 = Math.floor(e.x / tile), x1 = Math.floor((e.x + e.w) / tile);
      const y0 = Math.floor(e.y / tile), y1 = Math.floor((e.y + e.h) / tile);
      for (let cy = y0; cy <= y1; cy++)
        for (let cx = x0; cx <= x1; cx++)
          if (solids.has(rows[cy]?.[cx]))
            out.push({ x: cx * tile, y: cy * tile, w: tile, h: tile });
      return out;
    },
  };
}

// ── input abstraction (browser real / headless scripted) ─────────────────────
// down(key): held now. pressed(key): edge this frame. pointer: {x,y,down}.
export function makeInput() {
  const held = new Set(), edge = new Set();
  const pointer = { x: 0, y: 0, down: false };
  return {
    _held: held, _edge: edge, pointer,
    lookDX: 0, lookDY: 0,   // mouse-look delta this frame (first-person; filled by run3d under pointer lock)
    camYaw: 0,   // ground heading of the 3D camera this frame (filled by run3d); feed to kit.moveRelative
    down: (k) => held.has(k),
    pressed: (k) => edge.has(k),
    _set(k, v) { if (v) { if (!held.has(k)) edge.add(k); held.add(k); } else held.delete(k); },
    _endFrame() { edge.clear(); this.lookDX = 0; this.lookDY = 0; },
  };
}

// The selection half of a {kind:"menu"} HUD item: returns the 0-based index of the number key
// (1..9) pressed THIS frame, or -1. Pair with a menu whose options you drew in order:
//   const pick = kit.menuPick(input); if (pick === 0) buySpeed(); else if (pick === 1) ...
// So a dialogue/shop choice is two lines: render the menu in hud(), act on kit.menuPick in update().
export function menuPick(input) {
  for (let i = 1; i <= 9; i++) if (input.pressed(String(i))) return i - 1;
  return -1;
}

// ── dialogue / shop (the WHOLE talk loop as one primitive — open, advance, choose, close) ──────
// State lives in `state.talk` (plain data, sim-pure). The speaker is any object with
// { name, lines: string[] } and optionally { options: string[] } (the choices offered after the
// last line). Wire it with THREE calls and nothing else:
//   update():  const pick = talkStep(state, input);            // advance/choose/close — every frame
//              if (pick) { /* act on pick.pick (0-based option index) for pick.npc */ }
//              if (state.talk) return;                          // movement paused while talking
//              if (nearNpc && input.pressed("e")) talkOpen(state, nearNpc);
//   hud():     items.push(...talkHud(state));
// A SHOP is the same loop with priced options: talkOpen(state, vendor,
//   ["Health potion (10g)", "Sharper sword (25g)", "Leave"]) and branch on pick.pick.
export function talkOpen(state, npc, options) {
  state.talk = { npc, line: 0, options: options ?? npc.options ?? null };
}
export function talkStep(state, input, advanceKey = "e") {
  const t = state.talk;
  if (!t) return null;
  if (input.pressed("Escape")) { state.talk = null; return null; }
  const lines = t.npc.lines || [""];
  if (t.line < lines.length - 1) {                 // mid-dialogue: advance
    if (input.pressed(advanceKey)) t.line++;
    return null;
  }
  const opts = t.options;                          // last line: choose (or close)
  if (opts && opts.length) {
    const pick = menuPick(input);
    if (pick >= 0 && pick < opts.length) { state.talk = null; return { npc: t.npc, pick }; }
    return null;
  }
  if (input.pressed(advanceKey)) state.talk = null;
  return null;
}
export function talkHud(state) {
  const t = state.talk;
  if (!t) return [];
  const lines = t.npc.lines || [""], last = t.line >= lines.length - 1;
  const items = [{ kind: "panel", title: t.npc.name || "…", at: "bottom",
                   text: String(lines[Math.min(t.line, lines.length - 1)]) }];
  if (last && t.options && t.options.length)
    items.push({ kind: "menu", options: t.options, at: "center" });
  return items;
}

// ── quests (milestone progression WITHOUT ending the game — the Skyrim shape, not the soccer one) ──
// Quests are plain data in `state.quests`; completing one notifies and keeps playing. Reserve
// kit.win/kit.lose for the spec's DEFINITE ending (they stop the game); everything else that feels
// like an accomplishment is quest.complete / notify.
function makeQuestApi(notify) {
  const list = (state) => state.quests || (state.quests = []);
  return {
    // add(state, {id, title, reward?}) — idempotent by id; announces "New quest".
    add(state, q) {
      const L = list(state);
      if (L.some((x) => x.id === q.id)) return null;
      const nq = { done: false, reward: 0, ...q };
      L.push(nq);
      notify(`New quest: ${nq.title}`);
      return nq;
    },
    // complete(state, id) — marks done ONCE and announces; returns the quest (apply its .reward
    // yourself: `const q = kit.quest.complete(state,"beast"); if (q) state.gold += q.reward;`).
    complete(state, id) {
      const q = list(state).find((x) => x.id === id && !x.done);
      if (!q) return null;
      q.done = true;
      notify(`Quest complete: ${q.title}` + (q.reward ? ` (+${q.reward})` : ""));
      return q;
    },
    active: (state) => list(state).filter((q) => !q.done),
    isDone: (state, id) => list(state).some((q) => q.id === id && q.done),
    // log(state) → HUD items for the quest list; spread into hud(): `...kit.quest.log(this.state)`.
    log(state, at = "top-right") {
      const L = list(state);
      return L.length ? [{ kind: "panel", title: "Quests", at,
                           text: L.map((q) => `${q.done ? "✓" : "•"} ${q.title}`).join("\n") }] : [];
    },
  };
}

// ── draw api (canvas2d) — the render surface (2D games: the screen; 3D games: the HUD overlay) ──
export function makeDraw(ctx) {
  return {
    ctx,
    clear: (color = "#000") => { ctx.fillStyle = color; ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height); },
    rect: (x, y, w, h, color) => { ctx.fillStyle = color; ctx.fillRect(x, y, w, h); },
    circle: (x, y, r, color) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); },
    line: (x1, y1, x2, y2, color, width = 1) => {
      ctx.strokeStyle = color; ctx.lineWidth = width;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    },
    text: (str, x, y, color = "#fff", size = 16, align = "left") => {
      ctx.fillStyle = color; ctx.font = `${size}px monospace`; ctx.textAlign = align;
      ctx.fillText(str, x, y);
    },
    sprite: (img, x, y, w, h) => { if (img) ctx.drawImage(img, x, y, w, h); },
    // camera offset: push(cam) before drawing WORLD-space things, pop() before HUD/screen-space.
    push: (cam) => { ctx.save(); ctx.translate(-(cam?.x || 0), -(cam?.y || 0)); },
    pop: () => ctx.restore(),
  };
}

// ── HUD: a DATA-defined screen-space overlay, identical in 2D and 3D ──────────
// The game's hud(kit) RETURNS items; the engine draws them. The game never touches the canvas for
// the HUD, so it cannot clear/occlude the scene or fumble pixel layout. Anchors place items in the
// nine screen regions; same-anchor items stack. See validateHud for the accepted shape.
const HUD_ANCHORS = new Set([
  "top-left", "top", "top-right", "left", "center", "right",
  "bottom-left", "bottom", "bottom-right"]);
const _HUD_PAD = 16, _HUD_LINE = 24, _BAR_W = 160, _BAR_H = 12;

export function validateHud(items) {
  if (items == null) return null;
  if (!Array.isArray(items)) return "hud() must return an array of HUD items";
  for (const it of items) {
    if (!it || typeof it !== "object") return "each HUD item must be an object";
    if (it.kind === "text" || it.kind === "banner" || it.kind === "panel") {
      if (it.text == null) return `hud ${it.kind} item needs a 'text'`;
    } else if (it.kind === "bar") {
      if (typeof it.value !== "number" || typeof it.max !== "number")
        return "hud bar item needs numeric 'value' and 'max'";
    } else if (it.kind === "menu") {
      if (!Array.isArray(it.options) || it.options.length === 0)
        return "hud menu item needs a non-empty 'options' string array";
    } else if (it.kind === "marker") {
      if (typeof it.x !== "number" || typeof it.z !== "number")
        return "hud marker item needs numeric world 'x' and 'z'";
    } else {
      return `unknown hud item kind ${JSON.stringify(it.kind)} — use text | bar | banner | panel | menu | marker`;
    }
    if (it.at != null && !HUD_ANCHORS.has(it.at))
      return `unknown hud anchor ${JSON.stringify(it.at)} — use e.g. "top-left", "top", "bottom-right"`;
  }
  return null;
}

// A boxed HUD overlay (dialogue panel / choice menu) — a self-contained card placed by anchor, drawn
// with plain rects (works over both the 2D canvas and the 3D overlay). The SIM owns all state and
// selection; this only draws what the game returns from hud(). Border via thin rects (no line dep).
function _hudBox(draw, x, y, w, h) {
  draw.rect(x, y, w, h, "rgba(12,14,20,0.85)");
  const b = "#e8dcc0", t = 2;
  draw.rect(x, y, w, t, b); draw.rect(x, y + h - t, w, t, b);
  draw.rect(x, y, t, h, b); draw.rect(x + w - t, y, t, h, b);
}
function _hudBoxPos(anchor, W, H, bw, bh) {
  const pad = 24;
  const x = anchor.endsWith("left") ? pad : anchor.endsWith("right") ? W - pad - bw : (W - bw) / 2;
  const y = anchor.startsWith("top") ? pad : anchor.startsWith("bottom") ? H - pad - bh : (H - bh) / 2;
  return { x, y };
}
function _drawPanel(draw, it, W, H) {
  const lines = String(it.text).split("\n");
  const bw = Math.min(W - 48, 680), titleH = it.title ? 30 : 0;
  const bh = 24 + titleH + lines.length * 24;
  const { x, y } = _hudBoxPos(it.at || "bottom", W, H, bw, bh);
  _hudBox(draw, x, y, bw, bh);
  let ty = y + 22;
  if (it.title) { draw.text(String(it.title), x + 18, ty, it.color || "#ffd27a", 20, "left"); ty += 30; }
  for (const ln of lines) { draw.text(ln, x + 18, ty, "#f2ead8", 16, "left"); ty += 24; }
}
function _drawMenu(draw, it, W, H) {
  const opts = it.options.map(String);
  const bw = Math.min(W - 48, 560), titleH = it.title ? 32 : 0;
  const bh = 20 + titleH + opts.length * 30;
  const { x, y } = _hudBoxPos(it.at || "center", W, H, bw, bh);
  _hudBox(draw, x, y, bw, bh);
  let ty = y + 22;
  if (it.title) { draw.text(String(it.title), x + 18, ty, it.color || "#ffd27a", 20, "left"); ty += 32; }
  opts.forEach((opt, i) => {
    const sel = it.selected === i;
    if (sel) draw.rect(x + 8, ty - 16, bw - 16, 26, "rgba(255,210,120,0.22)");
    draw.text(`${sel ? "▶ " : ""}${i + 1}) ${opt}`, x + 18, ty, sel ? "#ffe9b0" : "#e2dccb", 16, "left");
    ty += 30;
  });
}

export function renderHud(draw, items, W, H) {
  if (!Array.isArray(items)) return;
  const cursor = {};   // anchor -> next y (top anchors grow down, bottom anchors grow up)
  const xOf = (anchor) => anchor.endsWith("left") ? { x: _HUD_PAD, align: "left" }
    : anchor.endsWith("right") ? { x: W - _HUD_PAD, align: "right" }
      : { x: W / 2, align: "center" };
  for (const it of items) {
    if (!it || typeof it !== "object") continue;
    if (it.kind === "marker") continue;   // world-anchored; run3d projects it to a screen label first
    if (it.kind === "banner") {
      draw.text(String(it.text), W / 2, H / 2, it.color || "#fff", 32, "center");
      continue;
    }
    if (it.kind === "panel") { _drawPanel(draw, it, W, H); continue; }
    if (it.kind === "menu") { _drawMenu(draw, it, W, H); continue; }
    const anchor = it.at || "top-left";
    const down = !anchor.startsWith("bottom");
    if (cursor[anchor] == null) cursor[anchor] = down ? _HUD_PAD + 16 : H - _HUD_PAD;
    const y = cursor[anchor];
    const { x, align } = xOf(anchor);
    if (it.kind === "text") {
      draw.text(String(it.text), x, y, it.color || "#fff", it.size || 16, align);
    } else if (it.kind === "bar") {
      const bx = align === "right" ? x - _BAR_W : align === "center" ? x - _BAR_W / 2 : x;
      const frac = Math.max(0, Math.min(1, (it.value || 0) / (it.max || 1)));
      if (it.label) draw.text(String(it.label), bx, y - 14, "#fff", 12, "left");
      draw.rect(bx, y - 10, _BAR_W, _BAR_H, "#2a2a33");
      draw.rect(bx, y - 10, _BAR_W * frac, _BAR_H, it.color || "#39c07a");
    }
    cursor[anchor] = down ? y + _HUD_LINE : y - _HUD_LINE;
  }
}

// ── the runner: shared control surface the kit hands the game ─────────────────
export function makeKit(config, rng) {
  let over = null; // null | {won:bool, msg}
  let sprites = {}; // id -> loaded Image; empty headless (kit.sprite always null -> game falls to shapes)
  let toasts = []; // transient notify() messages; stepped+drawn by the frame loop, inert headless
  const notify = (msg, secs = 3) => {
    toasts.push({ msg: String(msg), ttl: secs });
    if (toasts.length > 4) toasts.shift();
  };
  return {
    config,
    rng,
    V,
    spawn, cull, integrate, integrate3, physics3, heading3, flyer, aabb, resolveAabb, makeTilemap,
    physics, walk, jump, seek, flee, arrive, pursue, wander, astar, cellCenter,
    seek3, flee3, wander3, patrol3, avoidRects,
    gridMove, burst, stepParticles, makeCamera: () => makeCamera(config),
    chaseCam, moveTopDown, moveTopDown3, moveTank3, moveRelative, mouseLook, fpCam, moveFP,
    drive: (e, input, dt, speed) => driveScheme(config.controls, e, input, dt, speed),
    menuPick, talkOpen, talkStep, talkHud,
    quest: makeQuestApi(notify),
    notify,
    audio: { play: () => {} }, // stub; real backend wired later
    sprite: (id) => sprites[id] || null,
    _setSprites(map) { sprites = map || {}; },
    _stepToasts(dt) { for (const t of toasts) t.ttl -= dt; toasts = toasts.filter((t) => t.ttl > 0); },
    _toastItems: () => toasts.map((t) => ({ kind: "text", text: t.msg, at: "top", color: "#ffe9b0", size: 18 })),
    win: (msg = "You win") => { if (!over) over = { won: true, msg }; },
    lose: (msg = "Game over") => { if (!over) over = { won: false, msg }; },
    get over() { return over; },
    _reset() { over = null; },
  };
}

// Preload the game's sprite assets (assets.json in the game folder) into a {id: Image} map.
// Browser-only; a missing/empty manifest yields {} so every kit.sprite(id) returns null and the
// game renders its placeholder shapes — assets are a pure skin over a game that already runs.
async function loadSprites(assetBase) {
  if (!assetBase) return {};
  let manifest;
  try {
    const res = await fetch(`${assetBase}/assets.json`);
    if (!res.ok) return {};
    manifest = await res.json();
  } catch { return {}; }
  const sprites = manifest && Array.isArray(manifest.sprites) ? manifest.sprites : [];
  const entries = await Promise.all(sprites.map((s) => new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve([s.id, img]);
    img.onerror = () => resolve(null);
    img.src = `${assetBase}/${s.file}`;
  })));
  return Object.fromEntries(entries.filter(Boolean));
}

// Scale the canvas to fill the browser window, preserving its aspect (letterboxed). The backing
// store (canvas.width/height) is untouched — the game keeps drawing in its own coordinate space and
// the browser upscales — so this is a pure display change, no gameplay impact.
export function fitToWindow(canvas, aspect) {
  const fit = () => {
    let w = innerWidth, h = w / aspect;
    if (h > innerHeight) { h = innerHeight; w = h * aspect; }
    canvas.style.width = `${Math.round(w)}px`;
    canvas.style.height = `${Math.round(h)}px`;
  };
  addEventListener("resize", fit);
  fit();
}

// Build a game instance holding a REAL kit. The kit needs the game's config (seed, size), but config
// lives inside createGame — so peek it with a throwaway null-kit instance, build the kit from it, then
// re-instantiate WITH the kit. This is why createGame(kit) receives a live kit: a helper that closes
// over the `kit` parameter works, not just the kit passed into init/update. (Don't touch kit at the
// createGame top level — it runs during the config peek, before the kit exists; use it in init/update
// and in helpers those call.)
export function realize(factory, defaults = {}, seed = 1) {
  const peek = typeof factory === "function" ? factory(null) : factory;
  const config = { ...defaults, ...(peek.config || {}) };
  const kit = makeKit(config, makeRng(config.seed || seed));
  const g = typeof factory === "function" ? factory(kit) : peek;
  return { g, config, kit };
}

// ── browser entry: run a real animation loop with real input + canvas ────────
export async function run(game, canvas, assetBase) {
  const { g, config, kit } = realize(game, { width: 960, height: 540, background: "#111", gravity: 0 });
  canvas.width = config.width; canvas.height = config.height;
  fitToWindow(canvas, config.width / config.height);
  const ctx = canvas.getContext("2d");
  const draw = makeDraw(ctx);
  const input = makeInput();
  kit._setSprites(await loadSprites(assetBase));

  const keymap = (e) => e.key.length === 1 ? e.key.toLowerCase() : e.key;
  addEventListener("keydown", (e) => input._set(keymap(e), true));
  addEventListener("keyup", (e) => input._set(keymap(e), false));
  canvas.addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    // rect is the scaled display box; map the pointer back into backing (game) coordinates.
    input.pointer.x = (e.clientX - r.left) * (canvas.width / r.width);
    input.pointer.y = (e.clientY - r.top) * (canvas.height / r.height);
  });
  canvas.addEventListener("mousedown", () => { input.pointer.down = true; });
  addEventListener("mouseup", () => { input.pointer.down = false; });

  if (g.init) g.init(kit);
  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); // clamp dt so a tab-switch can't explode the sim
    last = now;
    if (!kit.over) g.update(dt, input, kit);
    input._endFrame();
    kit._stepToasts(dt);
    draw.clear(config.background);
    if (g.draw) g.draw(draw, kit);
    const hudItems = [...(g.hud ? g.hud(kit) || [] : []), ...kit._toastItems()];
    if (hudItems.length) renderHud(draw, hudItems, config.width, config.height);
    if (kit.over) draw.text(kit.over.msg, config.width / 2, config.height / 2, "#fff", 32, "center");
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return kit;
}

// ── headless entry: step the sim only, no draw. THE LOCAL GRADIENT ───────────
// Runs `frames` updates at a fixed dt with scripted (or empty) input, watchdogs
// every entity/state number for NaN/Infinity, and reports the first crash.
// Zero deps — pure Node. An outer process timeout catches infinite loops.
export function simulate(game, { frames = 600, dt = 1 / 60, seed = 1, script = [] } = {}) {
  const { g, config, kit } = realize(game, { width: 640, height: 480, gravity: 0 }, seed);
  const input = makeInput();
  try {
    if (g.init) g.init(kit);
  } catch (e) { return { ok: false, frame: 0, phase: "init", error: String(e && e.stack || e) }; }

  const badNum = (v) => typeof v === "number" && !Number.isFinite(v);
  for (let f = 0; f < frames; f++) {
    // apply scripted input for this frame: [{frame, key, down}] or {down:[keys]}
    for (const cmd of script) if (cmd.frame === f) input._set(cmd.key, cmd.down !== false);
    try {
      if (!kit.over) g.update(dt, input, kit);
    } catch (e) {
      return { ok: false, frame: f, phase: "update", error: String(e && e.stack || e) };
    }
    input._endFrame();
    // watchdog: scan common numeric fields on the world for divergence
    const world = g.state && (Array.isArray(g.state.world) ? g.state.world
      : Array.isArray(g.state.entities) ? g.state.entities : null);
    if (world) {
      for (let i = 0; i < world.length; i++) {
        const e = world[i];
        for (const k of ["x", "y", "z", "vx", "vy", "vz"]) {
          if (e[k] !== undefined && badNum(e[k])) {
            const who = ["type", "shape", "id", "label"].map(p => e[p] !== undefined && `${p}=${e[p]}`).filter(Boolean).join(" ");
            const when = f === 0 ? "on the first update()" : `at frame ${f}`;
            return { ok: false, frame: f, phase: "diverged",
              error: `entity field ${k}=${e[k]} is not finite ${when} — the offending entity is `
                   + `[${who || `world[${i}]`}]. Find every write to .${k} on THAT entity (its spawn `
                   + `and any update() line that reassigns .${k}); a value flowing in is undefined or `
                   + `NaN (an uninitialized state field, a missing WORLD key, or 0/0). Fix that one write.` };
          }
        }
      }
    }
    if (kit.over) return { ok: true, frame: f, resolved: kit.over.won ? "win" : "lose", msg: kit.over.msg };
  }
  return { ok: true, frame: frames, resolved: "ran" };
}

// ── probe: correctness gate beyond "didn't crash" ────────────────────────────
// Two GENERIC invariants no genre-specific knowledge is needed for:
//   1. CONTROLS LIVE — run the sim with no input and with every direction key
//      mashed; if the two final worlds are identical, input does nothing.
//   2. NO WALL-CLIP — if the game keeps a tilemap in state (anything with
//      solidAt+tile), no colliding entity's center may rest in a solid cell.
// Returns { ok, violations:[{kind, detail}] } — each violation is a fix prompt.
function worldOf(g) {
  return g.state && (Array.isArray(g.state.world) ? g.state.world
    : Array.isArray(g.state.entities) ? g.state.entities : []) || [];
}
function runSnapshot(gameFactory, { frames, dt, seed, script }) {
  const { g, kit } = realize(gameFactory, { gravity: 0 }, seed);
  const input = makeInput();
  if (g.init) g.init(kit);
  for (let f = 0; f < frames && !kit.over; f++) {
    for (const c of script) if (c.frame === f) input._set(c.key, c.down !== false);
    g.update(dt, input, kit);
    input._endFrame();
  }
  return g;
}
export function probe(gameFactory, { frames = 240, dt = 1 / 60, seed = 1, src = "" } = {}) {
  const violations = [];

  // CODE that reads the mouse outside the "fp" scheme: in a 3D orbital/follow/vehicle game the mouse
  // is the camera (input.pointer is never fed; there is no "mouse0" key), so a mouse-gated action can
  // never fire — the game passes every gate yet is unwinnable. Deterministic, so check it first.
  if (src) {
    // Nondeterminism breaks every gate's baseline-vs-driven comparison (and replay): the whole
    // reason kit.rng exists. Deterministic to detect, so check it first.
    const nondet = src.match(/Math\s*\.\s*random|Date\s*\.\s*now|new\s+Date\s*\(/);
    if (nondet) {
      violations.push({ kind: "nondeterminism",
        detail: `the code calls ${JSON.stringify(nondet[0])} — a game must be DETERMINISTIC (the `
          + `headless/probe gates diff a no-input run against driven runs; random state makes that `
          + `diff meaningless). Replace EVERY Math.random/Date.now with kit.rng: .next() 0..1, `
          + `.range(lo,hi), .int(lo,hi) inclusive, .pick(arr), .chance(p). A helper that needs `
          + `randomness must take the rng as a parameter (thread kit.rng from init/update).` });
    }
    const mouseRead = src.match(/input\s*\.\s*pointer|["'`]mouse\d?["'`]/);
    if (mouseRead) {
      const cfg = realize(gameFactory, { gravity: 0 }, seed).config;
      if (cfg.mode === "3d" && cfg.controls !== "fp") {
        violations.push({ kind: "dead_mouse_control",
          detail: `the code gates an action on the mouse (${JSON.stringify(mouseRead[0])}), but the `
            + `"${cfg.controls || "default"}" control scheme has no mouse input (the mouse orbits the `
            + `camera; input.pointer is never set outside "fp", and "mouse0" is not a key). That `
            + `branch can NEVER run. Bind the action to a KEYBOARD key instead — e.g. attack on `
            + `" " (space) or "f", checked with input.pressed(key) — and show the key in the HUD.` });
      }
    }
  }
  // Base movement keys, plus every key literal the game ITSELF reads (a turn game's controls may be
  // Enter/e/r/1-9 — keys the base mash never touches, so a fully-working game read as dead). Capped:
  // each key costs a full sim run.
  const declared = src
    ? [...src.matchAll(/input\s*\.\s*(?:pressed|down)\(\s*["'`]([^"'`]{1,12})["'`]/g)].map((m) => m[1])
    : [];
  const DIRS = [...new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "a", "d", "w", "s", " ",
                            ...declared])].slice(0, 20);
  const MIN_MOVE = 4; // px; below this over the whole window a "control" is effectively dead
  const snap = (script) => runSnapshot(gameFactory, { frames, dt, seed, script });

  let baseline;
  try { baseline = snap([]); }
  catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
  const baseW = worldOf(baseline);

  // Per-key SUSTAINED hold (one key at a time, so left/right can't cancel). Measure the biggest
  // input-caused displacement of any entity vs the no-input baseline (deterministic → the diff
  // isolates the input's effect). A spawn (bullet) => controls clearly live.
  // Position-free state digest: a turn/card/menu game's controls mutate STATE (hand, health, turn)
  // without moving any entity. Positions/velocities are EXCLUDED so a sub-MIN_MOVE drift can't
  // launder a too-slow mover into "live" — spatial liveness stays owned by the displacement check.
  const POS = new Set(["x", "y", "z", "vx", "vy", "vz"]);
  const digest = (v, seen = new WeakSet()) => {
    if (v === null || typeof v === "number" || typeof v === "boolean" || typeof v === "string") return JSON.stringify(v);
    if (typeof v !== "object") return "";                    // functions/undefined
    if (seen.has(v)) return "~";
    seen.add(v);
    if (Array.isArray(v)) return "[" + v.map((e) => digest(e, seen)).join(",") + "]";
    return "{" + Object.keys(v).sort().filter((k) => !POS.has(k))
      .map((k) => k + ":" + digest(v[k], seen)).join(",") + "}";
  };
  const baseDigest = digest(baseline.state);
  // The digest signal is only meaningful if a no-input run reproduces itself: a nondeterministic
  // init (random deck, Date-seeded anything) makes every digest differ, input or not.
  let digestReliable;
  try { digestReliable = digest(snap([]).state) === baseDigest; }
  catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
  let best = { key: null, disp: 0, spawned: false }, drivenForClip = baseline, stateChanged = false;
  for (const key of DIRS) {
    let g;
    try { g = snap([{ frame: 0, key, down: true }]); }
    catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
    const w = worldOf(g);
    // Only a NET SPAWN counts as "input did something" (a bullet/particle appears). A DECREASE
    // (a brick smashed, a pellet eaten) happens under gameplay regardless of input, so it must NOT
    // read as a live control — that false-green let a breakout with a dead paddle pass.
    if (w.length > baseW.length) { best = { key, disp: Infinity, spawned: true }; drivenForClip = g; break; }
    if (digestReliable && !stateChanged && digest(g.state) !== baseDigest) stateChanged = true;
    let m = 0;
    for (let i = 0; i < w.length; i++)
      m = Math.max(m, Math.hypot((w[i].x || 0) - (baseW[i].x || 0), (w[i].y || 0) - (baseW[i].y || 0),
                                 (w[i].z || 0) - (baseW[i].z || 0)));
    if (m > best.disp) { best = { key, disp: m, spawned: false }; drivenForClip = g; }
  }
  if (!best.spawned && !stateChanged && best.disp < MIN_MOVE) {
    const is3d = baseW.some((e) => e && (e.z !== undefined
      || e.shape === "box" || e.shape === "sphere" || e.shape === "ground"));
    const head = `no key does anything: the strongest input (${best.key}) shifted every entity by `
      + `at most ${best.disp.toFixed(2)} over ${frames} frames, and no key changed game state at all. `
      + `If the game is turn/menu-driven (input mutates state, not positions), check the KEY LITERALS: `
      + `keys are KeyboardEvent.key values — space is " " (a single space), NOT "Space"; arrows are `
      + `"ArrowLeft" etc. `;
    const detail = is3d
      // 3D: the #1 cause is a player that isn't the entity the probe (and renderer) sees.
      ? head + `In a 3D game the usual cause is that the object you move is NOT a member of `
        + `state.world — the probe (and the renderer) only see entities IN state.world. The player MUST `
        + `be a SHAPE-TAGGED entity pushed into state.world, and state.player must reference that SAME `
        + `object: \`state.player = kit.spawn(world, { shape:"box", x,y,z, w,h,d, color })\` (never a bare `
        + `state object kept outside the world). Move it with kit.drive(player,input,dt,speed) or `
        + `kit.moveTopDown3/moveRelative; speeds are units/SECOND. Do NOT use kit.walk/jump/physics — those `
        + `are the 2D kit. Also make sure movement isn't gated off every frame (e.g. a dialogue/pause flag `
        + `stuck true).`
      // 2D: the classic too-slow / per-frame-magnitude mistake.
      : head + `It likely responds but FAR too slowly — kit velocities are px/SECOND and integrate/physics `
        + `apply dt for you; NEVER use per-frame magnitudes. Typical: walker ~150 px/s, jump ~600 px/s, `
        + `gravity ~2000 px/s². Prefer kit.walk(e,dir,speed) / kit.jump(e,speed) / kit.physics(e,dt,solids,`
        + `gravity) so dt is handled.`;
    violations.push({ kind: "dead_controls", detail });
  }
  // wall-clip on the most-moving run (only meaningful when the game keeps a tilemap)
  const tm = drivenForClip.state && drivenForClip.state.tilemap;
  if (tm && typeof tm.solidAt === "function" && tm.tile) {
    const stuck = worldOf(drivenForClip).filter((e) => e.w > 0 && e.h > 0
      && tm.solidAt(Math.floor(e.x / tm.tile), Math.floor(e.y / tm.tile)));
    if (stuck.length) {
      violations.push({ kind: "wall_clip",
        detail: `${stuck.length} moving entit${stuck.length > 1 ? "ies rest" : "y rests"} INSIDE a `
          + `solid wall tile (e.g. ${stuck.slice(0, 3).map((e) => (e.type || e.tag || "entity")
          + `@${e.x?.toFixed(0)},${e.y?.toFixed(0)}`).join("; ")}). Block movement BEFORE an entity `
          + `enters a wall; never snap it to the wall tile's own center.` });
    }
  }
  return { ok: violations.length === 0, violations };
}

// ── render smoke: exercise the draw() path headless can't otherwise see ───────
// simulate() steps update() ONLY; a game can pass it yet crash or paint nothing — a blank/broken
// browser screen. Exercise the two screen-space paths each frame against recording mocks:
//   - 2D SCENE: draw(g) must not throw (draw_crash) and must paint ≥1 primitive (draw_blank).
//   - HUD (both modes): hud(kit) must return a valid item array (hud_bad) and not throw (hud_crash).
// A 3D game has no draw() — its scene renders from world entities — so only its hud() is checked. A
// 3D game with neither draw nor hud passes through (scene-only).
export function renderSmoke(gameFactory, { frames = 120, dt = 1 / 60, seed = 1 } = {}) {
  const { g, config, kit } = realize(gameFactory, { width: 640, height: 480, gravity: 0 }, seed);
  const has2dScene = typeof g.draw === "function";
  const hasHud = typeof g.hud === "function";
  if (config.mode === "3d" && has2dScene) {
    return { ok: false, violations: [{ kind: "draw_in_3d",
      detail: `a 3D game must NOT define draw() — the scene renders from world entity shape tags, and `
        + `the HUD is DATA returned from hud(kit). A draw() here is dead code (the 3D renderer ignores `
        + `it) and any g.clear in it would blank the scene. Delete draw(); move HUD into hud().` }] };
  }
  if (config.mode !== "3d" && !has2dScene) {
    return { ok: false, violations: [{ kind: "missing_draw",
      detail: `a 2D game MUST define draw(g, kit) — the scene renders ONLY from it (hud() is a thin `
        + `overlay; without draw the screen is the background color plus floating HUD text). Write `
        + `draw: paint the tilemap/ground, then every entity in state (crops, NPCs, the player) as `
        + `rects/circles/sprites at their positions, world-space under g.push(cam)/g.pop().` }] };
  }
  if (!has2dScene && !hasHud) return { ok: true, skipped: true };
  const input = makeInput();
  let content = 0;
  const bump = () => { content++; };
  const rec = { ctx: {}, clear: () => {}, push: () => {}, pop: () => {},
                rect: bump, circle: bump, line: bump, text: bump, sprite: bump };
  // HUD items render against a NON-counting mock: HUD text must not mask a blank SCENE (a game with
  // no world drawing but a chatty hud() read as "painted something").
  const noop = () => {};
  const hudRec = { ctx: {}, clear: noop, push: noop, pop: noop,
                   rect: noop, circle: noop, line: noop, text: noop, sprite: noop };
  try {
    if (g.init) g.init(kit);
    for (let f = 0; f < frames; f++) {
      if (!kit.over) g.update(dt, input, kit);
      input._endFrame();
      if (has2dScene) g.draw(rec, kit);
      if (hasHud) {
        const items = g.hud(kit);
        const bad = validateHud(items);
        if (bad) return { ok: false, violations: [{ kind: "hud_bad",
          detail: `hud(kit) returned an invalid overlay: ${bad}. hud() returns an array of `
            + `{kind:"text"|"bar"|"banner", ...} items — the engine draws them; never touch a canvas.` }] };
        renderHud(hudRec, items, config.width || 640, config.height || 480);
      }
    }
  } catch (e) {
    const which = has2dScene ? "draw()/hud()" : "hud()";
    return { ok: false, violations: [{ kind: "draw_crash",
      detail: `${which} threw during render: ${String(e && e.stack || e)}. These must only READ state; `
        + `never mutate state or read undefined fields.` }] };
  }
  if (has2dScene && content === 0) {
    return { ok: false, violations: [{ kind: "draw_blank",
      detail: `draw() ran ${frames} frames without drawing anything visible (no rect/circle/line/text/`
        + `sprite calls) — the screen would be blank. Render every entity in state.world from `
        + `draw(g,kit) at its position with a color.` }] };
  }
  return { ok: true, content };
}

// ── scroll smoke: a world bigger than the screen needs a camera that follows ──
// Drive the player across the level and watch draw's camera offset. If entities range well beyond
// one screen but the view never pans (no g.push(cam), or a static cam), most of the level is
// permanently off-screen — it passes headless/probe/render yet is unplayable. Tightly guarded so
// confined games (pong, wrap-around asteroids, one-screen mazes) never false-fire. 3D uses the
// camera() hook, checked separately.
export function scrollSmoke(gameFactory, { frames = 300, dt = 1 / 60, seed = 1 } = {}) {
  const defaults = { width: 640, height: 480, gravity: 0 };
  const config = realize(gameFactory, defaults, seed).config;
  if (config.mode === "3d") return { ok: true, skipped: true };
  const KEYS = ["d", "ArrowRight", "a", "ArrowLeft"];
  let best = { span: 0, camDx: 0, drew: false };
  for (const key of KEYS) {
    const { g, kit } = realize(gameFactory, defaults, seed);
    const input = makeInput();
    let minX = Infinity, maxX = -Infinity, camMin = Infinity, camMax = -Infinity, drew = false;
    const rec = { ctx: {}, clear: () => {}, pop: () => {}, rect: () => {}, circle: () => {},
      line: () => {}, text: () => {}, sprite: () => {},
      push: (cam) => { if (cam && typeof cam.x === "number") { drew = true; camMin = Math.min(camMin, cam.x); camMax = Math.max(camMax, cam.x); } } };
    try {
      if (g.init) g.init(kit);
      for (let f = 0; f < frames && !kit.over; f++) {
        input._set(key, true);
        g.update(dt, input, kit);
        input._endFrame();
        for (const e of worldOf(g)) if (typeof e.x === "number") { minX = Math.min(minX, e.x); maxX = Math.max(maxX, e.x); }
        if (typeof g.draw === "function") g.draw(rec, kit);
      }
    } catch { continue; } // a draw/update crash is the render/headless gate's job, not this one
    const span = maxX - minX;
    if (span > best.span) best = { span, camDx: camMax - camMin, drew };
  }
  if (best.span > config.width * 1.5 && (!best.drew || best.camDx < config.width * 0.5)) {
    return { ok: false, violations: [{ kind: "no_camera",
      detail: `the world spans ~${Math.round(best.span)}px but the screen is only ${config.width}px and the `
        + `camera ${best.drew ? `panned just ${Math.round(best.camDx)}px` : "was never used"} — most of the `
        + `level is off-screen. Make a camera (const cam = kit.makeCamera()), call cam.follow(player, worldW, `
        + `worldH) each update, and wrap WORLD-space drawing in g.push(cam) … g.pop() (draw HUD after pop).` }] };
  }
  return { ok: true, span: Math.round(best.span), camDx: Math.round(best.camDx) };
}
