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

// ── data-driven visuals ──────────────────────────────────────────────────────
// A row's id is also its ASSET id, so an entity spawned from a row carries its own binding
// (`mesh` in 3D, `sprite` in 2D) and the skin stage never has to rewrite source to tag it.
// `size` is 3D world units / 2D pixels; shape/color/parts are optional.
const DATA_COLOR = "#c8c8c8";

export function dataVisual(row, mode) {
  const s = (row && row.size) || {};
  const color = row.color || DATA_COLOR;
  if (mode === "3d") {
    const shape = row.shape || "box";
    const w = s.w ?? 1, h = s.h ?? 1, d = s.d ?? s.w ?? 1;
    const base = shape === "sphere" ? { shape, r: Math.max(w, h) / 2 } : { shape, w, h, d };
    return { ...base, color, mesh: row.id };
  }
  const vis = { shape: row.shape || "rect", w: s.w ?? 16, h: s.h ?? 16, color, sprite: row.id };
  if (Array.isArray(row.parts) && row.parts.length) vis.parts = row.parts;
  return vis;
}

// `at` supplies position, and overrides anything else. `type` defaults to the row id: gameplay
// branches on `e.type`, and a row-spawned entity with none is invisible to every one of them —
// measured, a game's own bullets passed through the boss because their type was undefined.
export function spawnData(world, row, at, mode) {
  return spawn(world, { type: row.id, ...dataVisual(row, mode), ...(at || {}) });
}

