// engine3d.js — the 3D RENDER layer (three.js). Browser-only; imports three, so it is
// kept OUT of engine.js (which must stay zero-dep for the headless sim gradient).
//
// The sim/render law is unchanged: a 3D game's update(dt,input,kit) mutates plain entity
// state (x,y,z,vx,vy,vz) exactly like a 2D game — so it still runs headless in pure Node
// and the probe still gates it. This module ONLY renders that state: it reads world entities
// and draws one mesh each from a shape tag. The model writes NO three.js.
//
// Entity render convention (fields on a world entity):
//   { shape: "box",    x,y,z, w,h,d, color, ry? }   // ry = yaw radians (optional)
//   { shape: "sphere", x,y,z, r,      color }
//   { shape: "ground", size, color, y? }             // flat plane centered at origin, y default 0
// A 3D game object also gets one optional hook:
//   camera(cam, kit)  // set cam.x/y/z (eye) and cam.tx/ty/tz (look-at) each frame; else a default 3/4 view

import * as THREE from "./vendor/three.module.js";
import { GLTFLoader } from "./vendor/GLTFLoader.js";
import { makeInput, chaseCam, schemeCamera, realize, makeDraw, renderHud } from "./engine.js";

function worldOf(g) {
  return (g.state && (Array.isArray(g.state.world) ? g.state.world
    : Array.isArray(g.state.entities) ? g.state.entities : [])) || [];
}

// Preload the game's meshes (assets.json `meshes: [{id,file}]`) into an {id: THREE.Object3D} map.
// A missing/empty manifest yields {} so every entity falls back to its primitive shape — meshes are
// a pure skin over a game that already renders as boxes/spheres, exactly like 2D sprites.
async function loadMeshes(assetBase) {
  if (!assetBase) return {};
  let manifest;
  try {
    const res = await fetch(`${assetBase}/assets.json`);
    if (!res.ok) return {};
    manifest = await res.json();
  } catch { return {}; }
  const meshes = manifest && Array.isArray(manifest.meshes) ? manifest.meshes : [];
  const loader = new GLTFLoader();
  const entries = await Promise.all(meshes.map((m) =>
    loader.loadAsync(`${assetBase}/${m.file}`).then(
      (gltf) => [m.id, gltf.scene],
      () => null)));
  return Object.fromEntries(entries.filter(Boolean));
}

// Skin an entity with its preloaded GLB (recentered + scaled to the entity's placeholder box), or
// fall back to the primitive shape. The GLB is nested under a pivot so the sim's position/yaw still
// apply at the entity's center regardless of the model's own origin.
function skinnedMesh(model, e) {
  const inst = model.clone(true);
  const box = new THREE.Box3().setFromObject(inst);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  inst.position.sub(center);  // recenter the model on the pivot origin (in model units, pre-scale)
  const pivot = new THREE.Group();
  pivot.add(inst);
  const s = (n, d) => (d > 1e-6 ? n / d : 1);
  if (e.shape === "sphere") {
    const u = s(2 * (e.r || 1), Math.max(size.x, size.y, size.z));
    pivot.scale.setScalar(u);
  } else {
    // UNIFORM scale that fits the model inside the entity's box — per-axis scaling distorts a real
    // model (a dog squished to a cube). Keep the model's proportions; fill the box on its tightest axis.
    const u = Math.min(s(e.w || 1, size.x), s(e.h || 1, size.y), s(e.d || 1, size.z));
    pivot.scale.setScalar(u);
  }
  return pivot;
}

