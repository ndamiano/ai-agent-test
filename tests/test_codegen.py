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

PONG = (RUNTIME_DIR / "games" / "pong.js").read_text(encoding="utf-8")
BROKEN = "export function createGame(kit){ return { init(){ throw new Error('boom'); }, update(){}, config:{} }; }"
# Passes headless + probe (moves on 'd'), but draw() calls a raw-canvas method the kit's draw api
# lacks — a browser crash the sim gates can't see.
DRAW_CRASH = """export function createGame(kit){ return {
  config:{width:200,height:200}, state:{world:[]},
  init(kit){ this.state.p = kit.spawn(this.state.world,{x:50,y:50,w:10,h:10}); },
  update(dt,input,kit){ if(input.down('d')) this.state.p.x += 150*dt; },
  draw(g){ g.save(); } }; }"""
# Passes headless + probe, but draw() paints nothing — a blank screen.
DRAW_BLANK = """export function createGame(kit){ return {
  config:{width:200,height:200}, state:{world:[]},
  init(kit){ this.state.p = kit.spawn(this.state.world,{x:50,y:50,w:10,h:10}); },
  update(dt,input,kit){ if(input.down('d')) this.state.p.x += 150*dt; },
  draw(g){} }; }"""
# A 3D game has no draw() — render smoke skips it (render is mesh-sync from shape tags).
GAME_3D = """export function createGame(kit){ return {
  config:{mode:"3d",width:200,height:200}, state:{world:[]},
  init(kit){ this.state.world.push({shape:"box",x:0,y:0,z:0,w:1,h:1,d:1,color:"#f00"}); },
  update(dt,input,kit){} }; }"""
# A level far wider than the screen with the player driven right but NO camera — the far level is
# off-screen. Passes headless/probe/render; only the scroll gate catches it.
WIDE_NO_CAMERA = """export function createGame(kit){ return {
  config:{width:320,height:240}, state:{world:[]},
  init(kit){ this.state.p = kit.spawn(this.state.world,{x:0,y:100,w:10,h:10}); },
  update(dt,input,kit){ if(input.down('d')||input.down('ArrowRight')) this.state.p.x += 300*dt; },
  draw(g){ g.rect(this.state.p.x, this.state.p.y, 10, 10, '#fff'); } }; }"""
# Same wide world, but a follow camera pans with the player (world-space draw under push/pop).
WIDE_WITH_CAMERA = """export function createGame(kit){ return {
  config:{width:320,height:240}, state:{world:[]},
  init(kit){ this.state.p = kit.spawn(this.state.world,{x:0,y:100,w:10,h:10}); this.cam = kit.makeCamera(); },
  update(dt,input,kit){ if(input.down('d')||input.down('ArrowRight')) this.state.p.x += 300*dt; this.cam.follow(this.state.p, 4000, 240); },
  draw(g){ g.push(this.cam); g.rect(this.state.p.x, this.state.p.y, 10, 10, '#fff'); g.pop(); } }; }"""


def _run_dir(tmp_path) -> RunState:
    return RunState(tmp_path)


def _write_game(tmp_path, code, extra=None):
    """Write a game folder (game/main.js + a manifest naming it) so `planned`/`authored` pass and
    the gate checks run. `extra` = {name: src} for extra system files."""
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "main.js").write_text(code, encoding="utf-8")
    files = [{"name": "main.js", "purpose": "game", "exports": ["createGame"]}]
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
SEEK_GAME = """export function createGame(kit){ return {
  config:{width:300,height:300}, state:{world:[]},
  init(kit){ this.state.player={x:250,y:250,w:8,h:8}; this.e=kit.spawn(this.state.world,{x:0,y:0,w:8,h:8}); },
  update(dt,input,kit){ const d=kit.seek(this.e,this.state.player,120); kit.integrate(this.e,dt); if(d<10) kit.win("caught"); },
  draw(g){ g.rect(this.state.player.x,this.state.player.y,8,8,'#0f0'); g.rect(this.e.x,this.e.y,8,8,'#f00'); } }; }"""


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


