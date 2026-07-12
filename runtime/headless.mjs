// headless.mjs — the local gradient. Step a game's SIM in pure Node (no browser,
// no canvas, zero deps), catch crashes/divergence, print a JSON verdict.
//
//   node headless.mjs games/pong.js [frames]
//
// Exit 0 + {"ok":true}  => the sim ran/resolved without crashing.
// Exit 1 + {"ok":false} => a crash/divergence the codegen loop feeds back as the fix.
// An infinite loop is caught by the CALLER's process timeout, not here.

import { simulate } from "./engine.js";

const [, , gamePath, framesArg] = process.argv;
if (!gamePath) { console.error("usage: node headless.mjs <game.js> [frames]"); process.exit(2); }

const frames = Number(framesArg) || 600;
try {
  const mod = await import(new URL(gamePath, import.meta.url));
  const game = mod.createGame || mod.default;
  if (!game) throw new Error("game module must export createGame (or default)");
  const result = simulate(game, { frames });
  console.log(JSON.stringify(result));
  process.exit(result.ok ? 0 : 1);
} catch (e) {
  console.log(JSON.stringify({ ok: false, phase: "load", error: String(e && e.stack || e) }));
  process.exit(1);
}
