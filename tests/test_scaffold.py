"""Control scaffold: the pipeline seeds a GENERATED main.ts from the spec's control scheme; the
model authors gameplay behind the game.ts hooks. Covers: per-scheme rendering, idempotence, the
world exclusion, the state.player contract throw, the planned/authoring integration, and the
scheme-aware probe (dead_movement) — the invariant the two shipped dead-control games motivated."""

import json

import pytest

from maestro.codegen.gates import run_headless, run_probe, run_render, run_scroll, typecheck
from maestro.codegen.module import CodegenModule, _authoring_order, _plan_entry
from maestro.codegen.scaffold import ENTRY_HOOK, is_scaffolded, seed_scaffold
from maestro.state import RunState

# A working top-down hook module: player spawned into state.world in init (the scaffold contract),
# space mutates non-positional state (an action key), scene painted in draw.
GAME_TS = """export interface GameState { world: World; player: Entity | null; score: number; }
export function createState(kit: Kit): GameState { return { world: [], player: null, score: 0 }; }
export function init(state: GameState, kit: Kit): void {
  state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff" });
}
export function update(state: GameState, dt: number, input: Input, kit: Kit): void {
  if (input.pressed(" ")) state.score += 1;
}
export function draw(g: DrawApi, state: GameState, kit: Kit): void {
  g.clear("#000");
  g.rect(state.player.x, state.player.y, 10, 10, "#fff");
}
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud };
"""
# THE shipped incident's shape: movement wired (by the scaffold) but a hand-rolled "collision"
# undoes it every frame, while an action key still mutates state — dead_controls stays green.
GAME_TS_SABOTAGED = GAME_TS.replace(
    'if (input.pressed(" ")) state.score += 1;',
    'state.player.x = 100; state.player.y = 100;\n  if (input.pressed(" ")) state.score += 1;')
# Hooks that forget the state.player contract — the scaffold's init assert must throw.
GAME_TS_NO_PLAYER = """export interface GameState { world: World; }
export function createState(kit: Kit): GameState { return { world: [] }; }
export function init(state: GameState, kit: Kit): void { }
export function update(state: GameState, dt: number, input: Input, kit: Kit): void { }
export function draw(g: DrawApi, state: GameState, kit: Kit): void { g.rect(0, 0, 5, 5, "#fff"); }
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud };
"""
# THE two-incident fixture: tilemap walls + enemies spawned STACKED, everything tagged solid, plus a
# spec-bound attack registered in init. The scaffold's collideWorld pass must resolve both (walls
# hold, the stack separates) and the registered action must satisfy the probe's action invariants.
GAME_TS_SOLID = """export interface GameState { world: World; player: Entity | null; tilemap: Tilemap | null; score: number; }
export function createState(kit: Kit): GameState { return { world: [], player: null, tilemap: null, score: 0 }; }
export function init(state: GameState, kit: Kit): void {
  state.tilemap = kit.makeTilemap([
    "####################",
    "#                  #",
    "#                  #",
    "#                  #",
    "#                  #",
    "#                  #",
    "#                  #",
    "####################",
  ], 32, "#");
  state.player = kit.spawn(state.world, { x: 100, y: 100, w: 20, h: 20, solid: true, color: "#fff" });
  kit.spawn(state.world, { x: 200, y: 100, w: 20, h: 20, solid: true, type: "slime", color: "#0f0" });
  kit.spawn(state.world, { x: 204, y: 104, w: 20, h: 20, solid: true, type: "slime", color: "#0f0" });
  kit.register("attack", ["f"], () => { state.score += 1; });
}
export function update(state: GameState, dt: number, input: Input, kit: Kit): void { }
export function draw(g: DrawApi, state: GameState, kit: Kit): void {
  g.clear("#000");
  for (const e of state.world) g.rect(e.x, e.y, e.w, e.h, e.color ?? "#999");
}
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud };
"""
# A talker NPC in interact range — the dialogue scaffold's registered "interact" must open it.
GAME_TS_TALK = """export interface GameState { world: World; player: Entity | null; }
export function createState(kit: Kit): GameState { return { world: [], player: null }; }
export function init(state: GameState, kit: Kit): void {
  state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff" });
  kit.spawn(state.world, { x: 130, y: 100, w: 10, h: 10, color: "#fa0",
    talk: { name: "Elder", lines: ["Hello."] } });
}
export function update(state: GameState, dt: number, input: Input, kit: Kit): void { }
export function draw(g: DrawApi, state: GameState, kit: Kit): void {
  g.clear("#000");
  g.rect(state.player.x, state.player.y, 10, 10, "#fff");
}
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud };
"""
# A 3D hook module — no draw; the player is a shape-tagged member of state.world.
GAME_TS_3D = """export interface GameState { world: World; player: Entity | null; }
export function createState(kit: Kit): GameState { return { world: [], player: null }; }
export function init(state: GameState, kit: Kit): void {
  state.world.push({ shape: "ground", x: 0, y: 0, z: 0, size: 60, color: "#274" });
  state.player = kit.spawn(state.world, { shape: "box", x: 0, y: 0.9, z: 0, w: 0.8, h: 1.7, d: 0.8, color: "#28303a" });
}
export function update(state: GameState, dt: number, input: Input, kit: Kit): void { }
export function hud(state: GameState, kit: Kit): HudItem[] { return [{ kind: "text", text: "hi", at: "top-left" }]; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
"""


