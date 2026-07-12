// attribute.mjs — pin a module-load failure to the file(s) that actually cause it.
// A multi-file game that fails to load throws a node esm-loader stack that names NO game file
// (e.g. "Identifier 'isWall' has already been declared"). Import each file on its own to find
// which ones throw and their real one-line error, so a fix can target the right file.
//   node attribute.mjs <game_dir>  ->  {"player.js":"SyntaxError: ...", "main.js":null, ...}
import { readdirSync } from "node:fs";
import { pathToFileURL } from "node:url";

const dir = process.argv[2];
const out = {};
for (const f of readdirSync(dir).filter((f) => f.endsWith(".js"))) {
  try {
    await import(pathToFileURL(`${dir}/${f}`).href);
    out[f] = null;
  } catch (e) {
    out[f] = String((e && e.message) || e).split("\n")[0];
  }
}
console.log(JSON.stringify(out));
