// lib/audio.js — every sound a game makes, synthesised: no audio files, no WebAudio of your own.
//
//   import { sfx, tone, noise, sound, seq, loop, music, setVolume } from './lib/audio.js';
//
//   sfx('hit');                    // ready-made: hit pickup jump select fail win shoot explode
//   tone(440, 200);                // a beep: Hz, ms; optional 'square'|'sawtooth'|'triangle'|'sine', then volume 0..1
//   noise(120);                    // a burst of noise — footstep, wind, crash, static
//   noise(120, { from: 2000, to: 400, q: 6, vol: 0.3 });   // swept band-pass: from/to Hz, q is how narrow
//
//   sound({...})                   // ANY one-shot effect, from these parts:
//     wave   'square'|'sawtooth'|'triangle'|'sine'|'noise'   (default 'square')
//     from, to   pitch in Hz at the start and the end (to defaults to from); for 'noise' these are the filter
//     ms     length (default 150)      vol  0..1 (default 0.25)      delay  seconds to wait before it sounds
//     q      filter narrowness (default 1)      attack  seconds to fade in (default 0.005)
//   sound({ wave: 'noise', from: 1600, to: 200, ms: 90, q: 4, vol: 0.25 });      // a footstep on gravel
//   sound({ wave: 'sawtooth', from: 90, to: 60, ms: 500, vol: 0.3, q: 8 });      // a heavy thud
//
//   seq([[523, 120], [659, 120], [784, 240]], { wave: 'triangle', vol: 0.25 });  // a jingle: [Hz, ms] each, played in order
//
//   const engine = loop({ wave: 'sawtooth', from: 80, vol: 0.15, q: 3 });        // a sound that HOLDS until stopped
//   engine.set({ from: 80 + speed * 40, vol: 0.2 });   // change it while it runs — engine pitch, wind, a rising alarm
//   engine.stop();
//
//   music.start(seed);             // a looping chiptune from the seed; every integer is a different tune; call once
//   music.start(seed, 140);        // second arg is tempo in BPM (default 120)
//   music.stop();  music.playing
//   setVolume(0.5);                // master volume 0..1
//
// Browsers refuse to make sound before a user gesture; this module listens for the first key,
// click or touch and unlocks itself, so call any of these from anywhere and it will sound as soon
// as it is allowed. Nothing here throws if audio is unavailable.

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

let noiseBuf = null;
function noiseBuffer(c) {
  if (!noiseBuf) {
    const n = c.sampleRate * 2;
    noiseBuf = c.createBuffer(1, n, c.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
  }
  return noiseBuf;
}

function source(c, wave, from, to, t0, dur, q) {
  // A noise source is a filter sweep, a tone source is a pitch sweep: the same `from`/`to` numbers
  // mean the frequency that moves in each case, so one spec describes both.
  if (wave === 'noise') {
    const src = c.createBufferSource();
    src.buffer = noiseBuffer(c);
    src.loop = true;
    const f = c.createBiquadFilter();
    f.type = 'bandpass';
    f.Q.value = Math.max(0.0001, q);
    f.frequency.setValueAtTime(Math.max(20, from), t0);
    f.frequency.exponentialRampToValueAtTime(Math.max(20, to), t0 + dur);
    src.connect(f);
    return { node: src, out: f };
  }
  const osc = c.createOscillator();
  osc.type = wave;
  osc.frequency.setValueAtTime(Math.max(20, from), t0);
  osc.frequency.exponentialRampToValueAtTime(Math.max(20, to), t0 + dur);
  return { node: osc, out: osc };
}

export function sound(spec = {}) {
  const c = ac();
  if (!c) return;
  const { wave = 'square', ms = 150, vol = 0.25, delay = 0, q = 1, attack = 0.005 } = spec;
  const from = spec.from ?? 440;
  const to = spec.to ?? from;
  const dur = Math.max(0.01, ms / 1000);
  const t0 = c.currentTime + delay;
  const { node, out } = source(c, wave, from, to, t0, dur, q);
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(Math.max(0.0001, vol), t0 + Math.min(attack, dur / 2));
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  out.connect(g).connect(master);
  node.start(t0);
  node.stop(t0 + dur + 0.02);
}

export function tone(freq, ms = 150, wave = 'sine', vol = 0.25) { sound({ wave, from: freq, ms, vol }); }

export function noise(ms = 120, opts = {}) {
  sound({ ...opts, wave: 'noise', from: opts.from ?? 1200, to: opts.to ?? opts.from ?? 400, ms, q: opts.q ?? 2, vol: opts.vol ?? 0.25 });
}

export function seq(notes, opts = {}) {
  let at = opts.delay || 0;
  for (const [freq, ms = 150, vol] of notes) {
    sound({ ...opts, from: freq, to: freq, ms, vol: vol ?? opts.vol ?? 0.25, delay: at });
    at += (ms + (opts.gap || 0)) / 1000;
  }
}

export function loop(spec = {}) {
  const c = ac();
  const handle = { set() {}, stop() {} };
  if (!c) return handle;
  const q = spec.q ?? 1;
  const from = spec.from ?? 220;
  const t0 = c.currentTime;
  const { node, out } = source(c, spec.wave || 'sawtooth', from, from, t0, 0.01, q);
  const g = c.createGain();
  g.gain.value = spec.vol ?? 0.15;
  out.connect(g).connect(master);
  node.start(t0);
  const freqParam = out.frequency;
  handle.set = (next = {}) => {
    const t = c.currentTime;
    if (next.from != null) freqParam.setTargetAtTime(Math.max(20, next.from), t, 0.03);
    if (next.vol != null) g.gain.setTargetAtTime(Math.max(0, next.vol), t, 0.03);
  };
  handle.stop = () => {
    const t = c.currentTime;
    g.gain.setTargetAtTime(0.0001, t, 0.02);
    try { node.stop(t + 0.2); } catch { /* already stopped */ }
  };
  return handle;
}

const SFX = {
  hit:     () => sound({ wave: 'sawtooth', from: 220, to: 60, ms: 120, vol: 0.3 }),
  pickup:  () => { sound({ from: 880, to: 1320, ms: 80, vol: 0.2 }); sound({ from: 1320, to: 1760, ms: 100, vol: 0.2, delay: 0.08 }); },
  jump:    () => sound({ from: 300, to: 700, ms: 160, vol: 0.2 }),
  select:  () => sound({ from: 660, ms: 60, vol: 0.15 }),
  fail:    () => { sound({ wave: 'sawtooth', from: 300, to: 150, ms: 250, vol: 0.25 }); sound({ wave: 'sawtooth', from: 200, to: 80, ms: 400, vol: 0.25, delay: 0.2 }); },
  win:     () => seq([[523, 180], [659, 180], [784, 180], [1047, 240]], { wave: 'triangle', vol: 0.25 }),
  shoot:   () => sound({ wave: 'sawtooth', from: 900, to: 200, ms: 90, vol: 0.2 }),
  explode: () => noise(400, { from: 800, to: 60, q: 0.7, vol: 0.4 }),
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
      sound({ from: m, ms: beat * 0.9, vol: 0.08 });
      if (i % 4 === 0) { const b = 55 * Math.pow(2, bass[(i / 4) % 4] / 12); sound({ wave: 'triangle', from: b, ms: beat * 1.8, vol: 0.12 }); }
      i++;
    }, beat);
  },
  stop() { if (timer) clearInterval(timer); timer = null; music.playing = false; },
};