def _spec(scheme, mode="2d", uses=None, world=None):
    return {"frozen": True, "mode": mode, "world": world,
            "design": {"title": "T", "control": {"entity": "player", "scheme": scheme},
                       "uses": uses or []}}


def _seed(tmp_path, spec):
    seed_scaffold(RunState(tmp_path), spec)
    return tmp_path / "game" / "main.ts"


def _hook_game(tmp_path, spec, game_ts):
    """A scaffolded run with a real hook module + manifest + empty data design — gate-ready."""
    _seed(tmp_path, spec)
    d = tmp_path / "game"
    (d / "game.ts").write_text(game_ts, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "game.ts", "purpose": "the whole game behind the scaffold hooks",
         "exports": ["createState", "init", "update", "draw", "hud"]}]}), encoding="utf-8")
    (d / "data").mkdir(exist_ok=True)
    (d / "data" / "manifest.json").write_text(json.dumps({"datasets": []}), encoding="utf-8")


# ── template rendering ────────────────────────────────────────────────────────
@pytest.mark.parametrize("scheme,movement,config_bits", [
    ("top-down", "kit.moveTopDown(state.player", ['mode: "2d"', "width: 960", "height: 540"]),
    ("platformer", "kit.physics(state.player", ['mode: "2d"', "kit.walk(state.player", "kit.jump(state.player"]),
    ("grid-turn", "kit.gridMove(state.player", ['mode: "2d"', "state.passable"]),
    ("orbital-3d", "kit.drive(state.player", ['mode: "3d"', 'controls: "orbital"']),
    ("vehicle-3d", "kit.drive(state.player", ['mode: "3d"', 'controls: "vehicle"']),
    ("first-person-3d", "kit.drive(state.player", ['mode: "3d"', 'controls: "fp"']),
    ("follow-3d", "kit.drive(state.player", ['mode: "3d"', 'controls: "follow"']),
])
def test_scaffold_renders_each_scheme(tmp_path, scheme, movement, config_bits):
    src = _seed(tmp_path, _spec(scheme, mode="3d" if scheme.endswith("-3d") else "2d")).read_text()
    assert src.splitlines()[0].startswith("// GENERATED control scaffold")
    assert movement in src
    for bit in config_bits:
        assert bit in src
    assert "{interact}" not in src           # placeholder consumed
    assert 'from "./game.ts"' in src         # hooks imported — tsc enforces the contract
    assert is_scaffolded(tmp_path)
    if scheme.endswith("-3d"):
        assert "draw(" not in src.split("export function createGame")[1]   # 3D scaffold has NO draw hook


