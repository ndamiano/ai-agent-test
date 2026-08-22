// lib/input.js — keyboard, mouse and touch state for any browser game (canvas or three.js).
//
//   import { keys, justPressed, mouse, touch, bindInput } from './lib/input.js';
//   bindInput(canvas);            // once; pass the canvas (or window) you want mouse coords relative to
//
//   keys.up / keys.down / keys.left / keys.right   true while held — W/A/S/D AND the arrow keys both set these
//   keys.jump (Space)  keys.action (E or Enter)  keys.shift  keys.escape
//   keys['KeyQ'], keys['Digit1'] ...            any key, by its KeyboardEvent.code
//   justPressed('up') / justPressed('KeyQ')       true on the ONE frame the key went down; call it inside your update loop
//   mouse.x, mouse.y  (CSS pixels inside the element)  mouse.down  mouse.right  mouse.justClicked (one frame)
//   mouse.dx, mouse.dy   movement since last frame (pointer-locked or not)
//   touch.active  touch.x  touch.y  touch.stick {x, y} in -1..1 (a thumb-stick from the drag start point)
//   tick()  call at the END of every frame to clear the just-pressed flags and mouse deltas
//
// Everything is read from plain objects, nothing polls; the event handlers preventDefault on arrows
// and Space so the page never scrolls.

export const keys = { up: false, down: false, left: false, right: false, jump: false, action: false,
                      shift: false, escape: false };
export const mouse = { x: 0, y: 0, dx: 0, dy: 0, down: false, right: false, justClicked: false };
export const touch = { active: false, x: 0, y: 0, stick: { x: 0, y: 0 } };

const ALIAS = {
  KeyW: 'up', ArrowUp: 'up', KeyS: 'down', ArrowDown: 'down',
  KeyA: 'left', ArrowLeft: 'left', KeyD: 'right', ArrowRight: 'right',
  Space: 'jump', KeyE: 'action', Enter: 'action',
  ShiftLeft: 'shift', ShiftRight: 'shift', Escape: 'escape',
};
const PREVENT = new Set(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Space']);
const pressed = new Set();
let bound = false;
let target = null;

function setKey(code, on) {
  keys[code] = on;
  const alias = ALIAS[code];
  if (alias) keys[alias] = on;
  if (on) { pressed.add(code); if (alias) pressed.add(alias); }
}

export function justPressed(name) { return pressed.has(name); }

export function tick() {
  pressed.clear();
  mouse.justClicked = false;
  mouse.dx = 0; mouse.dy = 0;
}

function local(e) {
  const r = target && target.getBoundingClientRect ? target.getBoundingClientRect() : { left: 0, top: 0 };
  return [e.clientX - r.left, e.clientY - r.top];
}

export function bindInput(el = window) {
  if (bound) return;
  bound = true;
  target = el === window ? null : el;
  window.addEventListener('keydown', e => {
    if (PREVENT.has(e.code)) e.preventDefault();
    if (!e.repeat) setKey(e.code, true);
  });
  window.addEventListener('keyup', e => setKey(e.code, false));
  window.addEventListener('blur', () => { for (const k in keys) keys[k] = false; pressed.clear(); });
  const area = el;
  area.addEventListener('mousemove', e => {
    const [x, y] = local(e); mouse.dx += e.movementX || 0; mouse.dy += e.movementY || 0; mouse.x = x; mouse.y = y;
  });
  area.addEventListener('mousedown', e => {
    const [x, y] = local(e); mouse.x = x; mouse.y = y;
    if (e.button === 2) mouse.right = true; else { mouse.down = true; mouse.justClicked = true; }
  });
  window.addEventListener('mouseup', e => { if (e.button === 2) mouse.right = false; else mouse.down = false; });
  area.addEventListener('contextmenu', e => e.preventDefault());
  let startX = 0, startY = 0;
  area.addEventListener('touchstart', e => {
    const t = e.changedTouches[0]; const [x, y] = local(t);
    touch.active = true; touch.x = x; touch.y = y; startX = x; startY = y;
    mouse.x = x; mouse.y = y; mouse.down = true; mouse.justClicked = true;
    e.preventDefault();
  }, { passive: false });
  area.addEventListener('touchmove', e => {
    const t = e.changedTouches[0]; const [x, y] = local(t);
    touch.x = x; touch.y = y; mouse.x = x; mouse.y = y;
    touch.stick.x = Math.max(-1, Math.min(1, (x - startX) / 60));
    touch.stick.y = Math.max(-1, Math.min(1, (y - startY) / 60));
    keys.left = touch.stick.x < -0.3; keys.right = touch.stick.x > 0.3;
    keys.up = touch.stick.y < -0.3; keys.down = touch.stick.y > 0.3;
    e.preventDefault();
  }, { passive: false });
  const end = () => {
    touch.active = false; touch.stick.x = 0; touch.stick.y = 0; mouse.down = false;
    keys.left = keys.right = keys.up = keys.down = false;
  };
  area.addEventListener('touchend', end);
  area.addEventListener('touchcancel', end);
}