// Draw one entity (2D): its loaded sprite, else its shape/parts. 3D needs no equivalent — the
// scene renders from shape tags and `mesh` is one.
export function drawEntity(g, e, sprites) {
  const img = e.sprite ? (sprites || {})[e.sprite] : null;
  if (img) return g.sprite(img, e.x, e.y, e.w, e.h);
  const color = e.color || DATA_COLOR;
  // `parts` are fractions of the entity's box, so one description works at any size.
  if (Array.isArray(e.parts) && e.parts.length) {
    for (const p of e.parts) {
      const px = e.x + (p.dx || 0) * e.w, py = e.y + (p.dy || 0) * e.h;
      const pw = (p.w ?? 1) * e.w, ph = (p.h ?? 1) * e.h;
      const pc = p.color || color;
      if (p.shape === "circle") g.circle(px + pw / 2, py + ph / 2, Math.min(pw, ph) / 2, pc);
      else g.rect(px, py, pw, ph, pc);
    }
    return;
  }
  if (e.shape === "circle") g.circle(e.x + e.w / 2, e.y + e.h / 2, Math.min(e.w, e.h) / 2, color);
  else g.rect(e.x, e.y, e.w, e.h, color);
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

export function collideWorld(world, solidAt = null, cell = 32) {
  const solids = world.filter((e) => e.solid && !e.dead && e.w > 0 && e.h > 0);
  for (let pass = 0; pass < 8; pass++) {
    let moved = false;
    const order = [...solids].sort((a, b) => a.x - b.x);
    for (let i = 0; i < order.length; i++) {
      const a = order[i];
      for (let j = i + 1; j < order.length; j++) {
        const b = order[j];
        if (b.x >= a.x + a.w) break;
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
export function wander(e, speed, turn = 3) {             // drift, turning by up to `turn` rad/step
  e._heading = (e._heading ?? Math.random() * 6.283) + (Math.random() - 0.5) * turn;
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
export function wander3(e, speed, dt, turn = 2) {        // amble: drift on x/z, slowly turning
  e._heading = (e._heading ?? Math.random() * 6.283) + (Math.random() - 0.5) * turn * dt * 6;
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
// Spawn a radial burst of short-lived particles into `world`.
export function burst(world, x, y, n = 12, { speed = 120, life = 0.5, color = "#fd0", size = 3 } = {}) {
  for (let i = 0; i < n; i++) {
    const a = Math.random() * 6.283, s = speed * (0.4 + Math.random() * 0.6);
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

// ── focus: WHAT the player is about to act on ────────────────────────────────────────────────
// ONE activate key can only mean the right thing if something decides what is in front of the
// player. That decision — inside the facing cone, within reach, nearest wins — is identical in
// every game, and hand-writing it per verb is where generation goes wrong: a measured build
// checked the cone for tool use and forgot it for animals, so the farmer tended a cow standing
// behind him. An entity opts in by carrying `action` (the verb id the game switches on) and an
// optional `label` (what the prompt calls it).
const _FOCUS_RANGE = 3, _FOCUS_CONE = 0.35;

// Unit facing on the entity's OWN plane, from whichever heading field its movement scheme sets:
// `ry` (3D mesh facing, points to (-sin, -cos)), `yaw` (first-person aim, (sin, -cos)) or `angle`
// (2D travel direction). Zero-length when the entity has never faced anywhere.
export function facing(e) {
  if (e.ry != null) return { x: -Math.sin(e.ry), z: -Math.cos(e.ry) };
  if (e.yaw != null) return { x: Math.sin(e.yaw), z: -Math.cos(e.yaw) };
  if (e.angle != null) return { x: Math.cos(e.angle), z: Math.sin(e.angle) };
  return { x: 0, z: 0 };
}

export function focusTarget(state, opts = {}) {
  const p = state && state.player;
  if (!p || state.talk) return null;              // no target while a dialogue owns the screen
  const range = opts.range ?? _FOCUS_RANGE, cone = opts.cone ?? _FOCUS_CONE;
  const flat = typeof p.z !== "number";           // 2D games act on the x/y plane, 3D on x/z
  const f = facing(p), fx = f.x, fy = f.z;        // `facing` names the second axis z; in 2D it is y
  const aimed = Math.hypot(fx, fy) > 0.001;       // never faced anywhere yet ⇒ distance alone decides
  let best = null, bestD = Infinity;
  for (const e of state.world ?? []) {
    if (e === p || e.dead || !e.action) continue;
    const dx = e.x - p.x, dy = flat ? (e.y ?? 0) - (p.y ?? 0) : (e.z ?? 0) - p.z;
    const d = Math.hypot(dx, dy);
    if (d > (e.reach ?? range) || d >= bestD) continue;
    if (aimed && d > 0.001 && (dx * fx + dy * fy) / d < cone) continue;
    best = e; bestD = d;
  }
  return best;
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
export function makeDraw(ctx, kit = null) {
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
    // ENGINE-INTERNAL (drawEntity + the HUD icon item); not on the game's typed surface.
    sprite: (img, x, y, w, h) => {
      const im = typeof img === "string" ? (kit ? kit.sprite(img) : null) : img;
      if (im) ctx.drawImage(im, x, y, w, h);
    },
    // camera offset: push(cam) before drawing WORLD-space things, pop() before HUD/screen-space.
    push: (cam) => { ctx.save(); ctx.translate(-(cam?.x || 0), -(cam?.y || 0)); },
    pop: () => ctx.restore(),
  };
}

// ── 2D scene render (engine-owned; games have no draw hook) ──────────────────
function drawTilemap(draw, map, config) {
  const colors = config.tileColors || {};
  for (let cy = 0; cy < map.h; cy++) {
    for (let cx = 0; cx < map.w; cx++) {
      const color = colors[map.at(cx, cy)] || (map.solidAt(cx, cy) ? "#555" : null);
      if (color) draw.rect(cx * map.tile, cy * map.tile, map.tile, map.tile, color);
    }
  }
}

export function renderScene(draw, g, kit, config) {
  const state = g.state || {};
  draw.clear(config.background || "#000");
  if (config.backdrop) draw.sprite(config.backdrop, 0, 0, config.width, config.height);
  const cam = state.cam && typeof state.cam.x === "number" ? state.cam : { x: 0, y: 0 };
  draw.push(cam);
  const map = state.tilemap;
  if (map && typeof map.at === "function") drawTilemap(draw, map, config);
  const world = Array.isArray(state.world) ? state.world : [];
  let live = world.filter((e) => e && !e.dead);
  if (live.some((e) => e.layer)) live = [...live].sort((a, b) => (a.layer || 0) - (b.layer || 0));
  for (const e of live) kit.drawEntity(draw, e);
  draw.pop();
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
    } else if (it.kind === "icon") {
      if (typeof it.id !== "string" || !it.id) return "hud icon item needs an asset 'id' string";
    } else {
      return `unknown hud item kind ${JSON.stringify(it.kind)} — use text | bar | banner | panel | menu | marker | icon`;
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
// Boxes STACK like every other HUD item: a dialogue panel and its choice menu are two items, and
// placing each by anchor alone drew them on top of each other (shipped: an options box over the
// line it was answering). `stack` carries the next free y per anchor across one render.
function _hudBoxPos(anchor, W, H, bw, bh, stack = {}) {
  const pad = 24, gap = 8;
  const x = anchor.endsWith("left") ? pad : anchor.endsWith("right") ? W - pad - bw : (W - bw) / 2;
  const up = anchor.startsWith("bottom");
  if (stack[anchor] == null)
    stack[anchor] = anchor.startsWith("top") ? pad : up ? H - pad : (H - bh) / 2;
  const y = up ? stack[anchor] - bh : stack[anchor];
  stack[anchor] = up ? y - gap : y + bh + gap;
  return { x, y };
}
function _drawPanel(draw, it, W, H, stack) {
  const lines = String(it.text).split("\n");
  const bw = Math.min(W - 48, 680), titleH = it.title ? 30 : 0;
  const bh = 24 + titleH + lines.length * 24;
  const { x, y } = _hudBoxPos(it.at || "bottom", W, H, bw, bh, stack);
  _hudBox(draw, x, y, bw, bh);
  let ty = y + 22;
  if (it.title) { draw.text(String(it.title), x + 18, ty, it.color || "#ffd27a", 20, "left"); ty += 30; }
  for (const ln of lines) { draw.text(ln, x + 18, ty, "#f2ead8", 16, "left"); ty += 24; }
}
function _drawMenu(draw, it, W, H, stack) {
  const opts = it.options.map(String);
  const bw = Math.min(W - 48, 560), titleH = it.title ? 32 : 0;
  const bh = 20 + titleH + opts.length * 30;
  const { x, y } = _hudBoxPos(it.at || "center", W, H, bw, bh, stack);
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
  const boxes = {};    // the same, for the boxed items (panel/menu), which have their own metrics
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
    if (it.kind === "panel") { _drawPanel(draw, it, W, H, boxes); continue; }
    if (it.kind === "menu") { _drawMenu(draw, it, W, H, boxes); continue; }
    const anchor = it.at || "top-left";
    const down = !anchor.startsWith("bottom");
    if (cursor[anchor] == null) cursor[anchor] = down ? _HUD_PAD + 16 : H - _HUD_PAD;
    const y = cursor[anchor];
    const { x, align } = xOf(anchor);
    if (it.kind === "text") {
      draw.text(String(it.text), x, y, it.color || "#fff", it.size || 16, align);
    } else if (it.kind === "icon") {
      const s = it.size || 24;
      const ix = align === "right" ? x - s : align === "center" ? x - s / 2 : x;
      draw.sprite(it.id, ix, y - s + 4, s, s);
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
export function makeKit(config) {
  let over = null; // null | {won:bool, msg}
  let sprites = {}; // id -> loaded Image; empty headless (kit.sprite always null -> game falls to shapes)
  let toasts = []; // transient notify() messages; stepped+drawn by the frame loop, inert headless
  let focused = null; // last kit.focus() result; the frame loop draws its prompt like a toast
  // Registered ACTIONS (name -> {keys, fn}). Registration makes the spec's key bindings
  // machine-readable: a binding's keys can be listed, remapped or shown in a HUD
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
    V,
    spawn, cull, integrate, integrate3, physics3, heading3, flyer, aabb, resolveAabb, makeTilemap,
    physics, walk, jump, collideWorld, seek, flee, arrive, pursue, wander, astar, cellCenter,
    seek3, flee3, wander3, patrol3, avoidRects, wallsFromTilemap,
    gridMove, burst, stepParticles, makeCamera: () => makeCamera(config),
    chaseCam, moveTopDown, moveTopDown3, moveTank3, moveRelative, mouseLook, fpCam, moveFP,
    drive: (e, input, dt, speed) => driveScheme(config.controls, e, input, dt, speed),
    menuPick, talkOpen, talkStep, talkHud, facing,
    // The one thing `activate` would act on right now, also parked on state.focus for the game to
    // switch on. The engine draws its prompt, so the player is never guessing what the key does.
    focus(state, opts) {
      focused = focusTarget(state, opts);
      state.focus = focused;
      return focused;
    },
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
    spawnData: (world, row, at) => spawnData(world, row, at, config.mode),
    dataVisual: (row) => dataVisual(row, config.mode),
    drawEntity: (g, e) => drawEntity(g, e, sprites),
    sprite: (id) => sprites[id] || null,
    _setSprites(map) { sprites = map || {}; },
    _stepToasts(dt) { for (const t of toasts) t.ttl -= dt; toasts = toasts.filter((t) => t.ttl > 0); },
    _toastItems: () => toasts.map((t) => ({ kind: "text", text: t.msg, at: "top", color: "#ffe9b0", size: 18 })),
    // The activate prompt is the ENGINE's, not the game's: the key it names is the one actually
    // registered, so a rebind can never leave the screen telling the player to press the old key.
    _focusItems() {
      if (!focused) return [];
      const a = actions.get("activate");
      const key = a && a.keys[0];
      const name = key === " " ? "Space" : key ? (key.length === 1 ? key.toUpperCase() : key) : "";
      const what = focused.label || focused.name || focused.action;
      return [{ kind: "text", text: name ? `${name} — ${what}` : String(what),
                at: "bottom", color: "#ffe9b0", size: 18 }];
    },
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

// Build a game instance holding a REAL kit. The kit needs the game's config (size), but config
// lives inside createGame — so peek it with a throwaway null-kit instance, build the kit from it, then
// re-instantiate WITH the kit. This is why createGame(kit) receives a live kit: a helper that closes
// over the `kit` parameter works, not just the kit passed into init/update. (Don't touch kit at the
// createGame top level — it runs during the config peek, before the kit exists; use it in init/update
// and in helpers those call.)
export function realize(factory, defaults = {}) {
  const peek = typeof factory === "function" ? factory(null) : factory;
  const config = { ...defaults, ...(peek.config || {}) };
  const kit = makeKit(config);
  const g = typeof factory === "function" ? factory(kit) : peek;
  return { g, config, kit };
}

// ── browser entry: run a real animation loop with real input + canvas ────────
export async function run(game, canvas, assetBase) {
  const { g, config, kit } = realize(game, { width: 960, height: 540, background: "#111", gravity: 0 });
  canvas.width = config.width; canvas.height = config.height;
  fitToWindow(canvas, config.width / config.height);
  const ctx = canvas.getContext("2d");
  const draw = makeDraw(ctx, kit);
  const input = makeInput();
  kit._setSprites(await loadSprites(assetBase));

  const keymap = (e) => e.key.length === 1 ? e.key.toLowerCase() : e.key;
  // The browser's own meaning for these steals the game: space scrolls, arrows scroll, Tab moves
  // focus off the canvas mid-play.
  addEventListener("keydown", (e) => {
    input._set(keymap(e), true);
    if (e.key.startsWith("Arrow") || e.key === " " || e.key === "Tab") e.preventDefault();
  });
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
    renderScene(draw, g, kit, config);
    const hudItems = [...(g.hud ? g.hud(kit) || [] : []), ...kit._focusItems(), ...kit._toastItems()];
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
export function simulate(game, { frames = 600, dt = 1 / 60, script = [] } = {}) {
  const { g, config, kit } = realize(game, { width: 640, height: 480, gravity: 0 });
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


// ── render smoke: exercise the screen-space path headless can't otherwise see ─
// simulate() steps update() ONLY. The game itself never draws — the ENGINE renders the scene from
// state.world — so this runs that render (a malformed entity crashes it) plus hud(), which is the
// only screen-space DATA the game still authors. It does NOT judge whether the game painted
// "enough": an idle/menu/text game legitimately paints no world.
export function renderSmoke(gameFactory, { frames = 120, dt = 1 / 60 } = {}) {
  const { g, config, kit } = realize(gameFactory, { width: 640, height: 480, gravity: 0 });
  const hasHud = typeof g.hud === "function";
  const is3d = config.mode === "3d";
  if (typeof g.draw === "function") {
    return { ok: false, violations: [{ kind: "draw_hook",
      detail: `a game must NOT define draw() — the engine renders the scene from state.world, using `
        + `each entity's sprite/shape/color. Delete draw(); spawn what should be visible into `
        + `state.world, and return screen overlay from hud(kit).` }] };
  }
  const input = makeInput();
  const noop = () => {};
  const rec = { ctx: {}, clear: noop, push: noop, pop: noop,
                rect: noop, circle: noop, line: noop, text: noop, sprite: noop };
  const hudRec = rec;
  try {
    if (g.init) g.init(kit);
    for (let f = 0; f < frames; f++) {
      if (!kit.over) { g.update(dt, input, kit); kit._fireActions(input); }
      input._endFrame();
      if (!is3d) renderScene(rec, g, kit, config);
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
    const which = is3d ? "hud()" : "the scene render/hud()";
    return { ok: false, violations: [{ kind: "draw_crash",
      detail: `${which} threw during render: ${String(e && e.stack || e)}. These must only READ state; `
        + `never mutate state or read undefined fields.` }] };
  }
  return { ok: true };
}