def test_unknown_scheme_gets_the_default_scaffold(tmp_path):
    src = _seed(tmp_path, _spec("mouse-aim")).read_text()
    assert src.splitlines()[0].startswith("// GENERATED control scaffold")
    assert "kit.moveTopDown" not in src and "kit.drive(" not in src   # no movement wired
    assert is_scaffolded(tmp_path)


def test_seed_is_idempotent(tmp_path):
    main = _seed(tmp_path, _spec("top-down"))
    first = main.read_text()
    seed_scaffold(RunState(tmp_path), _spec("platformer"))   # re-entry, even with a drifted spec
    assert main.read_text() == first


def test_seed_never_clobbers_an_existing_main(tmp_path):
    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text("export function createGame(kit: Kit) {}\n", encoding="utf-8")
    seed_scaffold(RunState(tmp_path), _spec("top-down"))
    assert (d / "main.ts").read_text() == "export function createGame(kit: Kit) {}\n"
    assert not is_scaffolded(tmp_path)


def test_world_flagged_spec_skips_scaffold(tmp_path):
    # v1 scope: worldgen owns world games' main.ts (it already prescribes the drive wiring).
    seed_scaffold(RunState(tmp_path), _spec("first-person-3d", mode="3d", world={"settlement": "X"}))
    assert not (tmp_path / "game" / "main.ts").exists()


def test_dialogue_use_adds_the_interact_block(tmp_path):
    src = _seed(tmp_path, _spec("top-down", uses=["dialogue"])).read_text()
    assert "kit.talkOpen" in src and "kit.talkStep" in src and "state.talkPick" in src
    assert "{interact}" not in src


def test_no_dialogue_use_no_interact_block(tmp_path):
    src = _seed(tmp_path, _spec("top-down", uses=["tilemap", "particles"])).read_text()
    assert "kit.talkOpen" not in src


# ── the hook contract at the gates ────────────────────────────────────────────
def test_scaffolded_game_passes_all_gates(tmp_path):
    # The scaffold itself must be gate-clean: a minimal hook game goes typecheck → headless →
    # probe (scheme-aware) → render → scroll green with zero fixes.
    _hook_game(tmp_path, _spec("top-down"), GAME_TS)
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True
    assert run_probe(tmp_path, scheme="top-down").get("ok") is True
    assert run_render(tmp_path).get("ok") is True
    assert run_scroll(tmp_path).get("ok") is True
    errs = CodegenModule().get_errors(_ctx(tmp_path, _spec("top-down")))
    assert errs == []


def test_scaffolded_3d_game_passes_gates(tmp_path):
    _hook_game(tmp_path, _spec("orbital-3d", mode="3d"), GAME_TS_3D)
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True
    assert run_probe(tmp_path, scheme="orbital-3d").get("ok") is True
    assert run_render(tmp_path).get("ok") is True   # no draw in 3D — hud only


def test_missing_player_throws_the_contract_error(tmp_path):
    # init asserts the hook contract; the headless gate surfaces the named throw at phase init.
    _hook_game(tmp_path, _spec("top-down"), GAME_TS_NO_PLAYER)
    hl = run_headless(tmp_path)
    assert hl["ok"] is False and hl["phase"] == "init"
    assert "state.player" in hl["error"] and "game.ts" in hl["error"]


# ── solid collision through the scaffold ──────────────────────────────────────
@pytest.mark.parametrize("scheme,expect", [
    ("top-down", True), ("platformer", True), ("grid-turn", True),
    ("mouse-aim", False),                       # default template: no movement, no collide pass
    ("orbital-3d", False), ("follow-3d", False),  # 3D: no solid primitive exists (documented law)
])
def test_scaffold_collide_world_call_per_template(tmp_path, scheme, expect):
    src = _seed(tmp_path, _spec(scheme, mode="3d" if scheme.endswith("-3d") else "2d")).read_text()
    assert ("kit.collideWorld(" in src) is expect


