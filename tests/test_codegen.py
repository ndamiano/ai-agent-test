"""Codegen build path: gates, module checks, and the AgentLoop driving a game to passing.

The kit + node runners are real (the gates shell out to runtime/*.mjs); the LLM is stubbed so the
loop test is deterministic and needs no live model.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from build_harness import canned_prelude, run_build_to_completion, seed_interfaces
from maestro.codegen import interfaces
from maestro.codegen import controls as controls_mod
from maestro.codegen.fix_classes import DEFAULT, classify
from maestro.codegen.gates import (
    RUNTIME_DIR,
    dedupe_functions,
    extract_code,
    run_headless,
    run_render,
)
from maestro.codegen.module import (
    _READS_BEFORE_FORCE_ACT,
    CodegenModule,
    _authoring_order,
    _detect_typechecks,
    _fix_schemas,
    _is_contract,
    _is_stub,
    _kit_context,
    _sibling_lines,
)
from maestro.codegen.scaffold import seed_scaffold
from maestro.codegen.tools import build_codegen_tools
from maestro.modules.context import build_context
from maestro.modules.module import Error, ErrorType
from maestro.state import RunState

# Typed TS fixtures — the games are .ts now (typecheck gate). `state` is `as any` so dynamic props
# (this.state.p) are allowed without over-annotating each fixture.
GOOD = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200, seed: 1 },
    state: { world: [] as World, p: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 50, y: 50, w: 10, h: 10 }); },
    update(dt, input, kit) {
      if (input.down("d")) kit.walk(this.state.p, 1, 150); else kit.walk(this.state.p, 0, 150);
      kit.integrate(this.state.p, dt);
      this.state.p.x = kit.V.clamp(this.state.p.x, 0, 190);   // confined to one screen
    },
  };
}"""
# init throws — a runtime headless crash.
BROKEN = """export function createGame(kit: Kit): GameObject {
  return { config: {}, state: {} as any, init(kit) { throw new Error("boom"); }, update(dt, input, kit) {} };
}"""
# Passes headless, but hud() throws at RUNTIME (not a type error) — a browser crash the sim can't see.
DRAW_CRASH = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200 }, state: { world: [] as World, p: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 50, y: 50, w: 10, h: 10 }); },
    update(dt, input, kit) { if (input.down("d")) kit.walk(this.state.p, 1, 150); else kit.walk(this.state.p, 0, 150); kit.integrate(this.state.p, dt); },
    hud(kit): HudItem[] { const z: any = null; return z.nope(); },
  };
}"""
# A game that defines a draw hook: rejected outright — the engine owns the scene.
DRAW_HOOK = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200 }, state: { world: [] as World },
    init(kit) {},
    update(dt, input, kit) {},
    draw(g: any) { g.rect(0, 0, 10, 10, "#fff"); },
  } as any;
}"""
# A 3D game with no draw() — render smoke skips it (render is mesh-sync from shape tags).
GAME_3D = """export function createGame(kit: Kit): GameObject {
  return {
    config: { mode: "3d", width: 200, height: 200 }, state: { world: [] as World },
    init(kit) { this.state.world.push({ shape: "box", x: 0, y: 0, z: 0, w: 1, h: 1, d: 1, color: "#f00" }); },
    update(dt, input, kit) {},
  };
}"""
# A 3D game with a DATA HUD — hud(kit) RETURNS items, the engine draws them. Render smoke validates
# the item array and renders it against the mock (never skips a game that has a HUD).
HUD_3D = """export function createGame(kit: Kit): GameObject {
  return {
    config: { mode: "3d", width: 200, height: 200 }, state: { world: [] as World, hp: 100 },
    init(kit) {},
    update(dt, input, kit) {},
    hud(kit): HudItem[] {
      return [
        { kind: "text", text: "HP", at: "top-left" },
        { kind: "bar", value: this.state.hp, max: 100, at: "top-right", color: "#0f0" },
      ];
    },
  };
}"""
# The shipped bug's vector: a 3D game defining draw() at all. The 3D renderer ignores draw() (scene
# comes from entities; HUD from hud()), so a g.clear there silently blanks the scene. The render gate
# now rejects any draw() in a 3D game outright.
HUD_DRAW_IN_3D = """export function createGame(kit: Kit): GameObject {
  return {
    config: { mode: "3d", width: 200, height: 200 }, state: { world: [] as World },
    init(kit) {},
    update(dt, input, kit) {},
    draw(g) { g.clear("#000"); g.text("score", 10, 20, "#fff"); },
  };
}"""
# A 2D scene draw() calling the raw canvas API (g.fillRect) — NOT on DrawApi. Caught at typecheck
# because g is contextually a DrawApi via the `: GameObject` return annotation.
CANVAS_MISUSE_2D = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200 }, state: { world: [] as World },
    init(kit) {},
    update(dt, input, kit) {},
    draw(g: DrawApi) { g.rect(0, 0, 10, 10, "#fff"); },
  };
}"""
# A 3D HUD whose hud() returns a malformed item — caught by the render gate's data validation.
HUD_BAD_DATA = """export function createGame(kit: Kit): GameObject {
  return {
    config: { mode: "3d", width: 200, height: 200 }, state: { world: [] as World },
    init(kit) {},
    update(dt, input, kit) {},
    hud(kit): any[] { return [{ kind: "gauge", value: 1 }]; },
  };
}"""
# A level far wider than the screen, player driven right but NO camera — passes headless/probe/render;
# only the scroll gate catches it.
WIDE_NO_CAMERA = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 320, height: 240 }, state: { world: [] as World, p: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 0, y: 100, w: 10, h: 10 }); },
    update(dt, input, kit) { if (input.down("d") || input.down("ArrowRight")) this.state.p.x += 300 * dt; },
  };
}"""
# Same wide world, but a follow camera pans with the player.
WIDE_WITH_CAMERA = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 320, height: 240 }, state: { world: [] as World, p: null as any, cam: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 0, y: 100, w: 10, h: 10 }); this.state.cam = kit.makeCamera(); },
    update(dt, input, kit) { if (input.down("d") || input.down("ArrowRight")) this.state.p.x += 300 * dt; this.state.cam.follow(this.state.p, 4000, 240); },
  };
}"""
# A deliberate TYPE error — caught by the typecheck gate before the game ever runs.
TYPE_ERROR = """export function createGame(kit: Kit): GameObject {
  const n: number = "not a number";
  return { config: {}, state: { n } as any, update(dt, input, kit) {} };
}"""


# ── the SCAFFOLDED layout (what the pipeline actually produces: a GENERATED main.ts owning the
# control scheme, the model's game.ts behind its hooks). The gate fixtures above stay bare-entry:
# they test the RUNTIME contract (a bundle exporting createGame), which the scaffold sits on top of.
HOOKS_GOOD = """export interface GameState { world: World; player: Entity | null; score: number; }
export function createState(kit: Kit): GameState { return { world: [], player: null, score: 0 }; }
export function init(state: GameState, kit: Kit): void {
  state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff" });
}
export function update(state: GameState, dt: number, input: Input, kit: Kit): void {
  if (input.pressed(" ")) state.score += 1;
}
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
"""
# init throws — a runtime headless crash behind the hooks.
HOOKS_BROKEN = """export interface GameState { world: World; player: Entity | null; }
export function createState(kit: Kit): GameState { return { world: [], player: null }; }
export function init(state: GameState, kit: Kit): void { throw new Error("boom"); }
export function update(state: GameState, dt: number, input: Input, kit: Kit): void { }
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
"""
# typechecks + runs clean; hud() throws at RUNTIME — only the render gate sees it.
HOOKS_DRAW_CRASH = HOOKS_GOOD.replace(
    "export function hud(state: GameState, kit: Kit): HudItem[] { return []; }",
    "export function hud(state: GameState, kit: Kit): HudItem[] { const z: any = null; return z.nope(); }")
HOOKS_TYPE_ERROR = HOOKS_GOOD.replace(
    "  if (input.pressed(\" \")) state.score += 1;",
    "  const n: number = \"not a number\"; state.score += n;")
# A game.ts that still exports a draw hook: GameHooks has no such member, so the contract assertion
# at the end of the file is a type error naming it.
HOOKS_WITH_DRAW = HOOKS_GOOD.replace(
    "const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };",
    "export function draw(g: any, state: GameState, kit: Kit): void { g.rect(0, 0, 1, 1, \"#fff\"); }\n"
    "const _scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud };")


def _write_hook_game(tmp_path, game_ts=HOOKS_GOOD, extra=None, spec=None):
    """A SCAFFOLDED run: the GENERATED main.ts from the spec's scheme + the model's game.ts (and any
    sibling system files) + a manifest naming them + an empty data design — gate-ready."""
    spec = spec or {"frozen": True, "mode": "2d", "design": {"control": {"scheme": "top-down"}}}
    seed_scaffold(RunState(tmp_path), spec)
    d = tmp_path / "game"
    (d / "game.ts").write_text(game_ts, encoding="utf-8")
    files = [{"name": "game.ts", "purpose": "the whole game behind the scaffold hooks",
              "exports": ["createState", "init", "update", "hud"]}]
    for name, src in (extra or {}).items():
        (d / name).write_text(src, encoding="utf-8")
        files.append({"name": name, "purpose": "", "exports": []})
    (d / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    (d / "data").mkdir(exist_ok=True)
    (d / "data" / "manifest.json").write_text(json.dumps({"datasets": []}), encoding="utf-8")
    seed_interfaces(tmp_path)
    return spec


def _run_dir(tmp_path) -> RunState:
    return RunState(tmp_path)


def _write_game(tmp_path, code, extra=None):
    """Write a game folder (game/main.ts + a manifest naming it + an empty data design) so
    `planned`/`data`/`authored` pass and the gate checks run. `extra` = {name: src} for extra
    system files."""
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "main.ts").write_text(code, encoding="utf-8")
    files = [{"name": "main.ts", "purpose": "game", "exports": ["createGame"]}]
    for name, src in (extra or {}).items():
        (d / name).write_text(src, encoding="utf-8")
        files.append({"name": name, "purpose": "", "exports": []})
    (d / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    (d / "data").mkdir(exist_ok=True)
    (d / "data" / "manifest.json").write_text(json.dumps({"datasets": []}), encoding="utf-8")
    seed_interfaces(tmp_path)


def _node_eval(js: str) -> dict:
    """Run an inline ES-module snippet against the kit and parse its JSON stdout. Used to unit-test
    kit primitives that need driven input a self-contained headless game can't produce."""
    p = subprocess.run(["node", "--input-type=module", "-e", js],
                       cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


# ── gates ─────────────────────────────────────────────────────────────────────
# A steering game: the enemy kit.seek()s the player and catches it — proves the steering primitive
# is wired into the kit and numerically sound (reaches the target, no NaN) under a headless run.
SEEK_GAME = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 300, height: 300 },
    state: { world: [] as World, player: { x: 250, y: 250, w: 8, h: 8 } as any, e: null as any },
    init(kit) { this.state.e = kit.spawn(this.state.world, { x: 0, y: 0, w: 8, h: 8 }); },
    update(dt, input, kit) { const d = kit.seek(this.state.e, this.state.player, 120); kit.integrate(this.state.e, dt); if (d < 10) kit.win("caught"); },
  };
}"""


def test_steering_seek_reaches_target(tmp_path):
    _write_game(tmp_path, SEEK_GAME)
    hl = run_headless(tmp_path)
    assert hl["ok"] is True and hl.get("resolved") == "win"


def test_flyer_thrusts_forward_and_stays_finite():
    r = _node_eval("""
      import {makeKit, makeRng} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const input = { down:(k)=>k===" ", pressed:()=>false };
      const e = {x:0,y:5,z:0,yaw:0,pitch:0};
      for (let i=0;i<120;i++) kit.flyer(e, input, 1/60, {thrust:40});
      console.log(JSON.stringify({z:e.z, finite: [e.x,e.y,e.z].every(Number.isFinite)}));
    """)
    assert r["finite"] is True and r["z"] > 5   # +z is forward at yaw 0


def test_astar_routes_around_a_wall():
    r = _node_eval("""
      import {makeKit, makeRng} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const blocked = (x,y)=> x===2 && y<4;            // a wall with a gap at y=4
      const path = kit.astar({x:0,y:0}, {x:4,y:0}, (x,y)=>!blocked(x,y), {cols:5, rows:5});
      const last = path[path.length-1];
      console.log(JSON.stringify({
        len: path.length,
        reachesGoal: !!last && last.x===4 && last.y===0,
        hitsWall: path.some(c=>blocked(c.x,c.y)),
      }));
    """)
    assert r["len"] > 0 and r["reachesGoal"] is True and r["hitsWall"] is False


def test_astar_returns_empty_when_unreachable():
    r = _node_eval("""
      import {makeKit, makeRng} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const wall = (x,y)=> x===2;                       // a full wall — no gap
      const path = kit.astar({x:0,y:0}, {x:4,y:0}, (x,y)=>!wall(x,y), {cols:5, rows:5});
      console.log(JSON.stringify({len: path.length}));
    """)
    assert r["len"] == 0


def test_gridmove_steps_into_open_and_blocks_on_walls():
    r = _node_eval("""
      import {makeKit, makeRng} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const cell = 32;
      const pass = (x,y) => !(x===1 && y===0);         // a wall one cell to the right
      const e = {x:0, y:0};
      const movedUp = kit.gridMove(e, 0, 1, cell, pass);   // open -> moves
      const blocked = kit.gridMove(e, 1, -1, cell, pass);  // into the wall at (1,0) -> blocked
      console.log(JSON.stringify({movedUp, blocked, x:e.x, y:e.y}));
    """)
    assert r["movedUp"] is True and r["blocked"] is False
    assert r["x"] == 0 and r["y"] == 32   # stepped down one cell, never into the wall


def test_particles_burst_then_expire_and_cull():
    r = _node_eval("""
      import {makeKit, makeRng} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const world = [];
      kit.burst(world, 100, 100, 10, {life:0.2, rng:kit.rng});
      const spawned = world.length;
      for (let i=0;i<30;i++) kit.stepParticles(world, 1/60);   // 0.5s > life -> all expire
      console.log(JSON.stringify({spawned, remaining: world.length}));
    """)
    assert r["spawned"] == 10 and r["remaining"] == 0


def test_physics3_falls_and_lands_on_ground():
    r = _node_eval("""
      import {makeKit, makeRng} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const b = {x:0,y:50,z:0,vy:0};
      for (let i=0;i<600;i++) kit.physics3(b, 1/60, 20, 0);
      console.log(JSON.stringify({y:b.y, grounded:b.grounded}));
    """)
    assert abs(r["y"]) < 0.001 and r["grounded"] is True


def test_move_relative_follows_camera_yaw():
    # W drives along the camera's heading (input.camYaw), not a world axis: at yaw 0, W = -z (into a
    # screen whose chase camera sits at +z); rotate the camera 90deg and the SAME W key drives +x.
    r = _node_eval("""
      import {makeKit, makeRng, makeInput} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const input = makeInput(); input._set("w", true);
      const a = {x:0,y:0,z:0}; input.camYaw = 0;
      kit.moveRelative(a, input, 1, 10);
      const b = {x:0,y:0,z:0}; input.camYaw = Math.PI/2;
      kit.moveRelative(b, input, 1, 10);
      console.log(JSON.stringify({ ax:+a.x.toFixed(3), az:+a.z.toFixed(3), bx:+b.x.toFixed(3), bz:+b.z.toFixed(3) }));
    """)
    assert r["ax"] == 0 and r["az"] == -10           # yaw 0: W -> into screen (-z)
    assert r["bx"] == 10 and abs(r["bz"]) < 0.001     # yaw 90deg: same W -> +x


# ── collideWorld: the ONE 2D solid pass (tile pushout + pair separation) ──────
def test_collide_world_pushes_solid_entity_out_of_solid_tile():
    r = _node_eval("""
      import {makeKit, makeRng} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const solidAt = (cx, cy) => cx === 2 && cy === 1;      // one wall cell: x 64..96, y 32..64
      const e = { x: 60, y: 40, w: 20, h: 20, vx: 50, solid: true };
      kit.collideWorld([e], solidAt, 32);
      const inside = e.x + e.w > 64 && e.x < 96 && e.y + e.h > 32 && e.y < 64;
      console.log(JSON.stringify({x: e.x, vx: e.vx, inside}));
    """)
    assert r["inside"] is False
    assert r["x"] == 44 and r["vx"] == 0   # minimal-axis pushout; blocked velocity zeroed


def test_collide_world_separates_solid_pair_symmetrically():
    r = _node_eval("""
      import {collideWorld} from "./engine.js";
      const a = { x: 100, y: 100, w: 20, h: 20, solid: true };
      const b = { x: 110, y: 100, w: 20, h: 20, solid: true };
      collideWorld([a, b]);
      console.log(JSON.stringify({ax: a.x, bx: b.x, ay: a.y, by: b.y}));
    """)
    assert r["ax"] == 95 and r["bx"] == 115   # 10px x-overlap split half-and-half
    assert r["ay"] == 100 and r["by"] == 100  # minimal axis only


def test_collide_world_leaves_non_solid_untouched():
    r = _node_eval("""
      import {collideWorld} from "./engine.js";
      const a = { x: 100, y: 100, w: 20, h: 20, solid: true };
      const bullet = { x: 105, y: 100, w: 6, h: 6 };          // overlaps a; not solid
      const ghost = { x: 40, y: 40, w: 10, h: 10 };           // rests in a solid cell; not solid
      collideWorld([a, bullet, ghost], (cx, cy) => cx === 1 && cy === 1, 32);
      console.log(JSON.stringify({bx: bullet.x, gx: ghost.x, ax: a.x}));
    """)
    assert r["bx"] == 105 and r["gx"] == 40 and r["ax"] == 100


def test_collide_world_is_deterministic():
    r = _node_eval("""
      import {collideWorld, makeRng} from "./engine.js";
      const build = () => {
        const rng = makeRng(7), w = [];
        for (let i = 0; i < 30; i++)
          w.push({ x: rng.int(0, 300), y: rng.int(0, 300), w: 20, h: 20, solid: true });
        return w;
      };
      const solidAt = (cx, cy) => (cx + cy) % 7 === 0;
      const a = build(), b = build();
      for (let i = 0; i < 5; i++) { collideWorld(a, solidAt, 32); collideWorld(b, solidAt, 32); }
      console.log(JSON.stringify({same: JSON.stringify(a) === JSON.stringify(b)}));
    """)
    assert r["same"] is True


# ── action registry: register / bindings / edge-fire ──────────────────────────
def test_register_edge_fires_once_per_press_and_rereg_replaces():
    r = _node_eval("""
      import {makeKit, makeRng, makeInput} from "./engine.js";
      const kit = makeKit({}, makeRng(1));
      const input = makeInput();
      let hits = 0, other = 0;
      kit.register("attack", ["F"], () => { hits++; });      // "F" normalizes like the key listener
      input._set("f", true); kit._fireActions(input); input._endFrame();   // press: ONE edge
      kit._fireActions(input); input._endFrame();                          // held: no edge
      kit._fireActions(input); input._endFrame();
      input._set("f", false); input._endFrame();
      input._set("f", true); kit._fireActions(input); input._endFrame();   // second press
      const afterTwoPresses = hits;
      kit.register("attack", ["f"], () => { other++; });     // re-register REPLACES (no double-fire)
      input._set("f", false); input._endFrame();
      input._set("f", true); kit._fireActions(input); input._endFrame();
      console.log(JSON.stringify({afterTwoPresses, hits, other, bindings: kit.bindings()}));
    """)
    assert r["afterTwoPresses"] == 2 and r["hits"] == 2 and r["other"] == 1
    assert r["bindings"] == [{"name": "attack", "keys": ["f"]}]


def test_simulate_fires_registered_actions():
    r = _node_eval("""
      import {simulate} from "./engine.js";
      const game = (kit) => ({
        config: { width: 100, height: 100, seed: 1 },
        state: { world: [], score: 0 },
        init(kit) {
          kit.register("attack", [" "], () => {
            this.state.score += 1;
            if (this.state.score >= 2) kit.win("done");
          });
        },
        update(dt, input, kit) {},
      });
      const res = simulate(game, { frames: 120, script: [
        { frame: 5, key: " ", down: true }, { frame: 8, key: " ", down: false },
        { frame: 20, key: " ", down: true } ] });
      console.log(JSON.stringify({ok: res.ok, resolved: res.resolved, frame: res.frame}));
    """)
    assert r["ok"] is True and r["resolved"] == "win"   # both presses fired exactly once each
    assert r["frame"] >= 20


# ── data-driven visuals: a row IS the entity's look ───────────────────────────
def test_spawn_data_binds_the_row_s_visual_and_asset_id():
    """The point of the whole data-visual path: an entity spawned from a row carries that row's
    size/shape/color AND its asset id (`mesh` in 3D, `sprite` in 2D), so the skin stage never has to
    rewrite source to tag it. Sizes ride through UNSCALED (a live build divided them by 100)."""
    r = _node_eval("""
      import {dataVisual} from "./engine.js";
      const wolf = { id: "wolf", size: { w: 0.8, h: 1.0, d: 1.8 }, color: "#777777" };
      const orb  = { id: "orb", size: { w: 0.6, h: 0.6, d: 0.6 }, shape: "sphere", color: "#44ccff" };
      const bare = { id: "bare" };
      const slime = { id: "slime", size: { w: 24, h: 24 }, shape: "circle", color: "#33cc77" };
      console.log(JSON.stringify({
        wolf3d: dataVisual(wolf, "3d"), orb3d: dataVisual(orb, "3d"),
        bare3d: dataVisual(bare, "3d"), slime2d: dataVisual(slime, "2d"),
        bare2d: dataVisual(bare, "2d"),
      }));
    """)
    assert r["wolf3d"] == {"shape": "box", "w": 0.8, "h": 1.0, "d": 1.8,
                           "color": "#777777", "mesh": "wolf"}
    assert r["orb3d"] == {"shape": "sphere", "r": 0.3, "color": "#44ccff", "mesh": "orb"}
    assert r["slime2d"] == {"shape": "circle", "w": 24, "h": 24,
                            "color": "#33cc77", "sprite": "slime"}
    # shape/color are OPTIONAL — a row that omits them still draws
    assert r["bare3d"]["shape"] == "box" and r["bare3d"]["mesh"] == "bare"
    assert r["bare2d"]["shape"] == "rect" and r["bare2d"]["sprite"] == "bare"


def test_spawn_data_position_overrides_and_lands_in_the_world():
    r = _node_eval("""
      import {spawnData} from "./engine.js";
      const world = [];
      const e = spawnData(world, { id: "wolf", size: { w: 1, h: 1, d: 2 }, color: "#777777" },
                          { x: 5, y: 0.5, z: -3, hp: 20 }, "3d");
      console.log(JSON.stringify({ n: world.length, same: world[0] === e,
                                   x: e.x, y: e.y, z: e.z, hp: e.hp, mesh: e.mesh }));
    """)
    assert r == {"n": 1, "same": True, "x": 5, "y": 0.5, "z": -3, "hp": 20, "mesh": "wolf"}


def test_draw_entity_renders_a_compound_look_from_parts():
    """A multi-shape look stays DATA: `parts` are fractions of the entity box, so the row still owns
    the art and one sprite can replace all of it."""
    r = _node_eval("""
      import {drawEntity} from "./engine.js";
      const calls = [];
      const g = { rect: (...a) => calls.push(["rect", ...a]),
                  circle: (...a) => calls.push(["circle", ...a]),
                  sprite: (...a) => calls.push(["sprite"]) };
      const e = { x: 100, y: 200, w: 32, h: 32, color: "#ccddee", sprite: "fighter", parts: [
        { shape: "rect", dx: 0.25, dy: 0, w: 0.5, h: 1, color: "#ccddee" },
        { shape: "circle", dx: 0.25, dy: 0.5, w: 0.5, h: 0.5 },
      ] };
      drawEntity(g, e, {});                        // unskinned -> every part
      drawEntity(g, e, { fighter: "IMG" });        // skinned -> ONE sprite, parts gone
      console.log(JSON.stringify(calls));
    """)
    assert r[0] == ["rect", 108, 200, 16, 32, "#ccddee"]
    assert r[1] == ["circle", 116, 224, 8, "#ccddee"]     # part color falls back to the entity's
    assert r[2] == ["sprite"] and len(r) == 3


def test_draw_entity_prefers_the_sprite_and_falls_back_to_the_shape():
    """kit.drawEntity is the 2D half — the same call renders a game before and after skinning, so a
    skinned game needs no draw() rewrite."""
    r = _node_eval("""
      import {drawEntity} from "./engine.js";
      const calls = [];
      const g = { rect: (...a) => calls.push(["rect", ...a]),
                  circle: (...a) => calls.push(["circle", ...a]),
                  sprite: (...a) => calls.push(["sprite", a[1], a[2], a[3], a[4]]) };
      const box = { x: 10, y: 20, w: 8, h: 8, color: "#ff0000", sprite: "slime" };
      drawEntity(g, box, {});                       // no asset loaded -> shape
      drawEntity(g, { ...box, shape: "circle" }, {});
      drawEntity(g, box, { slime: "IMG" });         // asset loaded -> sprite
      console.log(JSON.stringify(calls));
    """)
    assert r[0] == ["rect", 10, 20, 8, 8, "#ff0000"]
    assert r[1] == ["circle", 14, 24, 4, "#ff0000"]     # centered from the box
    assert r[2] == ["sprite", 10, 20, 8, 8]




def test_gamepad_controls_normalize_to_real_keys():
    """A spec is free to describe a gamepad — the runtime has none, so the pipeline MAPS the intent
    onto keys that exist rather than refusing it. A live build drew LEFT_STICK/A_BUTTON and the
    probe's unbound_control became unsatisfiable, grinding the fix loop until the step cap."""
    d = {"controls": {"LEFT_STICK": "move hero", "RIGHT_STICK": "orbit camera",
                      "A_BUTTON": "interact", "B_BUTTON": "swing weapon"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {"W/A/S/D": "move hero", "Mouse": "orbit camera",
                             "e": "interact", "q": "swing weapon"}


def test_gamepad_alias_never_collides_with_a_key_the_spec_already_uses():
    d = {"controls": {"E": "open door", "A_BUTTON": "interact", "Start": "pause"}}
    controls_mod.normalize_controls(d)
    assert d["controls"]["e"] == "open door"          # the real key keeps its meaning
    assert d["controls"]["q"] == "interact"           # the pad button moves to a free one
    assert d["controls"]["Escape"] == "pause"
    assert len(d["controls"]) == 3                    # nothing silently dropped


def test_keys_already_in_the_runtime_vocabulary_pass_through():
    d = {"controls": {"e": "use", " ": "jump", "ArrowLeft": "left", "Escape": "pause",
                      "Tab": "map", "Shift": "run", "Enter": "confirm", "1": "slot one"}}
    before = dict(d["controls"])
    controls_mod.normalize_controls(d)
    assert d["controls"] == before


def test_single_letter_keys_fold_to_the_case_the_runtime_binds():
    """The runtime lowercases single characters (keymap + register), and `pressed` matches
    exactly — so a spec key of "W" is a control nothing can ever press."""
    d = {"controls": {"W": "forward", "Z": "zoom", "B": "bomb"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {"w": "forward", "z": "zoom", "b": "bomb"}


@pytest.mark.parametrize("raw,key", [
    ("SPACE", " "), ("Spacebar", " "), ("Space", " "), ("spacebar", " "),
    ("ESC", "Escape"), ("Escape", "Escape"),
    ("Enter", "Enter"), ("Return", "Enter"),
    ("TAB", "Tab"), ("SHIFT", "Shift"),
    ("Up", "ArrowUp"), ("Up Arrow", "ArrowUp"), ("UP_ARROW", "ArrowUp"),
    ("DOWN", "ArrowDown"), ("LEFT_ARROW", "ArrowLeft"), ("Right Arrow", "ArrowRight"),
    ("Key Q", "q"), ("1-5", "1"),
])
def test_word_forms_map_onto_the_key_that_exists(raw, key):
    d = {"controls": {raw: "do a thing"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {key: "do a thing"}


@pytest.mark.parametrize("raw", ["WASD", "W/A/S/D", "W, A, S, D", "WASD/Arrows", "Arrow Keys",
                                 "W/Up", "A/Left", "S/DOWN", "D/Right", "A/Left/D/Right",
                                 "W/S", "ArrowUp / W", "LEFT/RIGHT ARROW"])
def test_movement_aggregates_collapse_to_the_scaffold_sentinel(raw):
    """Movement is scaffold-owned: an aggregate must not become a per-key binding."""
    d = {"controls": {raw: "move"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {"W/A/S/D": "move"}


@pytest.mark.parametrize("raw", ["Left Click", "LEFT_CLICK", "Right Click", "Mouse Look",
                                 "MOUSE_MOVE", "Mouse Move", "MOUSE_XY", "Mouse", "click",
                                 "left mouse button", "MOUSE CLICK CARD"])
def test_mouse_keeps_the_sentinel_on_a_scheme_that_owns_the_mouse(raw):
    d = {"control": {"scheme": "first-person-3d"}, "controls": {raw: "aim"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {"Mouse": "aim"}


@pytest.mark.parametrize("scheme", ["top-down", "platformer", "grid-turn", "follow-3d",
                                    "vehicle-3d", None])
def test_mouse_lands_on_a_free_key_where_there_is_no_mouse_input(scheme):
    """Only first-person/orbital read the mouse; everywhere else `pointer` is never wired, so a
    mouse control would ship dead."""
    d = {"control": {"scheme": scheme} if scheme else None,
         "controls": {"Left Click": "shoot", "E": "use"}}
    controls_mod.normalize_controls(d)
    assert d["controls"]["e"] == "use"                # the spec's own key is reserved first
    assert d["controls"]["q"] == "shoot"              # the mouse action gets a free key
    assert "Mouse" not in d["controls"]


def test_a_second_mouse_action_gets_its_own_key():
    """One pointer can't tell a left click from a right click — the look half keeps the sentinel
    and the clicks take keys, so no mechanic is overwritten."""
    d = {"control": {"scheme": "first-person-3d"},
         "controls": {"Mouse Look": "aim", "Left Click": "fire", "Right Click": "block"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {"Mouse": "aim", "e": "fire", "q": "block"}


@pytest.mark.parametrize("raw,key", [("Right Click / Key Q", "q"), ("Space/Click", " "),
                                     ("Q / Right Click", "q"), ("F or Left Click", "f")])
def test_alternates_resolve_to_the_first_bindable_alternative(raw, key):
    d = {"controls": {raw: "parry"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {key: "parry"}


def test_an_unmappable_control_is_kept_never_dropped():
    d = {"controls": {"MUTE_KEY": "toggle sound", "E": "use"}}
    controls_mod.normalize_controls(d)
    assert d["controls"] == {"MUTE_KEY": "toggle sound", "e": "use"}


def test_controls_that_are_not_a_map_are_left_alone():
    d = {"controls": "WASD to move, click to shoot"}
    controls_mod.normalize_controls(d)
    assert d["controls"] == "WASD to move, click to shoot"










def test_run_headless_green_on_pong(tmp_path):
    _write_game(tmp_path, GOOD)
    assert run_headless(_run_dir(tmp_path).run_dir).get("ok") is True


def test_run_headless_reports_missing(tmp_path):
    hl = run_headless(tmp_path)
    assert hl["ok"] is False and hl["phase"] == "build"   # nothing to bundle yet


def test_run_headless_catches_crash(tmp_path):
    _write_game(tmp_path, BROKEN)
    hl = run_headless(tmp_path)
    assert hl["ok"] is False and "boom" in hl.get("error", "")




def test_run_render_green_on_pong(tmp_path):
    _write_game(tmp_path, GOOD)
    assert run_render(tmp_path).get("ok") is True


def test_run_render_catches_draw_crash(tmp_path):
    _write_game(tmp_path, DRAW_CRASH)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "draw_crash"




def test_run_render_green_on_a_3d_game(tmp_path):
    _write_game(tmp_path, GAME_3D)
    assert run_render(tmp_path)["ok"] is True


def test_run_render_runs_3d_hud(tmp_path):
    # a 3D game's HUD is data returned from hud() — the render gate validates + renders it, not skips.
    _write_game(tmp_path, HUD_3D)
    rr = run_render(tmp_path)
    assert rr.get("ok") is True and not rr.get("skipped")


def test_run_render_rejects_a_draw_hook(tmp_path):
    # no game draws, in either mode: the engine renders state.world. A draw hook is dead code that
    # would also blank the scene, so the gate rejects it outright.
    for src in (HUD_DRAW_IN_3D, DRAW_HOOK):
        _write_game(tmp_path, src)
        rr = run_render(tmp_path)
        assert rr["ok"] is False and rr["violations"][0]["kind"] == "draw_hook"


def test_run_render_catches_bad_hud_data(tmp_path):
    # hud() returning a malformed item is caught by the gate's declarative validation.
    _write_game(tmp_path, HUD_BAD_DATA)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "hud_bad"


def test_screen_space_crash_passes_headless_but_not_render(tmp_path):
    # the whole point of the render gate: the sim gates alone give a false green here.
    _write_game(tmp_path, DRAW_CRASH)
    assert run_headless(tmp_path).get("ok") is True
    assert run_render(tmp_path).get("ok") is False










def test_extract_code_pulls_fenced_block():
    assert extract_code("blah\n```js\nconst x = 1;\n```\ntrailing") == "const x = 1;"


# ── tools ─────────────────────────────────────────────────────────────────────
def test_write_then_read_file(tmp_path):
    tools = build_codegen_tools(_run_dir(tmp_path))
    assert tools["write"](code=GOOD)["ok"] is True
    assert tools["read_file"]()["content"] == GOOD


def test_write_rejects_empty(tmp_path):
    tools = build_codegen_tools(_run_dir(tmp_path))
    assert tools["write"](code="  ")["ok"] is False


# ── module checks ─────────────────────────────────────────────────────────────
def _ctx(state):
    return build_context({"mode": "2d", "design": {}}, state)


def test_interfaced_error_when_empty(tmp_path):
    # an empty run reports exactly one thing: declare the architecture (blocking, suppresses the rest).
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["interfaced"]
    assert errs[0].type is ErrorType.BUILD


def test_reviewed_blocks_until_the_architecture_has_been_reviewed(tmp_path):
    interfaces.save(tmp_path, {"state": [], "invariants": [],
                               "functions": [{"name": "update", "file": "game.ts"}]})
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["reviewed"]


def test_authored_in_dependency_order_contract_first_entry_last(tmp_path):
    # authoring is ONE file at a time in DEPENDENCY order: the shared-types file FIRST (consumers
    # author against real types), the entry game.ts LAST (it wires every system, so it binds against
    # its siblings' real on-disk signatures) — even though the manifest lists game.ts second.
    d = tmp_path / "game"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "types.ts", "purpose": "shared interfaces", "exports": ["GameState"]},
        {"name": "game.ts", "purpose": "entry", "exports": ["createState"]},
        {"name": "combat.ts", "purpose": "combat", "exports": ["attack"]}]}))
    (d / "data").mkdir()
    (d / "data" / "manifest.json").write_text(json.dumps({"datasets": []}))
    seed_interfaces(tmp_path)
    order = [f["name"] for f in _authoring_order(_run_dir(tmp_path).run_dir)]
    assert order == ["types.ts", "combat.ts", "game.ts"]   # contract first, systems, entry LAST
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["authored"]
    assert [e.path for e in errs] == ["types.ts"]   # first to author


def test_typecheck_gate_catches_type_error(tmp_path):
    # a type error (string assigned to number) is caught by tsc, tagged with the file, BEFORE the run.
    _write_hook_game(tmp_path, HOOKS_TYPE_ERROR)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["typechecks"]
    assert errs[0].path == "game.ts" and "TS" in errs[0].message


def test_typecheck_catches_cross_file_shape_mismatch(tmp_path):
    # THE motivating bug: level.ts returns a boolean grid, game.ts consumes a char grid — tsc catches
    # the disagreement across files, which no runtime gate could.
    level = "export function makeMap(): boolean[][] { return [[false]]; }"
    game = HOOKS_GOOD.replace(
        "export interface GameState",
        "import { makeMap } from './level.ts';\nexport interface GameState").replace(
        'state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff" });',
        'state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff" });\n'
        "  const m: string[][] = makeMap();")
    _write_hook_game(tmp_path, game, extra={"level.ts": level})
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["typechecks"]
    assert errs[0].path == "game.ts"


def test_typecheck_catches_a_draw_hook_in_game_ts(tmp_path):
    # the compile-time half of "no game draws": GameHooks has no draw member, so the contract
    # assertion fails INSIDE game.ts naming it, before the render gate ever runs.
    _write_hook_game(tmp_path, HOOKS_WITH_DRAW)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["typechecks"]
    assert "draw" in errs[0].message


def test_a_2d_entity_may_carry_its_2d_shape(tmp_path):
    """The engine renders state.world from each entity's `shape`, and the 2D shapes are rect/circle.
    They were missing from Entity.shape (3D tags only), so every 2D game hit TS2322 on a type only
    the kit can change — a fix loop cannot converge on that."""
    game = HOOKS_GOOD.replace(
        'state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff" });',
        'state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff",\n'
        '    shape: "rect" });\n'
        '  kit.spawn(state.world, { x: 50, y: 50, w: 8, h: 8, shape: "circle", layer: 2 });')
    _write_hook_game(tmp_path, game)
    assert CodegenModule().get_errors(_ctx(_run_dir(tmp_path))) == []


def test_clean_game_has_no_errors(tmp_path):
    _write_hook_game(tmp_path)
    assert CodegenModule().get_errors(_ctx(_run_dir(tmp_path))) == []


def test_runs_error_on_crash(tmp_path):
    _write_hook_game(tmp_path, HOOKS_BROKEN)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["runs"]
    assert errs[0].type is ErrorType.FIX


def test_renders_error_when_draw_crashes(tmp_path):
    # authored + runs + plays are clean; only the render gate fires.
    _write_hook_game(tmp_path, HOOKS_DRAW_CRASH)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["renders"]
    assert "draw_crash" in errs[0].message


# ── the loop drives a game to passing ─────────────────────────────────────────
class _FakeConn:
    """Returns the pong module in a fenced block for every call — the authoring step the loop runs."""
    def __init__(self, code):
        self.code = code
        self.calls = 0

    def generate_with_tools(self, messages, tools=None, **kw):
        pre = canned_prelude(messages)
        if pre is not None:
            return pre
        self.calls += 1
        return {"choices": [{"message": {"content": f"```js\n{self.code}\n```"}}]}


def test_loop_authors_until_gates_pass(tmp_path, monkeypatch):
    state = _run_dir(tmp_path)
    spec = {"frozen": True, "mode": "2d", "title": "Pong",
            "design": {"title": "Pong", "control": {"scheme": "top-down"}}}
    state.write_spec(spec)
    conn = _FakeConn(HOOKS_GOOD)   # kickoff seeds the scaffold, then plan(fallback)/data/author drive
    cursor = run_build_to_completion(str(tmp_path), conn, monkeypatch)
    assert cursor.ok is True
    assert conn.calls >= 1
    assert cursor.step >= 1   # each llm turn advances the step so max_steps actually bounds it
    assert (tmp_path / "game" / "game.ts").read_text().strip() == HOOKS_GOOD.strip()


def test_multi_file_game_loads_and_gates(tmp_path):
    # a game split across files (game.ts imports a typed system file) type-checks, bundles, and runs.
    game = HOOKS_GOOD.replace(
        "export interface GameState",
        "import { scorePoint } from './scoring.ts';\nexport interface GameState").replace(
        'if (input.pressed(" ")) state.score += 1;',
        'if (input.pressed(" ")) state.score = scorePoint(state.score, 1);')
    scoring = ("export function scorePoint(score: number, by: number): number {\n"
               "  return score + by; }")
    _write_hook_game(tmp_path, game, extra={"scoring.ts": scoring})
    assert run_headless(tmp_path).get("ok") is True
    assert CodegenModule().get_errors(_ctx(_run_dir(tmp_path))) == []


class _FakeToolConn:
    """Scripts the read→edit subloop: call 1 reads a file, call 2 lands an atomic multi-hunk edit —
    both are real tool calls. Records the actions so a test can assert the fix READ before it EDITED."""
    def __init__(self, edits, target="main.ts"):
        self.edits = edits
        self.target = target
        self.calls = []
        self.reasonings = []
        self.user_msgs = []

    def generate_with_tools(self, messages, tools=None, **kw):
        self.reasonings.append(kw.get("reasoning"))
        self.user_msgs += [m.get("content", "") for m in messages if m.get("role") == "user"]
        step = len(self.calls)
        if step == 0:
            self.calls.append("read_file")
            tc = {"id": "c0", "type": "function",
                  "function": {"name": "read_file", "arguments": json.dumps({"file": self.target})}}
            return {"choices": [{"message": {"content": "", "tool_calls": [tc]}}]}
        self.calls.append("edit")
        tc = {"id": "c1", "type": "function",
              "function": {"name": "edit",
                           "arguments": json.dumps({"file": self.target, "edits": self.edits})}}
        return {"choices": [{"message": {"content": "Fixed it.", "tool_calls": [tc]}}]}


def test_fix_subloop_reads_then_edits_to_green(tmp_path, monkeypatch):
    # A game that crashes headless (init throws). The fix subloop must read, then land a grounded
    # edit (write refuses overwrites now); the outer loop re-gates to green.
    state = _run_dir(tmp_path)
    spec = _write_hook_game(tmp_path, HOOKS_BROKEN)
    spec = {**spec, "title": "T", "design": {**spec["design"], "title": "T"}}
    state.write_spec(spec)
    conn = _FakeToolConn([{"old_string": HOOKS_BROKEN, "new_string": HOOKS_GOOD}], target="game.ts")
    cursor = run_build_to_completion(str(tmp_path), conn, monkeypatch)
    assert cursor.ok is True
    assert "read_file" in conn.calls and "edit" in conn.calls
    assert conn.calls.index("read_file") < conn.calls.index("edit")
    assert (tmp_path / "game" / "game.ts").read_text().strip() == HOOKS_GOOD.strip()
    # the fix loop forces reasoning OFF — a thinking model burns the whole budget reasoning and starves
    # the tool call.
    assert conn.reasonings and all(r == "none" for r in conn.reasonings)


def test_fix_from_note_routes_through_subloop_and_lands_edit(tmp_path, monkeypatch):
    """A human playtest note runs through the SAME read→edit subloop as a gate failure (no whole-file
    rewrite shape left): the note rides as the failing-gate text, the fix lands as a grounded edit,
    then the build re-gates."""
    state = _run_dir(tmp_path)
    spec = _write_hook_game(tmp_path)
    state.write_spec({**spec, "title": "T"})

    conn = _FakeToolConn([{"old_string": 'if (input.pressed(" ")) state.score += 1;',
                           "new_string": 'if (input.pressed(" ")) state.score += 2;'}],
                         target="game.ts")
    cursor = run_build_to_completion(str(tmp_path), conn, monkeypatch, kind="fix",
                                     note="the player can leave the screen on the right")
    assert cursor.ok is True
    assert conn.calls == ["read_file", "edit"]           # the subloop shape: grounded read, then edit
    assert any("HUMAN PLAYTEST FEEDBACK" in m and "leave the screen" in m for m in conn.user_msgs)
    assert 'state.score += 2;' in (tmp_path / "game" / "game.ts").read_text()


def test_human_note_error_classifies_to_default():
    """The synthetic note Error (code='human') must fall through fix_classes to `default` — no TS-code
    or gate-kind matcher may claim a prose note."""
    e = Error(type=ErrorType.HUMAN, code="human", component="game",
              message="HUMAN PLAYTEST FEEDBACK — the paddle moves the wrong way")
    assert classify(e) is DEFAULT


def test_loop_refuses_unfrozen_spec(tmp_path):
    from maestro.codegen import build_chain
    state = _run_dir(tmp_path)
    state.write_spec({"frozen": False, "mode": "2d", "design": {}})
    with pytest.raises(RuntimeError):
        build_chain.start_build(str(tmp_path), "bid")


# ── context assembly (the fixes that converge multi-file builds) ────────────────
def test_fix_schemas_ladder():
    """read · edit · write are all real tool calls. EDIT is ALWAYS offered — it is the only way to
    change an existing file (write refuses overwrites), so no ladder step may drop it. READ drops
    ONLY on within-fix read-thrash (_READS_BEFORE_FORCE_ACT) — never on the outer stall: edits are
    grounded in reads, so an escalated fix that cannot read can only guess anchors (measured death
    spiral in a live run). WRITE stays offered for the create-a-missing-planned-file case; the
    tool itself refuses an overwrite."""

    def names(schemas):
        return {s["function"]["name"] for s in schemas}

    lo = _READS_BEFORE_FORCE_ACT - 1
    hi = _READS_BEFORE_FORCE_ACT
    assert names(_fix_schemas(escalate=False, nreads=0)) == {"read_file", "edit", "write"}
    assert names(_fix_schemas(escalate=True, nreads=0)) == {"read_file", "edit", "write"}   # stall keeps grounding
    # read is still offered right up to the threshold, then dropped so the fix must ACT
    assert "read_file" in names(_fix_schemas(escalate=False, nreads=lo))
    assert names(_fix_schemas(escalate=False, nreads=hi)) == {"edit", "write"}
    # edit is never dropped — an existing file can only be changed through it
    assert all({"edit", "write"} <= names(_fix_schemas(e, r))
               for e in (True, False) for r in (0, hi))


def test_author_extracts_code_from_the_write_tool_call():
    """Authoring goes THROUGH the write tool (a real tool call with the file in a `code` arg): the
    author step pulls the tool call's `code` and writes it. The write schema is what the turn offers."""
    from maestro.codegen.build_steps import _WRITE_SCHEMA, _extract_write_code
    result = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "write",
                                  "arguments": json.dumps({"file": "main.ts", "code": "export const x = 1;"})}}]}}]}
    assert _extract_write_code(result) == "export const x = 1;"
    assert _WRITE_SCHEMA["function"]["name"] == "write"


def test_author_falls_back_to_fence_when_no_tool_call():
    """A local model that ignores the tool and emits a ```ts block still lands — the fence is scraped as
    a last resort so authoring never silently writes nothing."""
    from maestro.codegen.build_steps import _extract_write_code
    result = {"choices": [{"message": {"content": "here you go\n```ts\nexport const y = 2;\n```"}}]}
    assert _extract_write_code(result).strip() == "export const y = 2;"


def test_is_stub_rejects_placeholder_and_empty_bodies():
    """A reserve-the-file placeholder or an essentially-empty body is not a fix — writing it bricks
    the file. Real source passes."""
    assert _is_stub("// This is a placeholder to allow reading the actual file first.\n"
                    "// DO NOT USE - will be replaced with proper fix.\n")
    assert _is_stub("")
    assert _is_stub("   \n// TODO\n")
    assert _is_stub("// fill this in later\nexport {};")
    real = ("export function createGame(kit: Kit): GameObject {\n"
            "  return { config: {}, state: { world: [] }, update() {}, draw() {} };\n}\n")
    assert not _is_stub(real)


def test_fix_loop_injects_contract_invariant_only_for_multifile(tmp_path):
    """A game with a shared contract file gets the contract-invariant reminder in its fix context (so a
    fix rewriting types.ts can't re-declare Entity / any-out a field); a single-file game does not."""
    multi = [{"name": "types.ts", "purpose": "shared interfaces", "exports": ["GameState"]},
             {"name": "main.ts", "purpose": "entry", "exports": ["createGame"]}]
    assert any(_is_contract(f) for f in multi)
    solo = [{"name": "main.ts", "purpose": "the whole game", "exports": ["createGame"]}]
    assert not any(_is_contract(f) for f in solo)
    invariant = (Path(__file__).resolve().parents[1] / "src/maestro/codegen/prompts"
                 / "contract_invariant.txt").read_text()
    assert "extends Kit.Entity" in invariant and "any" in invariant


def test_sibling_sigs_capture_full_multiline_params(tmp_path):
    """A multi-line function signature must expose ALL its parameters — truncating at the first line
    drops the arg list, and a caller can't match the arg count (the arg-count oscillation bug)."""
    combat = ("import { Entity, GameState } from './types';\n"
              "export function applyDamage(\n  target: Entity,\n  amount: number,\n"
              "  state: GameState,\n  kit: any\n): void {\n  target.hp -= amount;\n}\n")
    _write_hook_game(tmp_path, extra={"combat.ts": combat})
    sigs = _sibling_lines(_run_dir(tmp_path).run_dir, exclude="game.ts")
    assert "applyDamage" in sigs
    for param in ("target: Entity", "amount: number", "state: GameState", "kit: any"):
        assert param in sigs, f"missing {param!r} — signature was truncated"


def test_kit_context_dropped_for_pure_typecheck_fix():
    """A type/contract typecheck fix gets NO kit doc (noise); a runtime gate gets the full doc; an
    arg-count typecheck fix gets just the signatures."""
    spec = {"mode": "3d"}
    pure = Error(type=ErrorType.FIX, code="typechecks", component="game",
                 message="types.ts has 1 type error(s):\n  - line 3: error TS2305: no exported member 'X'")
    assert _kit_context(spec, pure) == ""
    argc = Error(type=ErrorType.FIX, code="typechecks", component="game",
                 message="main.ts: error TS2554: Expected 4 arguments, but got 3.")
    assert "KIT CALL SIGNATURES" in _kit_context(spec, argc)
    argtype = Error(type=ErrorType.FIX, code="typechecks", component="game",
                    message="render.ts: error TS2345: Argument of type 'number' is not assignable to parameter of type 'string'.")
    assert "KIT CALL SIGNATURES" in _kit_context(spec, argtype)
    runtime = Error(type=ErrorType.FIX, code="runs", component="game", message="HEADLESS FAILED")
    assert "# KIT API" in _kit_context(spec, runtime)


def test_kit_surface_errors_get_the_ambient_dts():
    """A hallucinated kit name/member (TS2304/TS2552, TS2339 on a kit type) gets the ambient
    engine.d.ts — the model can't fix misuse of an API it can't see (the churn class from the
    first prod build). A TS2339 on a GAME type stays lean (that's the contract reconciler's job)."""
    spec = {"mode": "2d"}

    def err(msg):
        return Error(type=ErrorType.FIX, code="typechecks", component="game", message=msg)

    hallucinated_name = err("main.ts: error TS2304: Cannot find name 'g_fillRect'.")
    assert "KIT AMBIENT TYPES" in _kit_context(spec, hallucinated_name)
    assert "DrawApi" in _kit_context(spec, hallucinated_name)

    kit_member = err("main.ts: error TS2339: Property 'mouseX' does not exist on type 'Kit'.")
    assert "KIT AMBIENT TYPES" in _kit_context(spec, kit_member)

    # Mixed list (the real prod failure): surface errors dominate → ambient types injected.
    mixed = err("main.ts has 9 type error(s):\n  - line 315: error TS2339: Property 'mouseX' does "
                "not exist on type 'Kit'.\n  - line 495: error TS2304: Cannot find name 'g_fillRect'.")
    assert "KIT AMBIENT TYPES" in _kit_context(spec, mixed)

    game_member = err("main.ts: error TS2339: Property 'hp' does not exist on type 'Enemy'.")
    assert _kit_context(spec, game_member) == ""


# ── run-7 regressions: scaffold error attribution + duplicate-decl dedupe ───────
def test_generated_file_tsc_errors_reattribute_to_hook(tmp_path):
    """tsc blames the GENERATED scaffold when game.ts breaks the hook contract; the tools refuse to
    edit generated files, so the error must route to game.ts (measured: a capped run burned ~130
    calls on main.ts errors it was forbidden from touching)."""

    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text(
        '// GENERATED control scaffold — never edit; gameplay lives in game.ts and its siblings.\n'
        'import { createState, init } from "./game.ts";\n'
        'export function createGame(kit: Kit): GameObject {\n'
        '  const state: any = createState(kit);\n'
        '  return { config: {}, state, init(kit) { init(state, kit); }, update(dt, input, kit) {} };\n'
        '}\n', encoding="utf-8")
    (d / "game.ts").write_text(  # exports init but NOT createState — breaks the scaffold's import
        'export function init(state: any, kit: Kit): void {}\n', encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "game.ts", "purpose": "hooks", "exports": ["createState", "init"]}]}), encoding="utf-8")
    ctx = SimpleNamespace(state=SimpleNamespace(run_dir=tmp_path), spec={"design": {}})
    errs = _detect_typechecks(None, None, ctx)
    assert errs, "expected type errors"
    assert all(e.path == "game.ts" for e in errs), [e.path for e in errs]
    joined = " ".join(e.message for e in errs)
    assert "GENERATED" in joined and "game.ts" in joined


