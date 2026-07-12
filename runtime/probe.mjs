// probe.mjs — correctness gate. Runs the generic invariant probe (controls-live,
// no wall-clip) and prints a JSON verdict. Exit 1 on any violation.
//   node probe.mjs games/pacman.js
import { probe } from "./engine.js";

const [, , gamePath] = process.argv;
try {
  const mod = await import(new URL(gamePath, import.meta.url));
  const result = probe(mod.createGame || mod.default, {});
  console.log(JSON.stringify(result, null, 1));
  process.exit(result.ok ? 0 : 1);
} catch (e) {
  console.log(JSON.stringify({ ok: false, violations: [{ kind: "load", detail: String(e && e.stack || e) }] }));
  process.exit(1);
}
