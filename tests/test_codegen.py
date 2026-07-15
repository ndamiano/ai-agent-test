"""Codegen build path: gates, module checks, and the AgentLoop driving a game to passing.

The kit + node runners are real (the gates shell out to runtime/*.mjs); the LLM is stubbed so the
loop test is deterministic and needs no live model.
"""

import json
from pathlib import Path

import pytest

from maestro.agent_loop import AgentLoop
from maestro.codegen.gates import (
    RUNTIME_DIR, extract_code, run_headless, run_probe, run_render, run_scroll,
)
from maestro.codegen.module import CodegenModule
from maestro.codegen.tools import build_codegen_tools
from maestro.modules.module import ErrorType
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
    draw(g) { g.clear("#000"); g.rect(this.state.p.x, this.state.p.y, 10, 10, "#fff"); },
  };
}"""
# init throws — a runtime headless crash.
BROKEN = """export function createGame(kit: Kit): GameObject {
  return { config: {}, state: {} as any, init(kit) { throw new Error("boom"); }, update(dt, input, kit) {} };
}"""
# Passes headless + probe (moves on 'd'), but draw() throws at RUNTIME (not a type error) — a browser
# crash the sim gates can't see.
DRAW_CRASH = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200 }, state: { world: [] as World, p: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 50, y: 50, w: 10, h: 10 }); },
    update(dt, input, kit) { if (input.down("d")) kit.walk(this.state.p, 1, 150); else kit.walk(this.state.p, 0, 150); kit.integrate(this.state.p, dt); },
    draw(g) { const z: any = null; z.nope(); },
  };
}"""
# Passes headless + probe, but draw() paints nothing — a blank screen.
DRAW_BLANK = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200 }, state: { world: [] as World, p: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 50, y: 50, w: 10, h: 10 }); },
    update(dt, input, kit) { if (input.down("d")) kit.walk(this.state.p, 1, 150); else kit.walk(this.state.p, 0, 150); kit.integrate(this.state.p, dt); },
    draw(g) {},
  };
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
    draw(g) { g.fillRect(10, 10, 50, 20); },
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
    draw(g) { g.rect(this.state.p.x, this.state.p.y, 10, 10, "#fff"); },
  };
}"""
# Same wide world, but a follow camera pans with the player.
WIDE_WITH_CAMERA = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 320, height: 240 }, state: { world: [] as World, p: null as any, cam: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 0, y: 100, w: 10, h: 10 }); this.state.cam = kit.makeCamera(); },
    update(dt, input, kit) { if (input.down("d") || input.down("ArrowRight")) this.state.p.x += 300 * dt; this.state.cam.follow(this.state.p, 4000, 240); },
    draw(g) { g.push(this.state.cam); g.rect(this.state.p.x, this.state.p.y, 10, 10, "#fff"); g.pop(); },
  };
}"""
# A deliberate TYPE error — caught by the typecheck gate before the game ever runs.
TYPE_ERROR = """export function createGame(kit: Kit): GameObject {
  const n: number = "not a number";
  return { config: {}, state: { n } as any, update(dt, input, kit) {} };
}"""


def _run_dir(tmp_path) -> RunState:
    return RunState(tmp_path)