def test_scaffolded_solid_game_passes_all_gates(tmp_path):
    # The two live incidents, resolved by the scaffold's pass: walls hold the knight, and the
    # slimes SPAWN stacked yet the probe's solid_overlap invariant stays green — the scaffold's
    # collideWorld separated them. The registered attack satisfies the action invariants (the
    # spec's controls map rides into the probe like _detect_plays passes it).
    spec = _spec("top-down")
    spec["design"]["controls"] = {"WASD": "move", "F": "attack"}
    _hook_game(tmp_path, spec, GAME_TS_SOLID)
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True
    pr = run_probe(tmp_path, scheme="top-down", control_keys=spec["design"]["controls"])
    assert pr.get("ok") is True, pr
    assert run_render(tmp_path).get("ok") is True
    assert run_scroll(tmp_path).get("ok") is True
    errs = CodegenModule().get_errors(_ctx(tmp_path, spec))
    assert errs == []


def test_scaffolded_unregistered_spec_key_fails_probe(tmp_path):
    # Negative control: strip only the kit.register — the same game now trips unbound_control for
    # the spec's F key, naming the key and the register call to add. (The solid-stack negative
    # lives at the probe level in test_codegen — the scaffold's own pass can't be defeated from
    # game.ts, which is the point.)
    spec = _spec("top-down")
    spec["design"]["controls"] = {"WASD": "move", "F": "attack"}
    bare = GAME_TS_SOLID.replace('kit.register("attack", ["f"], () => { state.score += 1; });', "")
    _hook_game(tmp_path, spec, bare)
    pr = run_probe(tmp_path, scheme="top-down", control_keys=spec["design"]["controls"])
    assert pr["ok"] is False
    kinds = [v["kind"] for v in pr["violations"]]
    assert "unbound_control" in kinds
    detail = next(v["detail"] for v in pr["violations"] if v["kind"] == "unbound_control")
    assert '"F"' in detail and "kit.register" in detail


# ── scheme-aware probe: dead_movement ─────────────────────────────────────────
def test_probe_dead_movement_catches_undone_movement(tmp_path):
    # The incident: an action key mutates state (dead_controls green) while a per-frame snap-back
    # kills all displacement. Only the scheme-aware probe sees it.
    _hook_game(tmp_path, _spec("top-down"), GAME_TS_SABOTAGED)
    assert run_probe(tmp_path).get("ok") is True   # scheme-less probe: the shipped false green
    pr = run_probe(tmp_path, scheme="top-down")
    assert pr["ok"] is False
    kinds = [v["kind"] for v in pr["violations"]]
    assert "dead_movement" in kinds and "dead_controls" not in kinds
    detail = next(v["detail"] for v in pr["violations"] if v["kind"] == "dead_movement")
    assert "top-down" in detail and "displace" in detail


def test_probe_dead_movement_passes_working_movement(tmp_path):
    _hook_game(tmp_path, _spec("top-down"), GAME_TS)
    assert run_probe(tmp_path, scheme="top-down").get("ok") is True


def test_plays_check_passes_scheme_through(tmp_path):
    # _detect_plays hands the frozen spec's scheme to the probe — the module-level wiring.
    spec = _spec("top-down")
    _hook_game(tmp_path, spec, GAME_TS_SABOTAGED)
    errs = CodegenModule().get_errors(_ctx(tmp_path, spec))
    assert [e.code for e in errs] == ["plays"]
    assert errs[0].kind == "dead_movement"


def test_plays_check_passes_control_keys_through(tmp_path):
    # _detect_plays hands the spec's whole controls map to the probe: a spec-bound action key with
    # no kit.register (GAME_TS reads bare input.pressed(" ")) comes back as unbound_control.
    spec = _spec("top-down")
    spec["design"]["controls"] = {"WASD": "move", "SPACE": "attack"}
    _hook_game(tmp_path, spec, GAME_TS)
    errs = CodegenModule().get_errors(_ctx(tmp_path, spec))
    assert [e.code for e in errs] == ["plays"]
    assert errs[0].kind == "unbound_control"
    assert '"SPACE"' in errs[0].message


