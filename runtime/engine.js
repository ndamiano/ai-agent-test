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

// ── solid collision (top-down/2D): ONE pass resolves both response cases ─────
// Live-run evidence for why this is a kit primitive: a shipped game had a solidAt() lookup that was
// never applied to movement (the knight walked through walls), and another let enemies stack under
// the player (no pair separation). Both are the same missing pass. The scaffold calls this AFTER
// gameplay each frame; game code composes it for free by tagging entities `solid: true`.
// Participants: entities with `e.solid` truthy, not `e.dead`, with a real AABB (w,h > 0).
//   1. entity-vs-entity: each overlapping solid pair gets a symmetric half-and-half push apart on
//      the minimal axis (no masses). Broadphase = sort by x + sweep (stable, worlds are tens of
//      entities). Velocities untouched — steering re-sets them each frame anyway.
//   2. entity-vs-tile (when solidAt given): full pushout of the entity's AABB from solid cells via
//      resolveAabb (minimal axis, zeroes the blocked velocity component — same resolution as the
//      platformer physics). Tiles resolve LAST so walls win: a pair push can't leave anyone inside
//      a wall this frame. Deterministic: stable order, no randomness.
// Iterated to convergence: one pass fully separates a pair, but a chain push can re-overlap an
// already-processed neighbor, and steering re-compresses clusters every frame — a single pass
// leaves steady-state residuals above the probe's solid_overlap epsilon (a parked live build).
export function collideWorld(world, solidAt = null, cell = 32) {
  const solids = world.filter((e) => e.solid && !e.dead && e.w > 0 && e.h > 0);
  for (let pass = 0; pass < 8; pass++) {
    let moved = false;
    const order = [...solids].sort((a, b) => a.x - b.x);
    for (let i = 0; i < order.length; i++) {
      const a = order[i];
      for (let j = i + 1; j < order.length; j++) {
        const b = order[j];
        if (b.x >= a.x + a.w) break;               // sweep: no later entity can overlap a
        if (!aabb(a, b)) continue;
        const px = Math.min(a.x + a.w - b.x, b.x + b.w - a.x);
        const py = Math.min(a.y + a.h - b.y, b.y + b.h - a.y);
        if (px < py) {
          const s = a.x + a.w / 2 <= b.x + b.w / 2 ? 1 : -1;
          a.x -= s * px / 2; b.x += s * px / 2;
        } else {
          const s = a.y + a.h / 2 <= b.y + b.h / 2 ? 1 : -1;
          a.y -= s * py / 2; b.y += s * py / 2;
        }
        moved = true;
      }
    }
    if (solidAt) for (const e of solids) {
      const x0 = Math.floor(e.x / cell), x1 = Math.floor((e.x + e.w) / cell);
      const y0 = Math.floor(e.y / cell), y1 = Math.floor((e.y + e.h) / cell);
      for (let cy = y0; cy <= y1; cy++)
        for (let cx = x0; cx <= x1; cx++)
          if (solidAt(cx, cy)) {
            const t = { x: cx * cell, y: cy * cell, w: cell, h: cell };
            if (aabb(e, t)) { resolveAabb(e, t); moved = true; }
          }
    }
    if (!moved) break;
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

// Build a walled 3D level from rows of chars in ONE call: spawns the ground (sized to the map,
// centered on the origin) + one solid wall box per solid char, and returns { rects, at, w, h, tile }
// — rects feed kit.avoidRects (centered {x,z,w,d}), at(c, r) maps a tile to its world-space center
// (use it to place the player/enemies/pickups). This exists because a hand-assembled dungeon is the
// #1 half-built 3D scene: a shipped build computed the wall rects and never spawned a single box or
// the ground — the crypt rendered as a void.
export function wallsFromTilemap(target, rows, { tile = 4, height = 3, solid = "#", color = "#666",
                                                 ground = "#333" } = {}) {
  // Accepts STATE or a world array. Pass state and it also sets state.walls — three consecutive
  // model fixes called this correctly but discarded the return, leaving walls uncollidable; wiring
  // state.walls inside the primitive removes the step models reliably drop.
  const state = (target && !Array.isArray(target) && Array.isArray(target.world)) ? target : null;
  const world = state ? state.world : target;
  // CORNER-origin like cellCenter — tile (c, r) centers at ((c+.5)*tile, (r+.5)*tile). Re-centering
  // the map on the origin created a SECOND coordinate system and every hand-written placement
  // (c*TILE math the model naturally writes) landed off the level; the ground centers over the map.
  const H = rows.length, W = (rows[0] || "").length;
  const at = (c, r) => ({ x: (c + 0.5) * tile, z: (r + 0.5) * tile });
  spawn(world, { shape: "ground", size: Math.max(W, H) * tile + tile * 4, color: ground,
                 x: W * tile / 2, y: 0, z: H * tile / 2 });
  const rects = [];
  for (let r = 0; r < H; r++) for (let c = 0; c < W; c++) {
    if (!solid.includes(rows[r][c] || " ")) continue;
    const { x, z } = at(c, r);
    spawn(world, { shape: "box", x, y: height / 2, z, w: tile, h: height, d: tile, color });
    rects.push({ x, z, w: tile, d: tile });
  }
  if (state) state.walls = rects;
  return { rects, at, w: W, h: H, tile };
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
  // Behind the entity = MINUS its forward (-sin ry, -cos ry), i.e. (+sin, +cos) — a -sin x here
  // mirrors the camera's swing against the turn direction (correct at yaw 0, backwards everywhere else).
  const px = tx + Math.sin(yaw) * back, py = ty + up, pz = tz + Math.cos(yaw) * back;
  // Ease toward the desired position instead of snapping: a heading change would otherwise teleport
  // the camera 90°+ in one frame. Look-at stays exact so the player never leaves center.
  const k = opts.lerp ?? 0.12;
  const seeded = cam.x || cam.y || cam.z;
  cam.x = seeded ? cam.x + (px - cam.x) * k : px;
  cam.y = seeded ? cam.y + (py - cam.y) * k : py;
  cam.z = seeded ? cam.z + (pz - cam.z) * k : pz;
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
    // Heading-relative, NOT world axes: under a behind-the-player camera, W must always mean "away
    // from the camera" and A/D must turn — world-axis WASD walks the player at the camera the moment
    // the chase cam swings to a new heading (shipped failure).
    case "follow":  return moveTank3(e, input, dt, { speed, back: 1, turn: 3 });
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
  // Registered ACTIONS (name -> {keys, fn}). Registration makes the spec's key bindings
  // machine-readable: the probe can press every registered action's keys and require an effect
  // (a shipped game had a full melee implementation behind a key read that never fired), and a
  // future frontend remap becomes a key→action indirection with no game-code change. Handlers
  // mutate state (sim/render law), so every runner fires them update-side — on the PRESSED edge
  // of any bound key, once per action per frame, AFTER the game's update for the frame. A Map
  // keyed by name means a re-register REPLACES (a re-init must not double-fire); keys normalize
  // exactly like the browser key listener (single chars lowercased, else event.key verbatim).
  const actions = new Map();
  const notify = (msg, secs = 3) => {
    toasts.push({ msg: String(msg), ttl: secs });
    if (toasts.length > 4) toasts.shift();
  };
  return {
    config,
    rng,
    V,
    spawn, cull, integrate, integrate3, physics3, heading3, flyer, aabb, resolveAabb, makeTilemap,
    physics, walk, jump, collideWorld, seek, flee, arrive, pursue, wander, astar, cellCenter,
    seek3, flee3, wander3, patrol3, avoidRects, wallsFromTilemap,
    gridMove, burst, stepParticles, makeCamera: () => makeCamera(config),
    chaseCam, moveTopDown, moveTopDown3, moveTank3, moveRelative, mouseLook, fpCam, moveFP,
    drive: (e, input, dt, speed) => driveScheme(config.controls, e, input, dt, speed),
    menuPick, talkOpen, talkStep, talkHud,
    quest: makeQuestApi(notify),
    notify,
    register(name, keys, fn) {
      actions.set(String(name), {
        keys: (keys || []).map((k) => (k.length === 1 ? k.toLowerCase() : k)), fn });
    },
    bindings: () => [...actions].map(([name, a]) => ({ name, keys: [...a.keys] })),
    _fireActions(input) {
      for (const [, a] of actions) if (a.keys.some((k) => input.pressed(k))) a.fn();
    },
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
    if (!kit.over) { g.update(dt, input, kit); kit._fireActions(input); }
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
      if (!kit.over) { g.update(dt, input, kit); kit._fireActions(input); }
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
// GENERIC invariants no genre-specific knowledge is needed for:
//   1. CONTROLS LIVE — run the sim with no input and with every direction key
//      mashed; if the two final worlds are identical, input does nothing.
//   2. NO WALL-CLIP — if a solid-cell lookup is reachable from state (a tilemap,
//      or the scaffold's state.solidAt), no colliding entity may rest in a solid cell.
//   3. NO SOLID OVERLAP — solid entities must not interpenetrate at rest.
//   4. ACTIONS LIVE — every kit.register binding must change something when pressed
//      (dead_action), and every non-movement spec control must be registered (unbound_control).
// Returns { ok, violations:[{kind, detail}] } — each violation is a fix prompt.
function worldOf(g) {
  return g.state && (Array.isArray(g.state.world) ? g.state.world
    : Array.isArray(g.state.entities) ? g.state.entities : []) || [];
}
function runSnapshot(gameFactory, { frames, dt, seed, script }) {
  const { g, kit } = realize(gameFactory, { gravity: 0 }, seed);
  const input = makeInput();
  if (g.init) g.init(kit);
  let f = 0;
  for (; f < frames && !kit.over; f++) {
    for (const c of script) if (c.frame === f) input._set(c.key, c.down !== false);
    g.update(dt, input, kit);
    kit._fireActions(input);
    input._endFrame();
  }
  return { g, kit, overAt: kit.over ? f : null };
}
export function probe(gameFactory, { frames = 240, dt = 1 / 60, seed = 1, src = "", scheme = "",
                                      controlKeys = null } = {}) {
  const violations = [];
  // The spec's control scheme, when it is a MOVEMENT scheme, makes displacement itself an
  // invariant (dead_movement below): dead_controls alone let a shipped game pass with dead movement
  // because a space-attack mutated state — "some key changed something" is not "the player can move".
  const movementScheme = /^(top-down|platformer|grid-turn)$/.test(scheme) || /-3d$/.test(scheme);
  const DIR_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "a", "d", "w", "s"]);

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
  const MIN_MOVE = 4; // px; below this over the whole window a "control" is effectively dead
  const snap = (script) => runSnapshot(gameFactory, { frames, dt, seed, script });

  let baseline, baseKit, baseOverAt;
  try { ({ g: baseline, kit: baseKit, overAt: baseOverAt } = snap([])); }
  catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
  // A game that resolves with NO input freezes every downstream signal (the engine stops update()
  // once kit.over is set), so a frame-0 win reads as dead_movement/dead_action and sends the fix
  // chasing movement ghosts (measured: a build oscillated 10+ steps on exactly this). A no-input WIN
  // is always a broken win condition; a no-input LOSS is only pathological when near-instant — a
  // late idle death (enemies reach the passive player) is legitimate design.
  if (baseKit.over && (baseKit.over.won || baseOverAt <= 120)) {
    const what = baseKit.over.won ? "WON" : "LOST";
    violations.push({ kind: "premature_end",
      detail: `the game ${what} (${JSON.stringify(baseKit.over.msg || "")}) after ${baseOverAt} `
        + `frame(s) with NO input at all — the ${baseKit.over.won ? "win" : "lose"} condition is `
        + `satisfied by the initial state, so play never happens (and every control then reads dead: `
        + `the engine stops update() once the game resolves). Fix the condition or the state it `
        + `checks — e.g. a counter that starts at its target, a win check against a list that starts `
        + `empty, a kill tally compared with >= 0. For a fast LOSS the classic cause is per-FRAME `
        + `contact damage: a touching enemy draining hp every frame kills in a fraction of a second — `
        + `damage on contact needs a cooldown (e.g. once per 0.5s) or knockback that separates. The `
        + `game must still be unresolved after ${frames} input-less frames.` });
    return { ok: false, violations };
  }
  const baseW = worldOf(baseline);
  // A steered player kept OUTSIDE state.world is invisible to the renderer AND to every displacement
  // measurement below — movement reads dead no matter how correct the mover is, and the fix loop
  // chases movement ghosts (measured: an orbital build churned 15 steps; the knight moved fine but
  // was never spawned into the world). Say the real thing and skip the artifact reports.
  // Membership is checked right after init (and again a few frames in), not on the 240-frame
  // baseline: a game may legitimately splice the player out on DEATH late in an idle run — judging
  // the corpse state false-flagged a correct game.
  let initRun, earlyRun;
  try {
    initRun = runSnapshot(gameFactory, { frames: 0, dt, seed, script: [] });
    earlyRun = runSnapshot(gameFactory, { frames: 8, dt, seed, script: [] });
  } catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
  const initPlayer = initRun.g.state && initRun.g.state.player;
  const initW = worldOf(initRun.g);
  if (initPlayer && typeof initPlayer === "object" && initW.length && !initW.includes(initPlayer)) {
    violations.push({ kind: "player_not_in_world",
      detail: `state.player is NOT a member of state.world — the renderer and this probe only see `
        + `entities IN state.world, so the player never appears on screen and its movement reads as `
        + `dead. In init, create the player WITH kit.spawn INTO the world and store that SAME object: `
        + `\`state.player = kit.spawn(state.world, { shape:"box", x,y,z, w,h,d, color })\` — never a `
        + `bare object kept outside the world, and never a copy (spawn once, reference it).` });
    return { ok: false, violations };
  }
  // A 3D scene with no ground plane renders as a VOID — the camera floats in fog with nothing to
  // stand on. Every runtime gate passed a shipped crypt whose init computed wall rects and spawned
  // neither walls nor ground; only a human in the browser saw it. Deterministic, so gate it.
  {
    const cfg = realize(gameFactory, { gravity: 0 }, seed).config;
    if (cfg.mode === "3d" && initW.length
        && !initW.some((e) => e && (e.shape === "ground" || e.shape === "heightfield"))) {
      violations.push({ kind: "no_ground",
        detail: `this 3D game spawns NO ground — the scene renders as a void (fog + floating `
          + `shapes). In init, spawn a ground plane FIRST, sized larger than the play area: `
          + `kit.spawn(state.world, { shape:"ground", size: 100, color:"#333" }) — or, for a walled `
          + `level (dungeon/maze/rooms), build it with kit.wallsFromTilemap(state.world, rows, opts) `
          + `which spawns the ground AND the wall boxes and returns the collision rects (store them `
          + `on state.walls; the scaffold collides the player with state.walls every frame).` });
    }
  }
  const earlyPlayer = earlyRun.g.state && earlyRun.g.state.player;
  const earlyW = worldOf(earlyRun.g);
  if (earlyPlayer && typeof earlyPlayer === "object" && earlyW.length && !earlyW.includes(earlyPlayer)) {
    violations.push({ kind: "player_not_in_world",
      detail: `state.player IS spawned into state.world by init, but update REMOVES it within `
        + `8 frames (no input, no death) — once out of the world the player disappears from the `
        + `screen and every movement/measurement reads dead. Some update loop splices or culls an `
        + `entity set that MATCHES THE PLAYER: selecting "enemies" by field presence (e.g. `
        + `\`e.hp !== undefined\`) matches the player too, and a contact check then treats the player `
        + `as touching itself (distance 0) and despawns it. Tag entities with an explicit kind `
        + `(\`e.kind = "slime"\`) and filter by kind — never by shared fields — and make contact `
        + `checks skip \`e === state.player\`.` });
    return { ok: false, violations };
  }
  // Base movement keys, plus every key literal the game ITSELF reads (a turn game's controls may be
  // Enter/e/r/1-9 — keys the base mash never touches, so a fully-working game read as dead), plus
  // every registered binding's keys (a register-wired action has no input.pressed literal for the
  // regex to find). Capped: each key costs a full sim run.
  const declared = src
    ? [...src.matchAll(/input\s*\.\s*(?:pressed|down)\(\s*["'`]([^"'`]{1,12})["'`]/g)].map((m) => m[1])
    : [];
  const boundKeys = (baseKit.bindings ? baseKit.bindings() : []).flatMap((b) => b.keys);
  const DIRS = [...new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "a", "d", "w", "s", " ",
                            ...declared, ...boundKeys])].slice(0, 20);

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
  try { digestReliable = digest(snap([]).g.state) === baseDigest; }
  catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
  let best = { key: null, disp: 0, spawned: false }, drivenForClip = baseline, stateChanged = false;
  let dirBest = { key: null, disp: 0 };   // strongest DIRECTIONAL displacement (dead_movement's signal)
  for (const key of DIRS) {
    let g;
    try { g = snap([{ frame: 0, key, down: true }]).g; }
    catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
    const w = worldOf(g);
    // Only a NET SPAWN counts as "input did something" (a bullet/particle appears). A DECREASE
    // (a brick smashed, a pellet eaten) happens under gameplay regardless of input, so it must NOT
    // read as a live control — that false-green let a breakout with a dead paddle pass.
    if (w.length > baseW.length) {
      best = { key, disp: Infinity, spawned: true }; drivenForClip = g;
      if (!movementScheme) break;   // a movement scheme still needs every directional key measured
      // NO continue: a net spawn must not skip the displacement measurement — in a game that spawns
      // continuously (most games) every directional key's run has a net spawn, so skipping here made
      // dead_movement report "strongest one (none), max 0.00" against a working mover (measured: an
      // orbital build churned 10 steps on that phantom).
    }
    if (digestReliable && !stateChanged && digest(g.state) !== baseDigest) stateChanged = true;
    let m = 0;
    const n = Math.min(w.length, baseW.length);
    for (let i = 0; i < n; i++)
      m = Math.max(m, Math.hypot((w[i].x || 0) - (baseW[i].x || 0), (w[i].y || 0) - (baseW[i].y || 0),
                                 (w[i].z || 0) - (baseW[i].z || 0)));
    if (DIR_KEYS.has(key) && m > dirBest.disp) dirBest = { key, disp: m };
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
  // dead_movement: the spec names a movement scheme, so the movement keys must DISPLACE something —
  // a live action key (state mutation, a spawn) cannot green a game the player can't steer.
  if (movementScheme && dirBest.disp < MIN_MOVE) {
    const alive = (stateChanged || best.spawned)
      ? " Other keys DO act (state changed / something spawned), so input is read — movement specifically is dead."
      : "";
    violations.push({ kind: "dead_movement",
      detail: `the spec's "${scheme}" control scheme requires the movement keys (WASD/arrows, held) to `
        + `displace the steered entity, but the strongest one (${dirBest.key || "none"}) moved nothing — max `
        + `${dirBest.disp.toFixed(2)} over ${frames} frames.${alive} Check that the scheme's movement `
        + `call runs EVERY frame off HELD keys (input.down) and that nothing undoes it afterwards — a `
        + `collision/clamp loop that zeroes the velocities or snaps the position back each frame is `
        + `the classic cause (resolve a collision by pushing OUT of the overlap, never by resetting `
        + `the move).` });
  }
  // wall-clip on the most-moving run — checked wherever a solid-cell lookup is reachable from
  // state: a tilemap (every sized entity is held to it, the original check), or the scaffold-read
  // `state.solidAt` contract (only `solid` entities — non-solid decor may legitimately sit in walls).
  const st = drivenForClip.state || {};
  const tm = st.tilemap && typeof st.tilemap.solidAt === "function" && st.tilemap.tile ? st.tilemap : null;
  const solidAtFn = tm ? (cx, cy) => tm.solidAt(cx, cy)
    : typeof st.solidAt === "function" ? st.solidAt : null;
  if (solidAtFn) {
    const cellPx = tm ? tm.tile : typeof st.cell === "number" ? st.cell : 32;
    const stuck = worldOf(drivenForClip).filter((e) => e.w > 0 && e.h > 0 && (tm || e.solid)
      && solidAtFn(Math.floor(e.x / cellPx), Math.floor(e.y / cellPx)));
    if (stuck.length) {
      violations.push({ kind: "wall_clip",
        detail: `${stuck.length} moving entit${stuck.length > 1 ? "ies rest" : "y rests"} INSIDE a `
          + `solid wall tile (e.g. ${stuck.slice(0, 3).map((e) => (e.type || e.tag || "entity")
          + `@${e.x?.toFixed(0)},${e.y?.toFixed(0)}`).join("; ")}). Block movement BEFORE an entity `
          + `enters a wall; never snap it to the wall tile's own center.` });
    }
  }
  // SOLID INTERPENETRATION: solid entities must not rest inside each other. After the settle run
  // (no input, `frames` frames) no two solid AABBs may overlap beyond a small epsilon — the shipped
  // enemies-stack-under-the-player failure. The fix is ONE pass: tag participants `solid: true` and
  // let kit.collideWorld separate them (the scaffold already calls it), never gameplay-side nudges.
  // 2D only: the pass and this check are x/y AABBs, and no 3D solid primitive exists (two 3D
  // entities apart in z would false-read as overlapping).
  const OVERLAP_EPS = 0.5;
  const mode3d = realize(gameFactory, { gravity: 0 }, seed).config.mode === "3d";
  const solidsAtRest = mode3d ? [] : baseW.filter((e) => e.solid && !e.dead && e.w > 0 && e.h > 0);
  const pairs = [];
  for (let i = 0; i < solidsAtRest.length; i++)
    for (let j = i + 1; j < solidsAtRest.length; j++) {
      const a = solidsAtRest[i], b = solidsAtRest[j];
      if (!aabb(a, b)) continue;
      const depth = Math.min(a.x + a.w - b.x, b.x + b.w - a.x, a.y + a.h - b.y, b.y + b.h - a.y);
      if (depth > OVERLAP_EPS) pairs.push(`${a.type || a.tag || "entity"}@${a.x?.toFixed(0)},${a.y?.toFixed(0)}`
        + ` into ${b.type || b.tag || "entity"}@${b.x?.toFixed(0)},${b.y?.toFixed(0)} by ${depth.toFixed(0)}px`);
    }
  if (pairs.length) {
    violations.push({ kind: "solid_overlap",
      detail: `${pairs.length} solid entity pair(s) interpenetrate at rest (e.g. ${pairs.slice(0, 3).join("; ")}). `
        + `Solid entities must stay separated by the collision pass: keep them tagged solid:true and let `
        + `kit.collideWorld(state.world, state.solidAt, state.cell) run every frame (the scaffold calls it) — `
        + `do NOT spawn them on top of each other, and never undo the separation in update().` });
  }
  // DEAD ACTION: every registered binding must DO something. A pulsed press of its keys (settle
  // frames between presses) must produce a state delta vs the no-input baseline — a non-positional
  // state mutation, a spawn/despawn, or an entity displacement. This is the incident invariant: a
  // shipped game had a full melee implementation behind a key read that never fired. Skip list is
  // EXPLICIT: "interact" is scaffold/kit-owned wiring whose effect is proximity-gated (no talker
  // near spawn ⇒ legitimately no delta), so a spawn-position probe cannot assert it.
  const DEAD_ACTION_SKIP = new Set(["interact"]);
  const allBindings = baseKit.bindings ? baseKit.bindings() : [];
  for (const b of allBindings.filter((x) => !DEAD_ACTION_SKIP.has(x.name)).slice(0, 8)) {
    const script = [];
    let presses = 0;
    for (let f = 10; f + 13 < frames; f += 30) {
      presses++;
      for (const k of b.keys) script.push({ frame: f, key: k, down: true }, { frame: f + 3, key: k, down: false });
    }
    if (!b.keys.length || !presses) continue;
    let run, runKit;
    try { ({ g: run, kit: runKit } = snap(script)); }
    catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
    const w = worldOf(run);
    let disp = 0;
    for (let i = 0; i < Math.min(w.length, baseW.length); i++)
      disp = Math.max(disp, Math.hypot((w[i].x || 0) - (baseW[i].x || 0), (w[i].y || 0) - (baseW[i].y || 0),
                                       (w[i].z || 0) - (baseW[i].z || 0)));
    const acted = w.length !== baseW.length || disp > 0.5 || !!runKit.over !== !!baseKit.over
      || (digestReliable && digest(run.state) !== baseDigest);
    if (!acted) {
      violations.push({ kind: "dead_action",
        detail: `registered action "${b.name}" (keys ${JSON.stringify(b.keys)}) was pressed ${presses} `
          + `time(s) over ${frames} frames and changed NOTHING — no state mutation, no spawn/despawn, `
          + `no entity moved. Its handler is a no-op or its effect is gated off from the initial state `
          + `(a flag never set, a cost never payable, a target never in range). Wire the real mechanic `
          + `into the kit.register handler so the action ACTS.` });
    }
  }
  // UNBOUND CONTROL: the frozen spec promised each key in its `controls` map does something; a
  // NON-movement spec key with no registered action is unverifiable wiring (the same shipped
  // incident, seen from the spec side). Skip lists are EXPLICIT, not inferred: the scheme's own
  // movement keys (movement liveness is dead_movement's job; platformer's space-jump is
  // scaffold-owned movement too) and mouse tokens (the mouse is the camera outside "fp" —
  // dead_mouse_control owns that failure).
  if (controlKeys && typeof controlKeys === "object") {
    const ALIAS = { space: " ", spacebar: " ", esc: "Escape", escape: "Escape", enter: "Enter",
      return: "Enter", tab: "Tab", shift: "Shift", ctrl: "Control", control: "Control",
      up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft", right: "ArrowRight",
      arrowup: "ArrowUp", arrowdown: "ArrowDown", arrowleft: "ArrowLeft", arrowright: "ArrowRight" };
    const MOVEMENT = new Set(["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright",
      "wasd", "arrows", "arrowkeys", "arrow"]);
    if (scheme === "platformer") MOVEMENT.add(" ");
    // Substring match, not exact tokens: specs write mouse keys in every shape ("MOUSE_MOVE",
    // "LEFT_CLICK", "RightMouseButton") and an unmatched one ping-pongs the fix loop — unbound_control
    // demands a registration that dead_action then kills as a no-op (there are no mouse keys to bind).
    const MOUSE = /mouse|click|pointer|cursor|drag|wheel|scroll|lmb|rmb|mmb/;
    // Kit-owned reads: menu digits (kit.menuPick / talkStep choices) and Escape (talkStep close)
    // are consumed by kit loops the game never registers — a spec "1-9: choose" is already wired.
    const KIT_OWNED = /^(\d(-\d)?|Escape)$/;
    const bound = new Set();
    for (const b of allBindings) for (const k of b.keys) bound.add(k);
    for (const [rawKey, what] of Object.entries(controlKeys)) {
      const tokens = String(rawKey).split(/[\s/+,|]+/).filter(Boolean)
        .map((t) => { const lc = t.toLowerCase(); return ALIAS[lc] ?? (t.length === 1 ? lc : t); });
      const actionable = tokens.filter((k) => !MOVEMENT.has(k.toLowerCase()) && !MOUSE.test(k.toLowerCase())
        && !KIT_OWNED.test(k));
      if (!actionable.length || actionable.some((k) => bound.has(k))) continue;
      violations.push({ kind: "unbound_control",
        detail: `the spec binds ${JSON.stringify(rawKey)} to "${what}" but no registered action listens `
          + `to ${actionable.map((k) => JSON.stringify(k)).join("/")}. Every non-movement spec control `
          + `must be wired in init via kit.register — e.g. kit.register("action", `
          + `[${actionable.map((k) => JSON.stringify(k)).join(", ")}], () => { /* mutate state */ }). A bare `
          + `input.pressed(...) scattered in update is invisible to this gate (held mechanics may still `
          + `read input.down each frame — this rule targets EDGE actions).` });
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
      if (!kit.over) { g.update(dt, input, kit); kit._fireActions(input); }
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
        kit._fireActions(input);
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
