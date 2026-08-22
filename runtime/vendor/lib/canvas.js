// lib/canvas.js — a 2D canvas that fills the window, resizes with it, and scales a fixed-size
// world to the screen so the game looks the same in every window.
//
//   import { createCanvas } from './lib/canvas.js';
//   const view = createCanvas({ width: 960, height: 540 });   // LOGICAL size: draw as if the screen were 960x540
//   const { canvas, ctx } = view;                              // the <canvas> (already appended to body) and its 2D context
//
//   view.clear('#101018');          // fill the whole screen; call at the start of every frame
//   view.begin();                   // after clear(): applies the scale (and camera, if any) — draw the world in logical units after this
//   view.end();                     // restores the transform; draw the HUD after this in plain logical units via view.hud()
//   view.hud();                     // transform for screen-fixed UI (0..width, 0..height), call before drawing it
//   view.width, view.height         // logical size
//   view.follow(x, y, lerp=0.1)     // camera: keep world point (x, y) centred, smoothed; call once per frame
//   view.clamp(minX, minY, maxX, maxY)   // keep the camera inside a world rectangle (call once after follow)
//   view.cam.x, view.cam.y          // camera centre in world units
//   view.toWorld(sx, sy)            // a screen/mouse position (CSS px) -> world coords {x, y}
//   view.toScreen(wx, wy)           // world -> screen CSS px
//   view.onResize(fn)               // fn(width, height) after the window changes
//
// The world is letterboxed: the logical rectangle always fits whole on screen, scaled by the same
// factor on both axes, centred, with bars in the clear colour. Pixel art stays sharp (imageSmoothing off
// when {pixelArt: true}).

export function createCanvas({ width = 960, height = 540, pixelArt = false, parent = document.body } = {}) {
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;display:block;touch-action:none;';
  parent.appendChild(canvas);
  document.body.style.margin = '0';
  document.body.style.overflow = 'hidden';
  const ctx = canvas.getContext('2d');
  const view = { canvas, ctx, width, height, scale: 1, offX: 0, offY: 0, dpr: 1,
                 cam: { x: width / 2, y: height / 2 }, _listeners: [] };

  function resize() {
    view.dpr = window.devicePixelRatio || 1;
    const w = window.innerWidth, h = window.innerHeight;
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

  view.toWorld = (sx, sy) => ({
    x: (sx - view.offX) / view.scale - width / 2 + view.cam.x,
    y: (sy - view.offY) / view.scale - height / 2 + view.cam.y,
  });

  view.toScreen = (wx, wy) => ({
    x: (wx - view.cam.x + width / 2) * view.scale + view.offX,
    y: (wy - view.cam.y + height / 2) * view.scale + view.offY,
  });

  return view;
}
