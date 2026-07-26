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
import { makeKit, makeInput } from "./engine.js";
const kit = makeKit({}), input = makeInput(), state = {};
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
import { makeKit, makeInput } from "./engine.js";
const kit = makeKit({}), input = makeInput(), state = {};
kit.talkOpen(state, { name: "Guard", lines: ["Move along."] });
input._set("e", true);
kit.talkStep(state, input);
console.log(JSON.stringify({ closed: state.talk === null }));
""")
    assert r["closed"] is True


def test_quest_add_complete_and_toasts():
    r = _node_eval("""
import { makeKit } from "./engine.js";
const kit = makeKit({}), state = {};
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
import { makeKit } from "./engine.js";
const kit = makeKit({});
kit.notify("hello", 1);
const before = kit._toastItems().length;
kit._stepToasts(2);
console.log(JSON.stringify({ before, after: kit._toastItems().length }));
""")
    assert r == {"before": 1, "after": 0}


def test_seek3_moves_on_ground_plane_and_faces():
    r = _node_eval("""
import { makeKit } from "./engine.js";
const kit = makeKit({});
const e = { x: 0, y: 5, z: 0 };
let d = 0;
for (let i = 0; i < 100; i++) d = kit.seek3(e, { x: 10, z: 0 }, 5, 1 / 30);
console.log(JSON.stringify({ x: Math.round(e.x), y: e.y, close: d < 0.1, faced: Number.isFinite(e.ry) }));
""")
    assert r == {"x": 10, "y": 5, "close": True, "faced": True}


def test_patrol3_loops_waypoints_and_wander3_stays_finite():
    r = _node_eval("""
import { makeKit } from "./engine.js";
const kit = makeKit({});
const g = { x: 0, y: 0, z: 0 };
let maxWp = 0;
for (let i = 0; i < 600; i++) { kit.patrol3(g, [[0, 0], [4, 0], [4, 4]], 6, 1 / 30); maxWp = Math.max(maxWp, g._wp); }
const w = { x: 0, y: 0, z: 0 };
for (let i = 0; i < 300; i++) kit.wander3(w, 2, 1 / 30);
console.log(JSON.stringify({
  looped: maxWp > 0, gOk: Number.isFinite(g.x) && Number.isFinite(g.z),
  wOk: Number.isFinite(w.x) && Number.isFinite(w.z) && Number.isFinite(w.ry),
}));
""")
    assert r == {"looped": True, "gOk": True, "wOk": True}


def test_avoid_rects_pushes_walker_out():
    r = _node_eval("""
import { makeKit } from "./engine.js";
const kit = makeKit({});
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
import { makeKit } from "./engine.js";
const kit = makeKit({});
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


def test_draw_sprite_resolves_an_asset_id_through_the_kit():
    """The engine-internal sprite call takes an id: drawEntity and the HUD both pass one, and a real
    ctx.drawImage THROWS on a string rather than drawing nothing."""
    r = _node_eval("""
import { makeKit, makeDraw } from "./engine.js";
const drawn = [];
const ctx = { canvas: { width: 64, height: 64 }, fillRect() {}, save() {}, restore() {}, translate() {},
              drawImage: (img, x, y, w, h) => {
                if (typeof img !== "object") throw new TypeError("drawImage: not a CanvasImageSource");
                drawn.push([img.tag, x, y, w, h]);
              } };
const kit = makeKit({});
const img = { tag: "hero", width: 16, height: 16 };
kit._setSprites({ hero: img });
const g = makeDraw(ctx, kit);
g.sprite("hero", 1, 2, 16, 16);        // by id -> resolved
g.sprite(img, 3, 4, 16, 16);           // by image -> passed through
g.sprite("missing", 5, 6, 16, 16);     // unknown id -> no-op, no throw
g.sprite(undefined, 7, 8, 16, 16);     // no id at all -> no-op, no throw
console.log(JSON.stringify({ drawn }));
""")
    assert r["drawn"] == [["hero", 1, 2, 16, 16], ["hero", 3, 4, 16, 16]]


def test_hud_icon_draws_screen_art_by_id():
    """Screen art is a HUD item, not a draw call: the game names an asset id and the engine places it."""
    r = _node_eval("""
import { makeKit, makeDraw, renderHud, validateHud } from "./engine.js";
const drawn = [];
const ctx = { canvas: { width: 640, height: 480 }, fillRect() {}, fillText() {}, save() {}, restore() {},
              translate() {}, beginPath() {}, arc() {}, fill() {}, moveTo() {}, lineTo() {}, stroke() {},
              drawImage: (img, x, y, w, h) => drawn.push([img.tag, w, h]) };
const kit = makeKit({});
kit._setSprites({ potion: { tag: "potion", width: 32, height: 32 } });
const g = makeDraw(ctx, kit);
const items = [{ kind: "icon", id: "potion", at: "top-right", size: 20 },
               { kind: "icon", id: "no_art_yet" }];
const bad = validateHud(items);
renderHud(g, items, 640, 480);
console.log(JSON.stringify({ bad, drawn, rejects: validateHud([{ kind: "icon" }]) }));
""")
    assert r["bad"] is None
    assert r["drawn"] == [["potion", 20, 20]]      # missing art draws nothing, no crash
    assert "id" in r["rejects"]