def test_scaffold_dialogue_interact_satisfies_spec_key_and_probe(tmp_path):
    # The dialogue scaffold registers "interact" on E: the spec's "E: talk" key reads as wired
    # (no unbound_control) and dead_action's explicit skip exempts the proximity-gated action.
    spec = _spec("top-down", uses=["dialogue"])
    spec["design"]["controls"] = {"WASD": "move", "E": "talk to villagers"}
    _hook_game(tmp_path, spec, GAME_TS_TALK)
    main = (tmp_path / "game" / "main.ts").read_text()
    assert 'kit.register("interact", ["e"]' in main
    assert typecheck(tmp_path) == []
    pr = run_probe(tmp_path, scheme="top-down", control_keys=spec["design"]["controls"])
    assert pr.get("ok") is True, pr


# ── planned / authoring integration ───────────────────────────────────────────
def _ctx(tmp_path, spec):
    from maestro.modules.context import build_context
    return build_context(spec, RunState(tmp_path))


def _manifest(tmp_path, names):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"files": [{"name": n, "purpose": "", "exports": []} for n in names]}), encoding="utf-8")
    (d / "data").mkdir(exist_ok=True)
    (d / "data" / "manifest.json").write_text(json.dumps({"datasets": []}), encoding="utf-8")


def test_planned_requires_game_ts_for_scaffolded_runs(tmp_path):
    spec = _spec("top-down")
    _seed(tmp_path, spec)   # run_build seeds before the loop ever detects — mirror that order
    assert _plan_entry(tmp_path) == ENTRY_HOOK
    _manifest(tmp_path, ["main.ts"])   # a plan of only the scaffold's own file is no plan at all
    errs = CodegenModule().get_errors(_ctx(tmp_path, spec))
    assert [e.code for e in errs] == ["planned"]
    _manifest(tmp_path, ["game.ts"])
    errs = CodegenModule().get_errors(_ctx(tmp_path, spec))
    assert [e.code for e in errs] == ["authored"] and errs[0].path == "game.ts"


def test_planned_keeps_main_ts_for_unscaffolded_runs(tmp_path):
    # A world game (worldgen owns main.ts, no scaffold) — the plan entry stays main.ts.
    spec = _spec("first-person-3d", mode="3d", world={"settlement": "X"})
    seed_scaffold(RunState(tmp_path), spec)   # no-op for a world spec
    assert _plan_entry(tmp_path) == "main.ts"
    _manifest(tmp_path, ["main.ts"])
    errs = CodegenModule().get_errors(_ctx(tmp_path, spec))
    assert [e.code for e in errs] == ["authored"]


def test_authoring_order_puts_game_ts_last_when_scaffolded(tmp_path):
    _seed(tmp_path, _spec("top-down"))
    _manifest(tmp_path, ["game.ts", "types.ts", "combat.ts"])
    order = [f["name"] for f in _authoring_order(tmp_path)]
    assert order == ["types.ts", "combat.ts", "game.ts"]   # contract first, hook entry LAST


def test_plan_fix_fallback_and_main_ts_drop(tmp_path):
    """An unparseable plan falls back to a game.ts-only manifest for a scaffolded spec, and a plan
    that lists main.ts anyway gets it dropped (the scaffold owns main.ts)."""
    from maestro.codegen.module import _plan_fix

    class Svc:
        def __init__(self, reply):
            self.reply = reply

        def infer(self, msgs, tools, max_tokens=None):
            return {"choices": [{"message": {"content": self.reply}}]}

        def _report(self, msg):
            pass

    spec = _spec("top-down")
    _seed(tmp_path, spec)
    _plan_fix(None, _ctx(tmp_path, spec), None, 0, Svc("not json at all"), None)
    manifest = json.loads((tmp_path / "game" / "manifest.json").read_text())
    assert [f["name"] for f in manifest["files"]] == ["game.ts"]
    assert manifest["files"][0]["exports"] == ["createState", "init", "update", "draw", "hud"]

    reply = '```json\n{"files": [{"name": "main.ts", "purpose": "entry", "exports": ["createGame"]},' \
            ' {"name": "game.ts", "purpose": "hooks", "exports": ["createState"]}]}\n```'
    _plan_fix(None, _ctx(tmp_path, spec), None, 0, Svc(reply), None)
    manifest = json.loads((tmp_path / "game" / "manifest.json").read_text())
    assert [f["name"] for f in manifest["files"]] == ["game.ts"]


