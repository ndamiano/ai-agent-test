"""The depth + world primitives: talk/quest/notify, 3D steering, the marker HUD kind, and the
probe's dead-mouse-control check. Node runs the real kit; no LLM involved."""

import json
import subprocess

from maestro.codegen.gates import RUNTIME_DIR


def _node_eval(js: str) -> dict:
    p = subprocess.run(["node", "--input-type=module", "-e", js],
                       cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_talk_loop_advances_picks_and_closes():
    r = _node_eval("""
import { makeKit, makeRng, makeInput } from "./engine.js";
const kit = makeKit({}, makeRng(1)), input = makeInput(), state = {};
const npc = { name: "Elder", lines: ["Hi.", "Beast bad."], options: ["Accept", "Leave"] };
kit.talkOpen(state, npc);
const hudMid = kit.talkHud(state);
input._set("e", true);
kit.talkStep(state, input);                       // advance to the last line
input._endFrame(); input._set("e", false);
const hudLast = kit.talkHud(state);
input._set("1", true);
const pick = kit.talkStep(state, input);          // choose option 1
console.log(JSON.stringify({
  midMenu: hudMid.some(i => i.kind === "menu"),
  lastMenu: hudLast.some(i => i.kind === "menu"),
  pick: pick && pick.pick, closed: state.talk === null,
  emptyAfter: kit.talkHud(state).length === 0,
}));
""")
    assert r == {"midMenu": False, "lastMenu": True, "pick": 0, "closed": True, "emptyAfter": True}


def test_talk_without_options_closes_on_advance():
    r = _node_eval("""
import { makeKit, makeRng, makeInput } from "./engine.js";
const kit = makeKit({}, makeRng(1)), input = makeInput(), state = {};
kit.talkOpen(state, { name: "Guard", lines: ["Move along."] });
input._set("e", true);
kit.talkStep(state, input);
console.log(JSON.stringify({ closed: state.talk === null }));
""")
    assert r["closed"] is True


def test_quest_add_complete_and_toasts():
    r = _node_eval("""
import { makeKit, makeRng } from "./engine.js";
const kit = makeKit({}, makeRng(1)), state = {};
kit.quest.add(state, { id: "beast", title: "Slay the beast", reward: 50 });
const dup = kit.quest.add(state, { id: "beast", title: "dup" });
const q = kit.quest.complete(state, "beast");
const again = kit.quest.complete(state, "beast");
console.log(JSON.stringify({
  n: state.quests.length, dup: dup === null, reward: q.reward, again: again === null,
  done: kit.quest.isDone(state, "beast"), log: kit.quest.log(state).length,
  toasts: kit._toastItems().length,
}));
""")
    assert r == {"n": 1, "dup": True, "reward": 50, "again": True, "done": True, "log": 1, "toasts": 2}


def test_notify_toasts_expire():
    r = _node_eval("""
import { makeKit, makeRng } from "./engine.js";
const kit = makeKit({}, makeRng(1));
kit.notify("hello", 1);
const before = kit._toastItems().length;
kit._stepToasts(2);
console.log(JSON.stringify({ before, after: kit._toastItems().length }));
""")
    assert r == {"before": 1, "after": 0}


def test_seek3_moves_on_ground_plane_and_faces():
    r = _node_eval("""
import { makeKit, makeRng } from "./engine.js";
const kit = makeKit({}, makeRng(1));
const e = { x: 0, y: 5, z: 0 };
let d = 0;
for (let i = 0; i < 100; i++) d = kit.seek3(e, { x: 10, z: 0 }, 5, 1 / 30);
console.log(JSON.stringify({ x: Math.round(e.x), y: e.y, close: d < 0.1, faced: Number.isFinite(e.ry) }));
""")
    assert r == {"x": 10, "y": 5, "close": True, "faced": True}


def test_patrol3_loops_waypoints_and_wander3_stays_finite():
    r = _node_eval("""
import { makeKit, makeRng } from "./engine.js";
const kit = makeKit({}, makeRng(1));
const g = { x: 0, y: 0, z: 0 };
let maxWp = 0;
for (let i = 0; i < 600; i++) { kit.patrol3(g, [[0, 0], [4, 0], [4, 4]], 6, 1 / 30); maxWp = Math.max(maxWp, g._wp); }
const w = { x: 0, y: 0, z: 0 };
for (let i = 0; i < 300; i++) kit.wander3(w, 2, 1 / 30, kit.rng);
console.log(JSON.stringify({
  looped: maxWp > 0, gOk: Number.isFinite(g.x) && Number.isFinite(g.z),
  wOk: Number.isFinite(w.x) && Number.isFinite(w.z) && Number.isFinite(w.ry),
}));
""")
    assert r == {"looped": True, "gOk": True, "wOk": True}


def test_avoid_rects_pushes_walker_out():
    r = _node_eval("""
import { makeKit, makeRng } from "./engine.js";
const kit = makeKit({}, makeRng(1));
const e = { x: 0.5, z: 0.2, w: 1, d: 1 };
kit.avoidRects(e, [{ x: 0, z: 0, w: 6, d: 6 }]);
const inside = Math.abs(e.x) < 3 && Math.abs(e.z) < 3;
console.log(JSON.stringify({ inside }));
""")
    assert r["inside"] is False


def test_marker_hud_item_validates():
    r = _node_eval("""
import { validateHud } from "./engine.js";
console.log(JSON.stringify({
  ok: validateHud([{ kind: "marker", x: 3, z: 4, text: "cave" }]) === null,
  bad: validateHud([{ kind: "marker", text: "no coords" }]) !== null,
}));
""")
    assert r == {"ok": True, "bad": True}


_ORBITAL_GAME = """
const game = (kit) => ({
  config: { mode: "3d", controls: "%s" },
  state: { world: [] },
  init(k) { this.state.player = k.spawn(this.state.world, { shape: "box", x: 0, y: 1, z: 0, w: 1, h: 2, d: 1, color: "#333" }); },
  update(dt, input, k) { k.drive(this.state.player, input, dt, 9); },
});
"""










_TURN_GAME = """(kit) => ({
  state: { world: [], score: 0 },
  update(dt, input, k) { if (input.pressed(" ")) this.state.score += 1; },
})"""

_DEAD_GAME = """(kit) => ({
  state: { world: [], score: 0 },
  update(dt, input, k) {},
})"""

_RANDOM_DEAD_GAME = """(kit) => ({
  state: { world: [], roll: Math.random() },
  update(dt, input, k) {},
})"""










_ENTER_ONLY_GAME = """(kit) => ({
  state: { world: [], turn: 0 },
  update(dt, input, k) { if (input.pressed("Enter")) this.state.turn += 1; },
})"""




def _render_kinds(game_js: str) -> list:
    r = _node_eval("""
import { renderSmoke } from "./engine.js";
const game = %s;
const res = renderSmoke(game);
console.log(JSON.stringify((res.violations || []).map(v => v.kind)));
""" % game_js)
    return r




# ── premature_end: a game that resolves with no input is broken, not "dead controls" ──
_INSTANT_WIN_GAME = """(kit) => ({
  state: { world: [], kills: 0 },
  update(dt, input, k) { if (this.state.kills >= 0) k.win("done"); },
})"""

_FAST_LOSE_GAME = """(kit) => ({
  state: { world: [], hp: 5, t: 0 },
  update(dt, input, k) { this.state.hp -= 1; if (this.state.hp <= 0) k.lose("dead"); },
})"""

_LATE_LOSE_GAME = """(kit) => ({
  state: { world: [], t: 0 },
  update(dt, input, k) { this.state.t += dt; if (this.state.t > 3.5) k.lose("starved"); },
})"""










# Player kept OUTSIDE state.world: mover works, but renderer/probe can't see it — must be named
# precisely, not reported as dead movement.
_PLAYER_OUTSIDE_GAME = """(kit) => {
  const world = [];
  const player = { shape: "box", x: 0, y: 0, z: 0, w: 1, h: 1, d: 1, color: "#fff" };
  return {
    config: { mode: "3d", controls: "follow" },
    state: { world, player },
    init(k) { k.spawn(world, { shape: "ground", size: 40, color: "#333" }); },
    update(dt, input, k) { k.drive(this.state.player, input, dt, 8); },
  };
}"""




# Continuous spawning must not blind the dead_movement measurement (the net-spawn skip bug).
_SPAWNING_MOVER_GAME = """(kit) => {
  const world = [];
  let player;
  return {
    config: { mode: "3d", controls: "follow" },
    state: { world, player: null, t: 0 },
    init(k) {
      player = k.spawn(world, { shape: "box", x: 0, y: 0.5, z: 0, w: 1, h: 1, d: 1, color: "#fff" });
      this.state.player = player;
    },
    update(dt, input, k) {
      this.state.t += dt;
      if (world.length < 200) k.spawn(world, { shape: "sphere", x: 5 + world.length, y: 0.2, z: 5, r: 0.2, color: "#f00" });
      k.drive(this.state.player, input, dt, 8);
    },
  };
}"""




# ── no_ground: a 3D scene with no ground plane is a void ─────────────────────
_VOID_3D_GAME = """(kit) => {
  const world = [];
  return {
    config: { mode: "3d", controls: "fp" },
    state: { world, player: null },
    init(k) { this.state.player = k.spawn(world, { shape: "box", x: 0, y: 1, z: 0, w: 1, h: 2, d: 1, color: "#3af" }); },
    update(dt, input, k) { k.drive(this.state.player, input, dt, 6); },
  };
}"""






def test_walls_from_tilemap_builds_level():
    r = _node_eval("""
import { makeKit, makeRng } from "./engine.js";
const kit = makeKit({}, makeRng(1));
const world = [];
const level = kit.wallsFromTilemap(world, ["####", "#..#", "####"], { tile: 4, height: 3 });
const grounds = world.filter(e => e.shape === "ground").length;
const boxes = world.filter(e => e.shape === "box").length;
const c = level.at(1, 1);
console.log(JSON.stringify({ grounds, boxes, rects: level.rects.length, cx: c.x, cz: c.z, w: level.w, h: level.h }));
""")
    assert r["grounds"] == 1 and r["boxes"] == 10 and r["rects"] == 10
    assert r["w"] == 4 and r["h"] == 3
    # corner-origin like cellCenter: tile (1,1) center = ((1+.5)*4, (1+.5)*4)
    assert r["cx"] == 6.0 and r["cz"] == 6.0