def test_dedupe_functions_keeps_last_and_skips_overloads():
    src = (
        'export function init(state: any): void { state.v = "old { brace in string"; }\n'
        'function helper(n: number): number; // overload signature — legal, untouched\n'
        'function helper(n: any): any { return n; }\n'
        'export function init(state: any): void { state.v = 2; }\n')
    out, removed = dedupe_functions(src)
    assert removed == ["init"]
    assert out.count("function init") == 1
    assert "state.v = 2" in out and "old { brace" not in out
    assert out.count("function helper") == 2   # overload pair intact


def test_dedupe_decls_routes_and_rewrites(tmp_path):
    d = tmp_path / "game"
    d.mkdir()
    (d / "game.ts").write_text(
        "export function init(s: any): void { s.a = 1; }\n"
        "export function init(s: any): void { s.a = 2; }\n", encoding="utf-8")
    err = Error(type=ErrorType.FIX, code="typechecks", component="game", path="game.ts",
                message="game.ts has 2 type error(s):\n  - line 1: error TS2393: Duplicate function implementation.")
    cls = classify(err)
    assert cls.id == "duplicate-decl"
    res = cls.deterministic(tmp_path, err)
    assert res["count"] == 1
    body = (d / "game.ts").read_text()
    assert body.count("function init") == 1 and "s.a = 2" in body
