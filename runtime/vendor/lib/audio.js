// lib/audio.js — sound with no audio files: every effect is synthesised with WebAudio.
//
//   import { sfx, tone, music } from './lib/audio.js';
//   sfx('hit');                       // names: hit  pickup  jump  select  fail  win  shoot  explode
//   tone(440, 200);                   // a beep: frequency in Hz, length in ms; optional third arg 'square'|'sawtooth'|'triangle'|'sine'
//   tone(440, 200, 'square', 0.3);    // fourth arg is volume 0..1
//   music.start(seed);                // a looping chiptune melody generated from the seed; every integer is a different tune, so pick one for THIS game; call once
//   music.start(seed, 140);           // second arg is tempo in BPM (default 120)
//   music.stop();
//   music.playing                     // true while the loop runs
//   setVolume(0.5);                   // master volume 0..1
//
// Browsers refuse to make sound before a user gesture; this module listens for the first key,
// click or touch and unlocks itself, so call sfx() from anywhere and it will sound as soon as it
// is allowed. Nothing here throws if audio is unavailable.

let ctx = null;
let master = null;

function ac() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = 0.5;
    master.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

for (const ev of ['keydown', 'mousedown', 'touchstart', 'pointerdown']) {
  window.addEventListener(ev, () => ac(), { once: true, passive: true });
}

export function setVolume(v) { const c = ac(); if (c) master.gain.value = Math.max(0, Math.min(1, v)); }

function play(freqFrom, freqTo, ms, type = 'square', vol = 0.25, at = 0) {
  const c = ac();
  if (!c) return;
  const t0 = c.currentTime + at;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(Math.max(20, freqFrom), t0);
  osc.frequency.exponentialRampToValueAtTime(Math.max(20, freqTo), t0 + ms / 1000);
  g.gain.setValueAtTime(vol, t0);
  g.gain.exponentialRampToValueAtTime(0.001, t0 + ms / 1000);
  osc.connect(g).connect(master);
  osc.start(t0);
  osc.stop(t0 + ms / 1000 + 0.02);
}

export function tone(freq, ms = 150, type = 'sine', vol = 0.25) { play(freq, freq, ms, type, vol); }

const SFX = {
  hit:     () => play(220, 60, 120, 'sawtooth', 0.3),
  pickup:  () => { play(880, 1320, 80, 'square', 0.2); play(1320, 1760, 100, 'square', 0.2, 0.08); },
  jump:    () => play(300, 700, 160, 'square', 0.2),
  select:  () => play(660, 660, 60, 'square', 0.15),
  fail:    () => { play(300, 150, 250, 'sawtooth', 0.25); play(200, 80, 400, 'sawtooth', 0.25, 0.2); },
  win:     () => [523, 659, 784, 1047].forEach((f, i) => play(f, f, 180, 'triangle', 0.25, i * 0.12)),
  shoot:   () => play(900, 200, 90, 'sawtooth', 0.2),
  explode: () => {
    const c = ac(); if (!c) return;
    const n = c.sampleRate * 0.4, buf = c.createBuffer(1, n, c.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const src = c.createBufferSource(); src.buffer = buf;
    const g = c.createGain(); g.gain.value = 0.4;
    src.connect(g).connect(master); src.start();
  },
};

export function sfx(name) { (SFX[name] || SFX.select)(); }

const SCALE = [0, 2, 4, 7, 9, 12, 14, 16];
let timer = null;

export const music = {
  playing: false,
  start(seed = 1, bpm = 120) {
    music.stop();
    let s = (seed | 0) || 1;
    const rnd = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    const melody = Array.from({ length: 16 }, () => SCALE[Math.floor(rnd() * SCALE.length)]);
    const bass = Array.from({ length: 4 }, () => SCALE[Math.floor(rnd() * 4)]);
    const beat = 60000 / bpm / 2;
    let i = 0;
    music.playing = true;
    timer = setInterval(() => {
      if (!ctx) return;
      const m = 220 * Math.pow(2, melody[i % 16] / 12);
      play(m, m, beat * 0.9, 'square', 0.08);
      if (i % 4 === 0) { const b = 55 * Math.pow(2, bass[(i / 4) % 4] / 12); play(b, b, beat * 1.8, 'triangle', 0.12); }
      i++;
    }, beat);
  },
  stop() { if (timer) clearInterval(timer); timer = null; music.playing = false; },
};