def test_loop_builds_scaffolded_game_to_green(tmp_path):
    """The production shape end-to-end through the AgentLoop: scaffold seeded first (as run_build
    does), then the loop plans game.ts (fallback), authors it from the model, and every gate —
    including the scheme-aware probe — passes with main.ts never model-written."""
    from maestro.agent_loop import AgentLoop
    from maestro.codegen.tools import build_codegen_tools

    class FakeConn:
        def generate_with_tools(self, messages, tools=None, **kw):
            return {"choices": [{"message": {"content": f"```ts\n{GAME_TS}\n```"}}]}

    state = RunState(tmp_path)
    spec = _spec("top-down")
    state.write_spec(spec)
    seed_scaffold(state, spec)
    scaffold_src = (tmp_path / "game" / "main.ts").read_text()
    loop = AgentLoop(spec, state, [CodegenModule()], build_codegen_tools(state),
                     connector=FakeConn(), max_steps=15)
    result = loop.run()
    assert result.ok is True
    assert (tmp_path / "game" / "game.ts").read_text().strip() == GAME_TS.strip()
    assert (tmp_path / "game" / "main.ts").read_text() == scaffold_src   # scaffold untouched


def test_plan_fix_fallback_3d_omits_draw_hook(tmp_path):
    from maestro.codegen.module import _plan_fix

    class Svc:
        def infer(self, msgs, tools, max_tokens=None):
            return {"choices": [{"message": {"content": "garbage"}}]}

        def _report(self, msg):
            pass

    spec = _spec("orbital-3d", mode="3d")
    _seed(tmp_path, spec)
    _plan_fix(None, _ctx(tmp_path, spec), None, 0, Svc(), None)
    manifest = json.loads((tmp_path / "game" / "manifest.json").read_text())
    assert manifest["files"][0]["exports"] == ["createState", "init", "update", "hud"]


# ── hook re-export bridge (run-7 regression) ────────────────────────────────────
def test_reexport_hooks_bridges_sibling_owned_hook(tmp_path):
    from maestro.codegen.scaffold import reexport_hooks
    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text("// GENERATED control scaffold — never edit\n", encoding="utf-8")
    (d / "game.ts").write_text(
        "export function init(s: any, kit: Kit): void {}\n"
        "export function update(s: any, dt: number, input: Input, kit: Kit): void {}\n"
        "export function draw(g: DrawApi, s: any, kit: Kit): void {}\n"
        "export function hud(s: any, kit: Kit): HudItem[] { return []; }\n", encoding="utf-8")
    (d / "world.ts").write_text(
        "export function createState(kit: Kit): any { return { world: [] }; }\n", encoding="utf-8")
    res = reexport_hooks(tmp_path)
    assert res["count"] == 1 and res["changes"] == [("reexport", "createState<-world.ts")]
    assert 'export { createState } from "./world.ts";' in (d / "game.ts").read_text()
    assert reexport_hooks(tmp_path)["count"] == 0   # idempotent — bridge already present


def test_reexport_hooks_skips_ambiguous_owner(tmp_path):
    from maestro.codegen.scaffold import reexport_hooks
    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text("// GENERATED control scaffold — never edit\n", encoding="utf-8")
    (d / "game.ts").write_text("export function init(s: any, kit: Kit): void {}\n", encoding="utf-8")
    (d / "a.ts").write_text("export function createState(kit: Kit): any { return {}; }\n", encoding="utf-8")
    (d / "b.ts").write_text("export const createState = (kit: Kit): any => ({});\n", encoding="utf-8")
    res = reexport_hooks(tmp_path)
    assert all(not c[1].startswith("createState") for c in res["changes"])


