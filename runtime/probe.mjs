// probe.mjs — correctness gate. Runs the generic invariant probe (controls-live,
// no wall-clip, no solid interpenetration, no mouse-gated action outside "fp", plus — when the
// spec's control scheme rides as the 2nd arg — movement keys must displace the steered entity, and
// — when the spec's controls map rides as the 3rd arg (JSON {key: description}) — registered
// actions must act (dead_action) and non-movement spec keys must be registered (unbound_control)).
// Prints a JSON verdict; exit 1 on any violation.
//   node probe.mjs games/pacman.js [scheme] [controlsJson]
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { probe } from "./engine.js";

const [, , gamePath, scheme, controlsJson] = process.argv;
try {
  const url = new URL(gamePath, import.meta.url);
  const mod = await import(url);
  let src = "";
  try { src = readFileSync(fileURLToPath(url), "utf8"); } catch { /* probe still runs without it */ }
  let controlKeys = null;
  try { controlKeys = controlsJson ? JSON.parse(controlsJson) : null; } catch { /* a bad map never blocks the probe */ }
  const result = probe(mod.createGame || mod.default, { src, scheme: scheme || "", controlKeys });
  console.log(JSON.stringify(result, null, 1));
  process.exit(result.ok ? 0 : 1);
} catch (e) {
  console.log(JSON.stringify({ ok: false, violations: [{ kind: "load", detail: String(e && e.stack || e) }] }));
  process.exit(1);
}