def _write_game(tmp_path, code, extra=None):
    """Write a game folder (game/main.ts + a manifest naming it) so `planned`/`authored` pass and the
    gate checks run. `extra` = {name: src} for extra system files."""
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "main.ts").write_text(code, encoding="utf-8")
    files = [{"name": "main.ts", "purpose": "game", "exports": ["createGame"]}]
    for name, src in (extra or {}).items():
        (d / name).write_text(src, encoding="utf-8")
        files.append({"name": name, "purpose": "", "exports": []})
    (d / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")


def _node_eval(js: str) -> dict:
    """Run an inline ES-module snippet against the kit and parse its JSON stdout. Used to unit-test
    kit primitives that need driven input a self-contained headless game can't produce."""
    import subprocess
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
    draw(g) { g.rect(this.state.player.x, this.state.player.y, 8, 8, "#0f0"); g.rect(this.state.e.x, this.state.e.y, 8, 8, "#f00"); },
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


def test_run_probe_green_on_pong(tmp_path):
    _write_game(tmp_path, GOOD)
    assert run_probe(tmp_path).get("ok") is True


def test_run_render_green_on_pong(tmp_path):
    _write_game(tmp_path, GOOD)
    assert run_render(tmp_path).get("ok") is True


def test_run_render_catches_draw_crash(tmp_path):
    _write_game(tmp_path, DRAW_CRASH)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "draw_crash"


def test_run_render_catches_blank_screen(tmp_path):
    _write_game(tmp_path, DRAW_BLANK)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "draw_blank"


def test_run_render_skips_3d_without_draw(tmp_path):
    _write_game(tmp_path, GAME_3D)
    rr = run_render(tmp_path)
    assert rr["ok"] is True and rr.get("skipped") is True


def test_run_render_runs_3d_hud(tmp_path):
    # a 3D game's HUD is data returned from hud() — the render gate validates + renders it, not skips.
    _write_game(tmp_path, HUD_3D)
    rr = run_render(tmp_path)
    assert rr.get("ok") is True and not rr.get("skipped")


def test_run_render_rejects_draw_in_3d(tmp_path):
    # the shipped bug's vector: a 3D game with draw() (a g.clear there blanks the scene). The render
    # gate now rejects any draw() in a 3D game — the whole occlusion class is impossible.
    _write_game(tmp_path, HUD_DRAW_IN_3D)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "draw_in_3d"


def test_run_render_catches_bad_hud_data(tmp_path):
    # hud() returning a malformed item is caught by the gate's declarative validation.
    _write_game(tmp_path, HUD_BAD_DATA)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "hud_bad"


def test_draw_crash_passes_headless_and_probe_but_not_render(tmp_path):
    # the whole point of the render gate: the sim gates alone give a false green here.
    _write_game(tmp_path, DRAW_CRASH)
    assert run_headless(tmp_path).get("ok") is True
    assert run_probe(tmp_path).get("ok") is True
    assert run_render(tmp_path).get("ok") is False


def test_run_scroll_flags_wide_world_without_camera(tmp_path):
    _write_game(tmp_path, WIDE_NO_CAMERA)
    sr = run_scroll(tmp_path)
    assert sr["ok"] is False and sr["violations"][0]["kind"] == "no_camera"


def test_run_scroll_passes_wide_world_with_camera(tmp_path):
    _write_game(tmp_path, WIDE_WITH_CAMERA)
    assert run_scroll(tmp_path).get("ok") is True


def test_run_scroll_ignores_confined_game(tmp_path):
    # pong never leaves one screen — the scroll gate must not demand a camera.
    _write_game(tmp_path, GOOD)
    assert run_scroll(tmp_path).get("ok") is True


def test_wide_no_camera_passes_sim_gates_but_not_scroll(tmp_path):
    _write_game(tmp_path, WIDE_NO_CAMERA)
    assert run_headless(tmp_path).get("ok") is True
    assert run_probe(tmp_path).get("ok") is True
    assert run_render(tmp_path).get("ok") is True
    assert run_scroll(tmp_path).get("ok") is False


def test_extract_code_pulls_fenced_block():
    assert extract_code("blah\n```js\nconst x = 1;\n```\ntrailing") == "const x = 1;"


# ── tools ─────────────────────────────────────────────────────────────────────
def test_write_then_read_game_file(tmp_path):
    tools = build_codegen_tools(_run_dir(tmp_path))
    assert tools["write_game_file"](code=GOOD)["ok"] is True
    assert tools["read_game_file"]()["content"] == GOOD


def test_write_rejects_empty(tmp_path):
    tools = build_codegen_tools(_run_dir(tmp_path))
    assert tools["write_game_file"](code="  ")["ok"] is False


# ── module checks ─────────────────────────────────────────────────────────────
def _ctx(state):
    from maestro.modules.context import build_context
    return build_context({"mode": "2d", "design": {}}, state)


def test_planned_error_when_empty(tmp_path):
    # an empty run reports exactly one thing: plan the manifest (blocking, suppresses all later checks).
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["planned"]
    assert errs[0].type is ErrorType.BUILD


def test_authored_in_dependency_order_contract_first_entry_last(tmp_path):
    # authoring is ONE file at a time in DEPENDENCY order: the shared-types file FIRST (consumers
    # author against real types), the entry main.ts LAST (it wires every system, so it binds against
    # its siblings' real on-disk signatures) — even though the manifest lists main.ts second.
    from maestro.codegen.module import _authoring_order
    d = tmp_path / "game"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "types.ts", "purpose": "shared interfaces", "exports": ["GameState"]},
        {"name": "main.ts", "purpose": "entry", "exports": ["createGame"]},
        {"name": "combat.ts", "purpose": "combat", "exports": ["attack"]}]}))
    order = [f["name"] for f in _authoring_order(_run_dir(tmp_path).run_dir)]
    assert order == ["types.ts", "combat.ts", "main.ts"]   # contract first, systems, entry LAST
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["authored"]
    assert [e.path for e in errs] == ["types.ts"]   # first to author