def test_run_headless_green_on_pong(tmp_path):
    _write_game(tmp_path, PONG)
    assert run_headless(_run_dir(tmp_path).run_dir).get("ok") is True


def test_run_headless_reports_missing(tmp_path):
    hl = run_headless(tmp_path)
    assert hl["ok"] is False and hl["phase"] == "missing"


def test_run_headless_catches_crash(tmp_path):
    _write_game(tmp_path, BROKEN)
    hl = run_headless(tmp_path)
    assert hl["ok"] is False and "boom" in hl.get("error", "")


def test_run_probe_green_on_pong(tmp_path):
    _write_game(tmp_path, PONG)
    assert run_probe(tmp_path).get("ok") is True


def test_run_render_green_on_pong(tmp_path):
    _write_game(tmp_path, PONG)
    assert run_render(tmp_path).get("ok") is True


def test_run_render_catches_draw_crash(tmp_path):
    _write_game(tmp_path, DRAW_CRASH)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "draw_crash"


def test_run_render_catches_blank_screen(tmp_path):
    _write_game(tmp_path, DRAW_BLANK)
    rr = run_render(tmp_path)
    assert rr["ok"] is False and rr["violations"][0]["kind"] == "draw_blank"


def test_run_render_skips_3d(tmp_path):
    _write_game(tmp_path, GAME_3D)
    rr = run_render(tmp_path)
    assert rr["ok"] is True and rr.get("skipped") is True


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
    _write_game(tmp_path, PONG)
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
    assert tools["write_game_file"](code=PONG)["ok"] is True
    assert tools["read_game_file"]()["content"] == PONG


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


def test_authored_error_per_missing_manifest_file(tmp_path):
    # with a manifest but no files on disk, one `authored` error per missing file (bounded authoring).
    d = tmp_path / "game"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "main.js", "purpose": "entry", "exports": ["createGame"]},
        {"name": "combat.js", "purpose": "combat", "exports": ["attack"]}]}))
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert {e.code for e in errs} == {"authored"}
    assert {e.path for e in errs} == {"main.js", "combat.js"}


def test_clean_game_has_no_errors(tmp_path):
    _write_game(tmp_path, PONG)
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
    conn = _FakeConn(PONG)
    loop = AgentLoop(spec, state, [CodegenModule()], build_codegen_tools(state),
                     connector=conn, max_steps=10)
    result = loop.run()
    assert result.ok is True
    assert conn.calls >= 1
    assert result.steps >= 1   # the fix must report so max_steps actually bounds the loop
    assert (tmp_path / "game" / "main.js").read_text().strip() == PONG.strip()


def test_multi_file_game_loads_and_gates(tmp_path):
    # a game split across files (main.js imports a system file) loads as a module graph and gates.
    main = ("import { movePaddle } from './paddle.js';\n"
            "export function createGame(kit){ return {\n"
            "  config:{width:320,height:240,seed:1}, state:{world:[]},\n"
            "  init(kit){ this.state.p = kit.spawn(this.state.world,{x:20,y:100,w:10,h:40}); },\n"
            "  update(dt,input,kit){ movePaddle(this.state.p, input, dt); },\n"
            "  draw(g){ g.rect(this.state.p.x, this.state.p.y, 10, 40, '#fff'); } }; }")
    paddle = ("export function movePaddle(p, input, dt){ if(input.down('w'))p.y-=200*dt; "
              "if(input.down('s'))p.y+=200*dt; }")
    _write_game(tmp_path, main, extra={"paddle.js": paddle})
    assert run_headless(tmp_path).get("ok") is True
    assert CodegenModule().get_errors(_ctx(_run_dir(tmp_path))) == []


def test_loop_refuses_unfrozen_spec(tmp_path):
    state = _run_dir(tmp_path)
    spec = {"frozen": False, "mode": "2d", "design": {}}
    state.write_spec(spec)
    loop = AgentLoop(spec, state, [CodegenModule()], build_codegen_tools(state),
                     connector=_FakeConn(PONG), max_steps=5)
    with pytest.raises(RuntimeError):
        loop.run()