// A terrain patch: a subdivided plane displaced by a height grid and tinted per-vertex by a color
// grid (biome/street colors). One mesh for a whole town, lit + smooth — replaces a field of boxes.
// e = { shape:"heightfield", grid:number[gh][gw], colors:string[gh][gw], cell, y? }.
function buildHeightfield(e, assetBase) {
  const grid = e.grid, colors = e.colors;
  const gh = grid.length, gw = grid[0].length, cell = e.cell || 2;
  // PlaneGeometry gives a gw×gh vertex grid (row-major); we overwrite each vertex to its exact world
  // (x, height, z) — no rotation — so terrain lines up cell-for-cell with where the game places things.
  const geo = new THREE.PlaneGeometry(1, 1, gw - 1, gh - 1);
  const pos = geo.attributes.position, col = new Float32Array(pos.count * 3), c = new THREE.Color();
  for (let r = 0; r < gh; r++) {
    for (let cx = 0; cx < gw; cx++) {
      const i = r * gw + cx;
      pos.setXYZ(i, (cx - (gw - 1) / 2) * cell, grid[r][cx], (r - (gh - 1) / 2) * cell);
      c.set((colors && colors[r] && colors[r][cx]) || "#5f9a4c");
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    }
  }
  pos.needsUpdate = true;
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.97, metalness: 0.0 });
  const m = new THREE.Mesh(geo, mat);
  m.position.y = e.y || 0;
  // a baked terrain texture (grass/paths/stone with real detail) beats interpolated vertex colors —
  // load it async and switch the material over to it when it arrives; vertex colors are the fallback.
  if (e.texture) {
    const url = assetBase ? `${assetBase}/${e.texture}` : e.texture;
    new THREE.TextureLoader().load(url, (tex) => {
      if ("colorSpace" in tex) tex.colorSpace = THREE.SRGBColorSpace;
      mat.map = tex; mat.vertexColors = false; mat.color.set("#ffffff"); mat.needsUpdate = true;
    });
  }
  return m;
}

function buildMesh(e, meshes, assetBase) {
  if (e.mesh && meshes[e.mesh] && e.shape !== "ground") return skinnedMesh(meshes[e.mesh], e);
  if (e.shape === "heightfield") return buildHeightfield(e, assetBase);
  const color = e.color || "#cccccc";
  const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.8, metalness: 0.05 });
  let geo;
  if (e.shape === "sphere") geo = new THREE.SphereGeometry(e.r || 1, 20, 16);
  else if (e.shape === "ground") {
    geo = new THREE.PlaneGeometry(e.size || 100, e.size || 100);
    const m = new THREE.Mesh(geo, mat); m.rotation.x = -Math.PI / 2; m.position.y = e.y || 0;
    return m;
  } else geo = new THREE.BoxGeometry(e.w || 1, e.h || 1, e.d || 1);
  return new THREE.Mesh(geo, mat);
}