def test_typecheck_gate_catches_type_error(tmp_path):
    # a type error (string assigned to number) is caught by tsc, tagged with the file, BEFORE the run.
    _write_game(tmp_path, TYPE_ERROR)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["typechecks"]
    assert errs[0].path == "main.ts" and "TS" in errs[0].message


def test_typecheck_catches_cross_file_shape_mismatch(tmp_path):
    # THE motivating bug: world.ts returns a boolean grid, main.ts consumes a char grid — tsc catches
    # the disagreement across files, which no runtime gate could.
    world = "export function makeMap(): boolean[][] { return [[false]]; }"
    main = ("import { makeMap } from './world.ts';\n"
            "export function createGame(kit: Kit): GameObject {\n"
            "  return { config: {}, state: {} as any,\n"
            "    init(kit) { const m: string[][] = makeMap(); this.state.m = m; },\n"
            "    update(dt, input, kit) {} }; }")
    _write_game(tmp_path, main, extra={"world.ts": world})
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["typechecks"]
    assert errs[0].path == "main.ts"


def test_typecheck_catches_canvas_api_on_drawapi(tmp_path):
    # the compile-time catch for the shipped bug: when createGame is annotated `: GameObject`, draw's
    # `g` is CONTEXTUALLY a DrawApi, so g.fillRect (not on DrawApi) is a type error before the run.
    # This bites only if the model leaves g unannotated — an explicit `g: any` defeats it, which is
    # why the render gate is the reliable backstop.
    _write_game(tmp_path, CANVAS_MISUSE_2D)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["typechecks"]
    assert "fillRect" in errs[0].message


def test_clean_game_has_no_errors(tmp_path):
    _write_game(tmp_path, GOOD)
    assert CodegenModule().get_errors(_ctx(_run_dir(tmp_path))) == []


def test_runs_error_on_crash(tmp_path):
    _write_game(tmp_path, BROKEN)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["runs"]
    assert errs[0].type is ErrorType.FIX


def test_renders_error_when_draw_crashes(tmp_path):
    # authored + runs + plays are clean; only the render gate fires.
    _write_game(tmp_path, DRAW_CRASH)
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
        self.calls += 1
        return {"choices": [{"message": {"content": f"```js\n{self.code}\n```"}}]}


def test_loop_authors_until_gates_pass(tmp_path):
    state = _run_dir(tmp_path)
    spec = {"frozen": True, "mode": "2d", "title": "Pong", "design": {"title": "Pong"}}
    state.write_spec(spec)
    conn = _FakeConn(GOOD)
    loop = AgentLoop(spec, state, [CodegenModule()], build_codegen_tools(state),
                     connector=conn, max_steps=10)
    result = loop.run()
    assert result.ok is True
    assert conn.calls >= 1
    assert result.steps >= 1   # the fix must report so max_steps actually bounds the loop
    assert (tmp_path / "game" / "main.ts").read_text().strip() == GOOD.strip()


def test_multi_file_game_loads_and_gates(tmp_path):
    # a game split across files (main.ts imports a typed system file) type-checks, bundles, and runs.
    main = ("import { movePaddle } from './paddle.ts';\n"
            "export function createGame(kit: Kit): GameObject {\n"
            "  return {\n"
            "    config: { width: 320, height: 240, seed: 1 }, state: { world: [] as World, p: null as any },\n"
            "    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 20, y: 100, w: 10, h: 40 }); },\n"
            "    update(dt, input, kit) { movePaddle(this.state.p, input, dt); },\n"
            "    draw(g) { g.rect(this.state.p.x, this.state.p.y, 10, 40, '#fff'); } }; }")
    paddle = ("export function movePaddle(p: Entity, input: Input, dt: number): void {\n"
              "  if (input.down('w')) p.y -= 200 * dt; if (input.down('s')) p.y += 200 * dt; }")
    _write_game(tmp_path, main, extra={"paddle.ts": paddle})
    assert run_headless(tmp_path).get("ok") is True
    assert CodegenModule().get_errors(_ctx(_run_dir(tmp_path))) == []


