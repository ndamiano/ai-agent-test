// render.mjs — render smoke gate: the screen-space path headless can't see. Runs the engine's
// scene render over state.world plus hud(). Prints a JSON verdict, exit 1 on any violation.
//   node render.mjs games/pong.js
import { renderSmoke } from "./engine.js";

const [, , gamePath] = process.argv;
try {
  const mod = await import(new URL(gamePath, import.meta.url));
  const result = renderSmoke(mod.createGame || mod.default, {});
  console.log(JSON.stringify(result, null, 1));
  process.exit(result.ok ? 0 : 1);
} catch (e) {
  console.log(JSON.stringify({ ok: false, violations: [{ kind: "load", detail: String(e && e.stack || e) }] }));
  process.exit(1);
}
