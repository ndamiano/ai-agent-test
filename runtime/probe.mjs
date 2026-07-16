// probe.mjs — correctness gate. Runs the generic invariant probe (controls-live,
// no wall-clip, no mouse-gated action outside "fp") and prints a JSON verdict. Exit 1 on any violation.
//   node probe.mjs games/pacman.js
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { probe } from "./engine.js";

const [, , gamePath] = process.argv;
try {
  const url = new URL(gamePath, import.meta.url);
  const mod = await import(url);
  let src = "";
  try { src = readFileSync(fileURLToPath(url), "utf8"); } catch { /* probe still runs without it */ }
  const result = probe(mod.createGame || mod.default, { src });
  console.log(JSON.stringify(result, null, 1));
  process.exit(result.ok ? 0 : 1);
} catch (e) {
  console.log(JSON.stringify({ ok: false, violations: [{ kind: "load", detail: String(e && e.stack || e) }] }));
  process.exit(1);
}