class _FakeToolConn:
    """Scripts the read→write subloop: call 1 reads a file, call 2 writes GOOD to main.ts. Records the
    tool calls so a test can assert the fix READ before it WROTE (the cross-file capability)."""
    def __init__(self, code, target="main.ts"):
        self.code = code
        self.target = target
        self.calls = []

    def generate_with_tools(self, messages, tools=None, **kw):
        step = len(self.calls)
        if step == 0:
            tc = {"id": "c0", "type": "function",
                  "function": {"name": "read_game_file", "arguments": json.dumps({"file": self.target})}}
        else:
            tc = {"id": f"c{step}", "type": "function",
                  "function": {"name": "write_game_file",
                               "arguments": json.dumps({"file": self.target, "code": self.code})}}
        self.calls.append(tc["function"]["name"])
        return {"choices": [{"message": {"content": "", "tool_calls": [tc]}}]}


def test_fix_subloop_reads_then_writes_to_green(tmp_path):
    # A game that crashes headless (init throws). The fix subloop must read, then write a good file;
    # the outer loop re-gates to green.
    state = _run_dir(tmp_path)
    _write_game(tmp_path, BROKEN)
    spec = {"frozen": True, "mode": "2d", "title": "T", "design": {"title": "T"}}
    state.write_spec(spec)
    conn = _FakeToolConn(GOOD)
    loop = AgentLoop(spec, state, [CodegenModule()], build_codegen_tools(state),
                     connector=conn, max_steps=15)
    result = loop.run()
    assert result.ok is True
    assert "read_game_file" in conn.calls and "write_game_file" in conn.calls
    assert conn.calls.index("read_game_file") < conn.calls.index("write_game_file")
    assert (tmp_path / "game" / "main.ts").read_text().strip() == GOOD.strip()


def test_loop_refuses_unfrozen_spec(tmp_path):
    state = _run_dir(tmp_path)
    spec = {"frozen": False, "mode": "2d", "design": {}}
    state.write_spec(spec)
    loop = AgentLoop(spec, state, [CodegenModule()], build_codegen_tools(state),
                     connector=_FakeConn(GOOD), max_steps=5)
    with pytest.raises(RuntimeError):
        loop.run()


# ── context assembly (the fixes that converge multi-file builds) ────────────────
def test_sibling_sigs_capture_full_multiline_params(tmp_path):
    """A multi-line function signature must expose ALL its parameters — truncating at the first line
    drops the arg list, and a caller can't match the arg count (the arg-count oscillation bug)."""
    from maestro.codegen.module import _sibling_lines
    combat = ("import { Entity, GameState } from './types';\n"
              "export function applyDamage(\n  target: Entity,\n  amount: number,\n"
              "  state: GameState,\n  kit: any\n): void {\n  target.hp -= amount;\n}\n")
    _write_game(tmp_path, GOOD, extra={"combat.ts": combat})
    sigs = _sibling_lines(_run_dir(tmp_path).run_dir, exclude="main.ts")
    assert "applyDamage" in sigs
    for param in ("target: Entity", "amount: number", "state: GameState", "kit: any"):
        assert param in sigs, f"missing {param!r} — signature was truncated"


def test_kit_context_dropped_for_pure_typecheck_fix():
    """A type/contract typecheck fix gets NO kit doc (noise); a runtime gate gets the full doc; an
    arg-count typecheck fix gets just the signatures."""
    from maestro.codegen.module import _kit_context
    from maestro.modules.module import Error, ErrorType
    spec = {"mode": "3d"}
    pure = Error(type=ErrorType.FIX, code="typechecks", component="game",
                 message="types.ts has 1 type error(s):\n  - line 3: error TS2305: no exported member 'X'")
    assert _kit_context(spec, pure) == ""
    argc = Error(type=ErrorType.FIX, code="typechecks", component="game",
                 message="main.ts: error TS2554: Expected 4 arguments, but got 3.")
    assert "KIT CALL SIGNATURES" in _kit_context(spec, argc)
    runtime = Error(type=ErrorType.FIX, code="runs", component="game", message="HEADLESS FAILED")
    assert "# KIT API" in _kit_context(spec, runtime)
