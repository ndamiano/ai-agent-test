// lib/canvas.js — scales a fixed-size logical world onto YOUR <canvas>, whatever size the page
// gives it, so the game looks the same in every window. It never creates elements: the canvas, the
// page layout and any HTML overlays (title screen, HUD, dialogue) stay yours. A canvas your CSS
// leaves unsized fills the window; size it in CSS or inline to make it smaller.
//
//   <canvas id="game"></canvas>                       in index.html; fills the window unless your CSS sizes it
//   import { createCanvas } from './lib/canvas.js';
//   const view = createCanvas(document.getElementById('game'), { width: 960, height: 540 });  // LOGICAL size: draw as if 960x540
//   const { canvas, ctx } = view;
//
//   view.clear('#101018');          // fill the whole canvas; call at the start of every frame
//   view.begin();                   // after clear(): applies the scale (and camera, if any) — draw the world in logical units after this
//   view.end();                     // restores the transform; draw canvas HUD after this in plain logical units via view.hud()
//   view.hud();                     // transform for screen-fixed drawing (0..width, 0..height), call before drawing it
//   view.width, view.height         // logical size
//   view.follow(x, y, lerp=0.1)     // camera: keep world point (x, y) centred, smoothed; call once per frame
//   view.clamp(minX, minY, maxX, maxY)   // keep the camera inside a world rectangle (call once after follow)
//   view.cam.x, view.cam.y          // camera centre in world units
//   view.toWorld(cx, cy)            // a position in CSS px relative to the canvas (mouse.x/mouse.y from lib/input.js) -> world {x, y}
//   view.toScreen(wx, wy)           // world -> CSS px relative to the canvas
//   view.onResize(fn)               // fn(width, height) after the canvas changes size
//
// The world is letterboxed: the logical rectangle always fits whole in the canvas, scaled by the same
// factor on both axes, centred, with bars in the clear colour. Pixel art stays sharp (imageSmoothing off
// when {pixelArt: true}).

export function createCanvas(canvas, { width = 960, height = 540, pixelArt = false } = {}) {
  const ctx = canvas.getContext('2d');
  const view = { canvas, ctx, width, height, scale: 1, offX: 0, offY: 0, dpr: 1,
                 cam: { x: width / 2, y: height / 2 }, _listeners: [] };

  if (!canvas.style.width && !canvas.style.height && canvas.clientWidth === 300 && canvas.clientHeight === 150) {
    canvas.style.width = '100vw'; canvas.style.height = '100vh';
  }

  function resize() {
    view.dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    canvas.width = Math.round(w * view.dpr);
    canvas.height = Math.round(h * view.dpr);
    view.scale = Math.min(w / width, h / height);
    view.offX = (w - width * view.scale) / 2;
    view.offY = (h - height * view.scale) / 2;
    ctx.imageSmoothingEnabled = !pixelArt;
    for (const fn of view._listeners) fn(width, height);
  }
  window.addEventListener('resize', resize);
  resize();

  view.onResize = fn => view._listeners.push(fn);

  view.clear = (colour = '#000') => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = colour;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  };

  view.hud = () => {
    ctx.setTransform(view.dpr * view.scale, 0, 0, view.dpr * view.scale, view.offX * view.dpr, view.offY * view.dpr);
    ctx.imageSmoothingEnabled = !pixelArt;
  };

  view.begin = () => {
    view.hud();
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, width, height);
    ctx.clip();
    ctx.translate(Math.round(width / 2 - view.cam.x), Math.round(height / 2 - view.cam.y));
  };

  view.end = () => { ctx.restore(); };

  view.follow = (x, y, lerp = 0.1) => {
    view.cam.x += (x - view.cam.x) * lerp;
    view.cam.y += (y - view.cam.y) * lerp;
  };

  view.clamp = (minX, minY, maxX, maxY) => {
    const hw = width / 2, hh = height / 2;
    view.cam.x = maxX - minX <= width ? (minX + maxX) / 2 : Math.max(minX + hw, Math.min(maxX - hw, view.cam.x));
    view.cam.y = maxY - minY <= height ? (minY + maxY) / 2 : Math.max(minY + hh, Math.min(maxY - hh, view.cam.y));
  };

  view.toWorld = (cx, cy) => ({
    x: (cx - view.offX) / view.scale - width / 2 + view.cam.x,
    y: (cy - view.offY) / view.scale - height / 2 + view.cam.y,
  });

  view.toScreen = (wx, wy) => ({
    x: (wx - view.cam.x + width / 2) * view.scale + view.offX,
    y: (wy - view.cam.y + height / 2) * view.scale + view.offY,
  });

  return view;
}
