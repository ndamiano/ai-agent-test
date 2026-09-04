// lib/sprites.js — plays an animated character made by generate_media(kind: "anim"): one sheet PNG
// with a JSON manifest beside it, holding every facing direction and every animation of ONE
// character. It draws onto your 2D context; the game owns position, facing and state.
//
//   import { loadAnim } from './lib/sprites.js';
//   const knight = await loadAnim('assets/knight.png');   // the path generate_media gave you; the .json is beside it
//
//   knight.draw(ctx, x, y, { dir: 'left', anim: 'walk', t: timeSeconds });     // every frame
//   knight.draw(ctx, x, y, { dir: 'front', anim: 'attack', t, scale: 2 });
//   knight.draw(ctx, x, y, { dir: 'back', anim: 'idle', t, once: true, start: tStarted });
//
//   (x, y) is where the FEET stand in your world units; the sprite is drawn up from there, centred.
//   dir:   'front' | 'right' | 'back' | 'left'  — the way the character faces; a missing one is mirrored
//          from its opposite side, so 'right' and 'left' always work.
//   anim:  'walk' | 'idle' | 'attack'  — a missing animation falls back to 'idle', then to the first row.
//   t:     seconds; the animation loops by itself at its own fps.
//   once:  play through one time from `start` and hold the last frame — for attacks and hits.
//   scale: world units per sheet pixel (default 1).
//
//   knight.duration('attack')   // seconds one pass takes — time an attack's hit to it
//   knight.cell                 // { w, h } of one frame in sheet pixels
//   knight.dirs, knight.anims   // what this character has
//
// Draw in the same transform you draw everything else in (after view.begin() from lib/canvas.js);
// nothing here touches the canvas size or the camera.

const MIRROR = { left: 'right', right: 'left' };

export async function loadAnim(pngPath) {
  const jsonPath = pngPath.replace(/\.png$/i, '.json');
  const [manifest, image] = await Promise.all([fetch(jsonPath).then((r) => r.json()), loadImage(pngPath)]);
  return makeAnim(manifest, image);
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`sprites: cannot load ${src}`));
    img.src = src;
  });
}

function makeAnim(manifest, image) {
  const { cell, dirs, anims, pivot } = manifest;
  const animNames = Object.keys(anims);

  function pick(dir, anim) {
    const name = anims[anim] ? anim : anims.idle ? 'idle' : animNames[0];
    const spec = anims[name];
    let flip = false;
    let row = spec.rows[dir];
    if (row === undefined && MIRROR[dir] !== undefined && spec.rows[MIRROR[dir]] !== undefined) {
      row = spec.rows[MIRROR[dir]];
      flip = true;
    }
    if (row === undefined) row = spec.rows[dirs[0]];
    return { spec, row, flip };
  }

  function frameAt(spec, t, once, start) {
    const elapsed = once ? Math.max(0, t - (start ?? 0)) : t;
    const f = Math.floor(elapsed * spec.fps);
    return once ? Math.min(f, spec.frames - 1) : ((f % spec.frames) + spec.frames) % spec.frames;
  }

  return {
    cell,
    dirs,
    anims: animNames,
    duration(anim) {
      const spec = anims[anim] ?? anims.idle ?? anims[animNames[0]];
      return spec.frames / spec.fps;
    },
    draw(ctx, x, y, { dir = 'front', anim = 'idle', t = 0, once = false, start = 0, scale = 1 } = {}) {
      const { spec, row, flip } = pick(dir, anim);
      const frame = frameAt(spec, t, once, start);
      const sx = frame * cell.w;
      const sy = row * cell.h;
      const w = cell.w * scale;
      const h = cell.h * scale;
      ctx.save();
      ctx.translate(x, y);
      if (flip) ctx.scale(-1, 1);
      ctx.drawImage(image, sx, sy, cell.w, cell.h, -pivot.x * scale, -pivot.y * scale, w, h);
      ctx.restore();
    },
  };
}