def test_spawn_data_types_the_entity_by_its_row_id():
    """Gameplay branches on e.type. A row-spawned entity with no type is invisible to every one of
    them — measured: a game's own bullets flew through the boss because their type was undefined."""
    r = _node_eval("""
import { makeKit } from "./engine.js";
const kit = makeKit({});
const world = [];
const row = { id: "player_bullet", size: { w: 8, h: 8 }, color: "#ff0" };
const shot = kit.spawnData(world, row, { x: 10, y: 20 });
const named = kit.spawnData(world, row, { x: 0, y: 0, type: "friendly" });
console.log(JSON.stringify({ type: shot.type, sprite: shot.sprite, override: named.type,
                            matches: world.filter(e => e.type === "player_bullet").length }));
""")
    assert r == {"type": "player_bullet", "sprite": "player_bullet",
                 "override": "friendly", "matches": 1}


# ── focus: one activate key, whatever the player faces ────────────────────────
def test_focus_targets_what_the_player_faces_and_nothing_else():
    """The targeting every game used to hand-write per verb — and got wrong: a shipped build let
    the farmer tend a cow standing behind him because that verb's copy of the check omitted the
    facing cone."""
    r = _node_eval("""
import { focusTarget } from "./engine.js";
const p = { x: 0, y: 1, z: 0, ry: 0 };                       // ry 0 faces -z
const state = { player: p, world: [
  { x: 0, y: 1, z: -2, action: "harvest", label: "Ripe crop" },
  { x: 0, y: 1, z: 2,  action: "tend",    label: "Cow" },
  { x: 0, y: 1, z: -20, action: "talk",   label: "Distant elder" },
  { x: 0, y: 1, z: -1 },                                     // no action ⇒ never a target
]};
const ahead = focusTarget(state);
p.ry = Math.PI;                                              // turn around
const behind = focusTarget(state);
console.log(JSON.stringify({
  ahead: ahead && ahead.label,
  turned: behind && behind.label,
  outOfRange: !focusTarget({ player: p, world: [{ x: 0, y: 1, z: 20, action: "talk" }] }),
  whileTalking: focusTarget({ ...state, talk: {} }) === null,
  noPlayer: focusTarget({ world: [] }) === null,
}));
""")
    assert r == {"ahead": "Ripe crop", "turned": "Cow", "outOfRange": True,
                 "whileTalking": True, "noPlayer": True}


def test_focus_works_on_the_2d_plane_too():
    r = _node_eval("""
import { focusTarget } from "./engine.js";
const p = { x: 0, y: 0, angle: 0 };                          // 2D faces +x
const world = [{ x: 30, y: 0, action: "open", label: "Chest" },
               { x: -30, y: 0, action: "open", label: "Behind me" }];
const hit = focusTarget({ player: p, world }, { range: 50 });
console.log(JSON.stringify({ hit: hit && hit.label }));
""")
    assert r == {"hit": "Chest"}


def test_the_engine_names_the_real_activate_key_in_the_prompt():
    """The prompt is the ENGINE's, so a spec that bound activate to Space can never show "E"."""
    r = _node_eval("""
import { makeKit } from "./engine.js";
const kit = makeKit({});
kit.register("activate", [" "], () => {});
const state = { player: { x: 0, y: 1, z: 0, ry: 0 },
                world: [{ x: 0, y: 1, z: -2, action: "harvest", label: "Ripe crop" }] };
kit.focus(state);
console.log(JSON.stringify({ prompt: kit._focusItems()[0].text,
                             onState: state.focus && state.focus.label,
                             goneWhenNothingNear: (kit.focus({ player: state.player, world: [] }),
                                                   kit._focusItems().length) }));
""")
    assert r == {"prompt": "Space — Ripe crop", "onState": "Ripe crop", "goneWhenNothingNear": 0}


def test_dialogue_boxes_stack_instead_of_covering_each_other():
    """The shipped screenshot: the choice menu drawn on top of the line it was answering, with an
    `undefined` panel behind both."""
    r = _node_eval("""
import { renderHud } from "./engine.js";
const rects = [];
const draw = { rect: (x, y, w, h) => rects.push({ x, y, w, h }), text: () => {}, sprite: () => {} };
renderHud(draw, [{ kind: "panel", title: "Oren", text: "I need to finish my chores.", at: "center" },
                 { kind: "menu", options: ["Accept quest", "Anything else?"], at: "center" }], 1280, 720);
const cards = rects.filter(r => r.w > 300 && r.h > 20);      // the fill rect of each card
console.log(JSON.stringify({ cards: cards.length,
                             overlap: cards[0].y + cards[0].h > cards[1].y }));
""")
    assert r == {"cards": 2, "overlap": False}
