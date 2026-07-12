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
import { makeKit, makeInput, makeRng } from "./engine.js";

function worldOf(g) {
  return (g.state && (Array.isArray(g.state.world) ? g.state.world
    : Array.isArray(g.state.entities) ? g.state.entities : [])) || [];
}

function buildMesh(e) {
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

export function run3d(game, canvas) {
  const g = typeof game === "function" ? game(null) : game;
  const config = { width: 800, height: 600, background: "#101018", ...(g.config || {}) };
  canvas.width = config.width; canvas.height = config.height;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setSize(config.width, config.height, false);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(config.background);
  const camera = new THREE.PerspectiveCamera(60, config.width / config.height, 0.1, 5000);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const sun = new THREE.DirectionalLight(0xffffff, 1.0);
  sun.position.set(50, 120, 60); scene.add(sun);

  const input = makeInput();
  const rng = makeRng(config.seed || 1);
  const kit = makeKit(config, rng);
  const cam = { x: 0, y: 30, z: 60, tx: 0, ty: 0, tz: 0 };

  const keymap = (e) => e.key.length === 1 ? e.key.toLowerCase() : e.key;
  addEventListener("keydown", (e) => { input._set(keymap(e), true); if (e.key.startsWith("Arrow") || e.key === " ") e.preventDefault(); });
  addEventListener("keyup", (e) => input._set(keymap(e), false));

  if (g.init) g.init(kit);

  const meshes = new Map();   // entity -> THREE.Mesh
  function sync() {
    const live = new Set();
    for (const e of worldOf(g)) {
      if (!e.shape) continue;
      live.add(e);
      let m = meshes.get(e);
      if (!m) { m = buildMesh(e); scene.add(m); meshes.set(e, m); }
      if (e.shape !== "ground") {
        m.position.set(e.x || 0, e.y || 0, e.z || 0);
        if (e.ry != null) m.rotation.y = e.ry;
      }
    }
    for (const [e, m] of meshes) if (!live.has(e)) { scene.remove(m); meshes.delete(e); }
  }

  const banner = document.createElement("div");
  banner.style.cssText = "position:fixed;inset:0;display:none;place-items:center;color:#fff;"
    + "font:bold 32px monospace;background:rgba(0,0,0,.5);pointer-events:none";
  document.body.appendChild(banner);

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!kit.over) { g.update(dt, input, kit); if (g.camera) g.camera(cam, kit); }
    input._endFrame();
    sync();
    camera.position.set(cam.x, cam.y, cam.z);
    camera.lookAt(cam.tx || 0, cam.ty || 0, cam.tz || 0);
    renderer.render(scene, camera);
    if (kit.over) { banner.textContent = kit.over.msg; banner.style.display = "grid"; }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return kit;
}