export async function run3d(game, canvas, assetBase) {
  const { g, config, kit } = realize(game, { width: 1280, height: 720, background: "#101018" });
  const aspect = config.width / config.height;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(config.background);
  const camera = new THREE.PerspectiveCamera(60, aspect, 0.1, 5000);

  // Fill the window (letterboxed to the config aspect); 3D renders at the display size so it stays
  // crisp — unlike 2D there is no fixed backing to preserve.
  const fit = () => {
    let w = innerWidth, h = w / aspect;
    if (h > innerHeight) { h = innerHeight; w = h * aspect; }
    renderer.setSize(w, h);
  };
  addEventListener("resize", fit);
  fit();

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const sun = new THREE.DirectionalLight(0xffffff, 1.0);
  sun.position.set(50, 120, 60); scene.add(sun);

  const input = makeInput();
  const cam = { x: 0, y: 30, z: 60, tx: 0, ty: 0, tz: 0 };

  const keymap = (e) => e.key.length === 1 ? e.key.toLowerCase() : e.key;
  addEventListener("keydown", (e) => { input._set(keymap(e), true); if (e.key.startsWith("Arrow") || e.key === " ") e.preventDefault(); });
  addEventListener("keyup", (e) => input._set(keymap(e), false));

  // HUD overlay: a 2D canvas over the WebGL canvas. The game's draw(g, kit) paints screen-space UI
  // (health, gold, menus, crosshair) onto it — this is how a 3D game gets a HUD.
  const hud = document.createElement("canvas");
  hud.width = config.width; hud.height = config.height;
  hud.style.cssText = "position:fixed;pointer-events:none";
  document.body.appendChild(hud);
  const hctx = hud.getContext("2d");
  const hudDraw = makeDraw(hctx);
  const placeHud = () => {
    const r = canvas.getBoundingClientRect();
    hud.style.left = `${r.left}px`; hud.style.top = `${r.top}px`;
    hud.style.width = `${r.width}px`; hud.style.height = `${r.height}px`;
  };

  // Camera control. First-person (config.pointerLock OR controls:"fp"): click locks the pointer,
  // mouse-look feeds input.lookDX/DY, clicks become pointer.down. Otherwise: drag to orbit the view
  // around the game's look-at target (a free capability layered on the game's own camera() hook).
  const firstPerson = config.pointerLock || config.controls === "fp";
  let viewYaw = 0, viewPitch = 0, dragging = false, lastX = 0, lastY = 0, lookDX = 0, lookDY = 0;
  if (firstPerson) {
    canvas.addEventListener("click", () => { if (document.pointerLockElement !== canvas) canvas.requestPointerLock(); });
    addEventListener("mousemove", (e) => { if (document.pointerLockElement === canvas) { lookDX += e.movementX; lookDY += e.movementY; } });
    addEventListener("mousedown", () => { if (document.pointerLockElement === canvas) input.pointer.down = true; });
    addEventListener("mouseup", () => { input.pointer.down = false; });
  } else {
    canvas.addEventListener("mousedown", (e) => { dragging = true; lastX = e.clientX; lastY = e.clientY; });
    addEventListener("mouseup", () => { dragging = false; });
    addEventListener("mousemove", (e) => {
      if (!dragging) return;
      viewYaw -= (e.clientX - lastX) * 0.008;
      viewPitch = Math.max(-1.0, Math.min(1.0, viewPitch + (e.clientY - lastY) * 0.006));
      lastX = e.clientX; lastY = e.clientY;
    });
  }

  const assets = await loadMeshes(assetBase);   // {id: GLB scene}; {} when unskinned → primitives
  if (g.init) g.init(kit);

  const nodes = new Map();   // entity -> THREE.Object3D
  function sync() {
    const live = new Set();
    for (const e of worldOf(g)) {
      if (!e.shape) continue;
      live.add(e);
      let m = nodes.get(e);
      if (!m) { m = buildMesh(e, assets, assetBase); scene.add(m); nodes.set(e, m); }
      if (e.shape !== "ground") {
        m.position.set(e.x || 0, e.y || 0, e.z || 0);
        if (e.ry != null) m.rotation.y = e.ry;
      }
    }
    for (const [e, m] of nodes) if (!live.has(e)) { scene.remove(m); nodes.delete(e); }
  }

  const banner = document.createElement("div");
  banner.style.cssText = "position:fixed;inset:0;display:none;place-items:center;color:#fff;"
    + "font:bold 32px monospace;background:rgba(0,0,0,.5);pointer-events:none";
  document.body.appendChild(banner);

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    input.lookDX = lookDX; input.lookDY = lookDY; lookDX = 0; lookDY = 0;   // hand this frame's mouse-look to the game
    if (!kit.over) {
      g.update(dt, input, kit);
      if (g.camera) g.camera(cam, kit);                                    // explicit hook wins
      else if (config.controls) schemeCamera(config.controls, cam, g.state && g.state.player);  // scheme-wired
      else if (g.state && g.state.player) chaseCam(cam, g.state.player);  // sane default follow
    }
    input._endFrame();
    sync();
    // orbit the eye around the look-at target by the user's drag (yaw + height), then look at it
    const ox = cam.x - cam.tx, oz = cam.z - cam.tz;
    const rad = Math.hypot(ox, oz) || 1;
    const ang = Math.atan2(ox, oz) + viewYaw;
    camera.position.set(cam.tx + Math.sin(ang) * rad, cam.y + viewPitch * rad, cam.tz + Math.cos(ang) * rad);
    camera.lookAt(cam.tx || 0, cam.ty || 0, cam.tz || 0);
    // Hand the camera's ground heading to next frame's update() so kit.moveRelative can steer the
    // player relative to the VIEW the player actually sees (orbit + movement stay tied).
    input.camYaw = Math.atan2(cam.tx - camera.position.x, -(cam.tz - camera.position.z));
    renderer.render(scene, camera);
    // HUD overlay: transparent 2D canvas over the scene. The game RETURNS items from hud(kit); the
    // engine draws them. The game never touches this canvas, so it can't clear/occlude the 3D scene.
    if (g.hud) { placeHud(); hctx.clearRect(0, 0, hud.width, hud.height); renderHud(hudDraw, g.hud(kit), config.width, config.height); }
    if (kit.over) { banner.textContent = kit.over.msg; banner.style.display = "grid"; }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return kit;
}
