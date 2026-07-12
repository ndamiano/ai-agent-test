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
  const next = () => {
    // xorshift32
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 0xffffffff;
  };
  return {
    next,
    range: (lo, hi) => lo + next() * (hi - lo),
    int: (lo, hi) => Math.floor(lo + next() * (hi - lo + 1)),
    pick: (arr) => arr[Math.floor(next() * arr.length)],
    chance: (p) => next() < p,
  };
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
  if (e.y <= ground) { e.y = ground; e.vy = 0; e.grounded = true; } else e.grounded = false;
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
    down: (k) => held.has(k),
    pressed: (k) => edge.has(k),
    _set(k, v) { if (v) { if (!held.has(k)) edge.add(k); held.add(k); } else held.delete(k); },
    _endFrame() { edge.clear(); },
  };
}

// ── draw api (canvas2d) — the ONLY render surface ────────────────────────────
function makeDraw(ctx) {
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

// ── the runner: shared control surface the kit hands the game ─────────────────
export function makeKit(config, rng) {
  let over = null; // null | {won:bool, msg}
  return {
    config,
    rng,
    V,
    spawn, cull, integrate, integrate3, physics3, heading3, flyer, aabb, resolveAabb, makeTilemap,
    physics, walk, jump, seek, flee, arrive, pursue, wander, astar, cellCenter,
    gridMove, burst, stepParticles, makeCamera: () => makeCamera(config),
    audio: { play: () => {} }, // stub; real backend wired later
    win: (msg = "You win") => { if (!over) over = { won: true, msg }; },
    lose: (msg = "Game over") => { if (!over) over = { won: false, msg }; },
    get over() { return over; },
    _reset() { over = null; },
  };
}

// ── browser entry: run a real animation loop with real input + canvas ────────
export function run(game, canvas) {
  const g = typeof game === "function" ? game(null) : game;
  const config = { width: 640, height: 480, background: "#111", gravity: 0, ...(g.config || {}) };
  canvas.width = config.width; canvas.height = config.height;
  const ctx = canvas.getContext("2d");
  const draw = makeDraw(ctx);
  const input = makeInput();
  const rng = makeRng(config.seed || 1);
  const kit = makeKit(config, rng);

  const keymap = (e) => e.key.length === 1 ? e.key.toLowerCase() : e.key;
  addEventListener("keydown", (e) => input._set(keymap(e), true));
  addEventListener("keyup", (e) => input._set(keymap(e), false));
  canvas.addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    input.pointer.x = e.clientX - r.left; input.pointer.y = e.clientY - r.top;
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
    draw.clear(config.background);
    g.draw(draw, kit);
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
  const g = typeof game === "function" ? game(null) : game;
  const config = { width: 640, height: 480, gravity: 0, ...(g.config || {}) };
  const input = makeInput();
  const rng = makeRng(config.seed || seed);
  const kit = makeKit(config, rng);
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
      for (const e of world) {
        for (const k of ["x", "y", "z", "vx", "vy", "vz"]) {
          if (e[k] !== undefined && badNum(e[k])) return { ok: false, frame: f, phase: "diverged",
            error: `entity field ${k}=${e[k]} is not finite` };
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
  const g = typeof gameFactory === "function" ? gameFactory(null) : gameFactory;
  const config = { gravity: 0, ...(g.config || {}) };
  const input = makeInput();
  const kit = makeKit(config, makeRng(config.seed || seed));
  if (g.init) g.init(kit);
  for (let f = 0; f < frames && !kit.over; f++) {
    for (const c of script) if (c.frame === f) input._set(c.key, c.down !== false);
    g.update(dt, input, kit);
    input._endFrame();
  }
  return g;
}
export function probe(gameFactory, { frames = 240, dt = 1 / 60, seed = 1 } = {}) {
  const violations = [];
  const DIRS = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "a", "d", "w", "s", " "];
  const MIN_MOVE = 4; // px; below this over the whole window a "control" is effectively dead
  const snap = (script) => runSnapshot(gameFactory, { frames, dt, seed, script });

  let baseline;
  try { baseline = snap([]); }
  catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
  const baseW = worldOf(baseline);

  // Per-key SUSTAINED hold (one key at a time, so left/right can't cancel). Measure the biggest
  // input-caused displacement of any entity vs the no-input baseline (deterministic → the diff
  // isolates the input's effect). A spawn (bullet) => controls clearly live.
  let best = { key: null, disp: 0, spawned: false }, drivenForClip = baseline;
  for (const key of DIRS) {
    let g;
    try { g = snap([{ frame: 0, key, down: true }]); }
    catch (e) { return { ok: false, violations: [{ kind: "crash", detail: String(e && e.stack || e) }] }; }
    const w = worldOf(g);
    if (w.length !== baseW.length) { best = { key, disp: Infinity, spawned: true }; drivenForClip = g; break; }
    let m = 0;
    for (let i = 0; i < w.length; i++)
      m = Math.max(m, Math.hypot((w[i].x || 0) - (baseW[i].x || 0), (w[i].y || 0) - (baseW[i].y || 0),
                                 (w[i].z || 0) - (baseW[i].z || 0)));
    if (m > best.disp) { best = { key, disp: m, spawned: false }; drivenForClip = g; }
  }
  if (!best.spawned && best.disp < MIN_MOVE) {
    violations.push({ kind: "dead_controls",
      detail: `no key moves the player: the strongest input (${best.key}) shifted every entity by at `
        + `most ${best.disp.toFixed(2)}px over ${frames} frames. It likely responds but FAR too slowly — `
        + `kit velocities are px/SECOND and integrate/physics apply dt for you; NEVER use per-frame `
        + `magnitudes. Typical: walker ~150 px/s, jump ~600 px/s, gravity ~2000 px/s². Prefer `
        + `kit.walk(e,dir,speed) / kit.jump(e,speed) / kit.physics(e,dt,solids,gravity) so dt is handled.` });
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
// simulate() steps update() ONLY; a game can pass it yet crash or paint nothing in draw() — a
// blank/broken browser screen. Call draw() against a recording mock each frame: catch throws
// (draw_crash) and require ≥1 visible primitive over the run (draw_blank). 3D games have no
// draw() (the runtime syncs meshes from shape tags), so they pass through.
export function renderSmoke(gameFactory, { frames = 120, dt = 1 / 60, seed = 1 } = {}) {
  const g = typeof gameFactory === "function" ? gameFactory(null) : gameFactory;
  const config = { width: 640, height: 480, gravity: 0, ...(g.config || {}) };
  if (config.mode === "3d" || typeof g.draw !== "function") return { ok: true, skipped: true };
  const input = makeInput();
  const kit = makeKit(config, makeRng(config.seed || seed));
  let content = 0;
  const bump = () => { content++; };
  const rec = { ctx: {}, clear: () => {}, push: () => {}, pop: () => {},
                rect: bump, circle: bump, line: bump, text: bump, sprite: bump };
  try {
    if (g.init) g.init(kit);
    for (let f = 0; f < frames; f++) {
      if (!kit.over) g.update(dt, input, kit);
      input._endFrame();
      g.draw(rec, kit);
    }
  } catch (e) {
    return { ok: false, violations: [{ kind: "draw_crash",
      detail: `draw() threw during render: ${String(e && e.stack || e)}. draw(g,kit) must only READ `
        + `state and call g.rect/circle/line/text/sprite; never mutate state or read undefined fields.` }] };
  }
  if (content === 0) {
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
  const g0 = typeof gameFactory === "function" ? gameFactory(null) : gameFactory;
  const config = { width: 640, height: 480, gravity: 0, ...(g0.config || {}) };
  if (config.mode === "3d") return { ok: true, skipped: true };
  const KEYS = ["d", "ArrowRight", "a", "ArrowLeft"];
  let best = { span: 0, camDx: 0, drew: false };
  for (const key of KEYS) {
    const g = typeof gameFactory === "function" ? gameFactory(null) : gameFactory;
    const input = makeInput();
    const kit = makeKit(config, makeRng(config.seed || seed));
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
