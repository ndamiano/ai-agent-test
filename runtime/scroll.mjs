// scroll.mjs — camera/scroll gate. Drives the player across the level and checks that a world
// bigger than the screen is followed by a panning camera (else most of it is off-screen).
//   node scroll.mjs games/platformer.js
import { scrollSmoke } from "./engine.js";

const [, , gamePath] = process.argv;
try {
  const mod = await import(new URL(gamePath, import.meta.url));
  const result = scrollSmoke(mod.createGame || mod.default, {});
  console.log(JSON.stringify(result, null, 1));
  process.exit(result.ok ? 0 : 1);
} catch (e) {
  console.log(JSON.stringify({ ok: false, violations: [{ kind: "load", detail: String(e && e.stack || e) }] }));
  process.exit(1);
}
