/* Unit tests for the pure condition/effect core of the web runtime.
 * Run via `node tests/web_runtime_core.test.js` (also driven by tests/test_web_runtime_js.py). */
const assert = require("assert");
const path = require("path");
const { makeState, evalCond, applyEffect, applyEffects } =
  require(path.join(__dirname, "..", "src", "web", "runtime", "engine.js"));

const IR = {
  flags: ["door_open", "betrayed"],
  variables: [{ id: "trust", default: 2 }, { id: "gold", default: 0 }],
};

function fresh() { return makeState(IR); }

// makeState seeds flags false and vars to defaults, inventory empty.
(function () {
  const s = fresh();
  assert.strictEqual(s.flags.door_open, false);
  assert.strictEqual(s.vars.trust, 2);
  assert.deepStrictEqual(s.inv, []);
})();

// leaf conditions
(function () {
  const s = fresh();
  assert.strictEqual(evalCond(s, { flag: "door_open" }), false);
  s.flags.door_open = true;
  assert.strictEqual(evalCond(s, { flag: "door_open" }), true);
  assert.strictEqual(evalCond(s, { item: "key" }), false);
  s.inv.push("key");
  assert.strictEqual(evalCond(s, { item: "key" }), true);
  assert.strictEqual(evalCond(s, { var: "trust", op: ">=", value: 2 }), true);
  assert.strictEqual(evalCond(s, { var: "trust", op: ">", value: 2 }), false);
})();

// var-vs-var comparison
(function () {
  const s = fresh();
  s.vars.gold = 5;
  assert.strictEqual(evalCond(s, { var: "gold", op: ">", value: { var: "trust" } }), true);
  assert.strictEqual(evalCond(s, { var: "trust", op: ">", value: { var: "gold" } }), false);
})();

// composites: not / all / any
(function () {
  const s = fresh();
  s.flags.door_open = true;
  assert.strictEqual(evalCond(s, { not: { flag: "door_open" } }), false);
  assert.strictEqual(evalCond(s, { all: [{ flag: "door_open" }, { var: "trust", op: "==", value: 2 }] }), true);
  assert.strictEqual(evalCond(s, { all: [{ flag: "door_open" }, { flag: "betrayed" }] }), false);
  assert.strictEqual(evalCond(s, { any: [{ flag: "betrayed" }, { flag: "door_open" }] }), true);
})();

// effects mutate state at the beat
(function () {
  const s = fresh();
  applyEffect(s, { set_flag: "door_open" });
  assert.strictEqual(s.flags.door_open, true);
  applyEffect(s, { clear_flag: "door_open" });
  assert.strictEqual(s.flags.door_open, false);
  applyEffect(s, { add_item: "key" });
  applyEffect(s, { add_item: "key" }); // idempotent
  assert.deepStrictEqual(s.inv, ["key"]);
  applyEffect(s, { remove_item: "key" });
  assert.deepStrictEqual(s.inv, []);
  applyEffects(s, [{ set_var: { var: "trust", value: 5 } }, { add_var: { var: "trust", delta: -2 } }]);
  assert.strictEqual(s.vars.trust, 3);
})();

console.log("web_runtime_core: all assertions passed");