def test_missing_hook_classifies_and_bridges(tmp_path):
    from maestro.codegen.fix_classes import classify
    from maestro.modules.module import Error, ErrorType
    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text("// GENERATED control scaffold — never edit\n", encoding="utf-8")
    (d / "game.ts").write_text("export function init(s: any, kit: Kit): void {}\n", encoding="utf-8")
    (d / "world.ts").write_text("export function createState(kit: Kit): any { return {}; }\n", encoding="utf-8")
    err = Error(type=ErrorType.FIX, code="typechecks", component="game", path="game.ts",
                message="game.ts has 1 type error(s):\n  - line 18: error TS2305: Module '\"./game.ts\"' "
                        "has no exported member 'createState'.")
    cls = classify(err)
    assert cls.id == "missing-hook"
    assert cls.deterministic(tmp_path, err)["count"] == 1


# ── contract assertion: signature drift becomes a LOCAL game.ts error ──────────
def _assert_run(tmp_path, game_ts):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "main.ts").write_text(
        '// GENERATED control scaffold — never edit; gameplay lives in game.ts and its siblings.\n'
        'import { createState, init as initGame, update as updateGame, draw as drawGame, hud as hudGame } from "./game.ts";\n'
        'export function createGame(kit: Kit): GameObject {\n'
        '  const state: any = createState(kit);\n'
        '  return { config: { width: 100, height: 100 }, state,\n'
        '    init(kit) { initGame(state, kit); },\n'
        '    update(dt, input, kit) { updateGame(state, dt, input, kit); },\n'
        '    draw(g, kit) { drawGame(g, state, kit); },\n'
        '    hud(kit) { return hudGame(state, kit); } };\n'
        '}\n', encoding="utf-8")
    (d / "game.ts").write_text(game_ts, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "game.ts", "purpose": "hooks", "exports": ["createState", "init", "update", "draw", "hud"]}]}),
        encoding="utf-8")
    return tmp_path


_HOOKS_OK = (
    "export interface GameState { world: World; player: Entity | null; }\n"
    "export function createState(kit: Kit): GameState { return { world: [], player: null }; }\n"
    "export function init(state: GameState, kit: Kit): void { state.player = { x: 0, y: 0, w: 8, h: 8 }; }\n"
    "export function update(state: GameState, dt: number, input: Input, kit: Kit): void {}\n"
    "export function draw(g: DrawApi, state: GameState, kit: Kit): void { g.clear(\"#000\"); }\n"
    "export function hud(state: GameState, kit: Kit): HudItem[] { return []; }\n"
    "const _scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud };\n")


def test_contract_assertion_localizes_signature_drift_to_game_ts(tmp_path):
    """The run-7 terminal failure: draw's params in the wrong ORDER surfaced as an error at the
    GENERATED scaffold's call site. With the assertion, tsc reports it INSIDE game.ts."""
    from maestro.codegen.gates import typecheck
    bad = _HOOKS_OK.replace(
        "export function draw(g: DrawApi, state: GameState, kit: Kit): void",
        "export function draw(g: DrawApi, kit: Kit, state: GameState): void")
    _assert_run(tmp_path, bad)
    errs = typecheck(tmp_path)
    assert errs, "expected a type error from the swapped draw params"
    assert any(f == "game.ts" and "_scaffoldContract" not in m and "draw" in m or f == "game.ts"
               for f, m in errs), errs


def test_contract_assertion_clean_hooks_typecheck_green(tmp_path):
    from maestro.codegen.gates import typecheck
    _assert_run(tmp_path, _HOOKS_OK)
    assert typecheck(tmp_path) == []


def test_contracted_check_demands_the_assertion_line(tmp_path):
    from types import SimpleNamespace
    from maestro.codegen.module import _detect_contracted
    _assert_run(tmp_path, _HOOKS_OK.replace(
        "const _scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud };\n", ""))
    ctx = SimpleNamespace(state=SimpleNamespace(run_dir=tmp_path),
                          spec={"design": {"control": {"scheme": "top-down"}}})
    errs = _detect_contracted(None, None, ctx)
    assert len(errs) == 1 and errs[0].path == "game.ts"
    assert "_scaffoldContract: GameHooks<GameState> = { createState, init, update, draw, hud }" in errs[0].message
    _assert_run(tmp_path, _HOOKS_OK)   # with the line present the check is clean
    assert _detect_contracted(None, None, ctx) == []
