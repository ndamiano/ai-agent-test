"""Control scaffold: the pipeline seeds a GENERATED main.ts from the spec's control scheme; the
model authors gameplay behind the game.ts hooks. Covers: per-scheme rendering, the world spec's
sky background + terrain clamp, the state.player contract throw, the planned/authoring integration,
— the wiring two shipped dead-control games motivated."""

import json
from types import SimpleNamespace

import pytest

from build_harness import canned_prelude, run_build_to_completion, seed_interfaces
from maestro.codegen.data_files import generate_data_ts
from maestro.codegen.fix_classes import classify
from maestro.codegen.gates import (
    run_headless,
    run_render,
    typecheck,
)
from maestro.codegen.module import (
    CodegenModule,
    _authoring_order,
)
from maestro.codegen.scaffold import (
    _activate_keys,
    reexport_hooks,
    seed_scaffold,
)
from maestro.modules.context import build_context
from maestro.modules.module import Error, ErrorType
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
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
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
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
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
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
"""
# A talker NPC in activate range — the scaffold's registered "activate" must open it. `action` is
# what opts an entity into kit.focus at all.
GAME_TS_TALK = """export interface GameState { world: World; player: Entity | null; }
export function createState(kit: Kit): GameState { return { world: [], player: null }; }
export function init(state: GameState, kit: Kit): void {
  state.player = kit.spawn(state.world, { x: 100, y: 100, w: 10, h: 10, color: "#fff", angle: 0 });
  kit.spawn(state.world, { x: 130, y: 100, w: 10, h: 10, color: "#fa0", action: "talk",
    talk: { name: "Elder", lines: ["Hello."] } });
}
export function update(state: GameState, dt: number, input: Input, kit: Kit): void { }
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
export function onActivate(state: GameState, target: Entity, kit: Kit): void { }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud, onActivate };
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
         "exports": ["createState", "init", "update", "hud"]}]}), encoding="utf-8")
    (d / "data").mkdir(exist_ok=True)
    (d / "data" / "manifest.json").write_text(json.dumps(
        {"datasets": [{"name": "things", "fields": {"hp": "number"}}]}), encoding="utf-8")
    (d / "data" / "things.json").write_text(json.dumps(
        [{"id": "thing_a", "hp": 1}]), encoding="utf-8")
    generate_data_ts(tmp_path)      # the GENERATED main.ts imports ROWS from it
    seed_interfaces(tmp_path)


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
    assert "{activate}" not in src           # placeholder consumed
    assert 'from "./game.ts"' in src         # hooks imported — tsc enforces the contract
    if scheme.endswith("-3d"):
        assert "draw(" not in src.split("export function createGame")[1]   # 3D scaffold has NO draw hook


def test_unknown_scheme_on_a_3d_spec_falls_to_orbital_not_the_2d_default(tmp_path):
    # The default template is 2D; landing there on a 3D spec would render the game flat.
    src = _seed(tmp_path, _spec("", mode="3d")).read_text()
    assert 'mode: "3d"' in src and 'controls: "orbital"' in src


def test_unknown_scheme_gets_the_default_scaffold(tmp_path):
    src = _seed(tmp_path, _spec("mouse-aim")).read_text()
    assert src.splitlines()[0].startswith("// GENERATED control scaffold")
    assert "kit.moveTopDown" not in src and "kit.drive(" not in src   # no movement wired


@pytest.mark.parametrize("scheme", ["orbital-3d", "vehicle-3d", "first-person-3d", "follow-3d"])
def test_world_spec_is_scaffolded_under_a_sky(tmp_path, scheme):
    """A world game is scaffolded like any other — worldgen owns the PLACE, the scaffold the
    CONTROLS. Its background must be sky, not the indoor dark (config.background also tints the
    3D distance fog, so the default would fog a village grey)."""
    src = _seed(tmp_path, _spec(scheme, mode="3d", world={"settlement": "X"})).read_text()
    assert src.splitlines()[0].startswith("// GENERATED control scaffold")
    assert "kit.drive(state.player" in src
    assert 'background: "#a9c7e0"' in src
    assert "{background}" not in src


def test_world_less_3d_keeps_the_dark_background(tmp_path):
    src = _seed(tmp_path, _spec("orbital-3d", mode="3d")).read_text()
    assert 'background: "#101018"' in src


def test_dialogue_use_adds_the_activate_block(tmp_path):
    src = _seed(tmp_path, _spec("top-down", uses=["dialogue"])).read_text()
    assert "kit.talkOpen" in src and "kit.talkStep" in src and "state.talkPick" in src
    assert "{activate}" not in src


def test_no_interaction_no_activate_block(tmp_path):
    src = _seed(tmp_path, _spec("top-down", uses=["tilemap", "particles"])).read_text()
    assert "kit.talkOpen" not in src


# ── the hook contract at the gates ────────────────────────────────────────────
def test_scaffolded_game_passes_all_gates(tmp_path):
    # The scaffold itself must be gate-clean: a minimal hook game goes typecheck → headless →
    # render green with zero fixes.
    _hook_game(tmp_path, _spec("top-down"), GAME_TS)
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True
    assert run_render(tmp_path).get("ok") is True
    errs = CodegenModule().get_errors(_ctx(tmp_path, _spec("top-down")))
    assert errs == []


def test_scaffolded_3d_game_passes_gates(tmp_path):
    _hook_game(tmp_path, _spec("orbital-3d", mode="3d"), GAME_TS_3D)
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True
    assert run_render(tmp_path).get("ok") is True   # no draw in 3D — hud only


# A 3D hook module that ASSERTS the scaffold ground-clamped the player last frame: update runs
# before the clamp, so frame N sees frame N-1's result. A missing clamp throws at the headless gate.
GAME_TS_3D_GROUND = """export interface GameState { world: World; player: Entity | null;
  ground: ((x: number, z: number) => number) | null; }
export function createState(kit: Kit): GameState { return { world: [], player: null, ground: null }; }
export function init(state: GameState, kit: Kit): void {
  state.ground = (x: number, z: number) => 5 + x * 0.1 + z * 0.2;
  state.player = kit.spawn(state.world, { shape: "box", x: 3, y: 0, z: -2, w: 0.8, h: 1.7, d: 0.8, color: "#28303a" });
}
let frames = 0;
export function update(state: GameState, dt: number, input: Input, kit: Kit): void {
  frames++;
  if (frames > 1 && state.player && state.ground) {
    const want = state.ground(state.player.x, state.player.z) + 0.85;
    if (Math.abs(state.player.y - want) > 1e-9)
      throw new Error(`ground clamp missing: y=${state.player.y} want=${want}`);
  }
}
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
"""


@pytest.mark.parametrize("scheme", ["orbital-3d", "vehicle-3d", "first-person-3d", "follow-3d"])
def test_scaffold_stands_the_player_on_state_ground(tmp_path, scheme):
    """The terrain half of the world merge: game.ts hands over a height function and the scaffold
    owns the per-frame clamp, so a world game never re-implements it in update (the old worldgen
    main.ts did, by hand, in model-authored code)."""
    _hook_game(tmp_path, _spec(scheme, mode="3d"), GAME_TS_3D_GROUND)
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True


def test_no_state_ground_leaves_y_alone(tmp_path):
    # The clamp is opt-in: a 3D game that never sets state.ground keeps the y it spawned with.
    _hook_game(tmp_path, _spec("orbital-3d", mode="3d"), GAME_TS_3D.replace(
        "export function update(state: GameState, dt: number, input: Input, kit: Kit): void { }",
        "export function update(state: GameState, dt: number, input: Input, kit: Kit): void {\n"
        "  if (state.player && state.player.y !== 0.9) throw new Error(`y moved to ${state.player.y}`);\n}"))
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True


# A stand-in for worldgen's output: the same exported surface (WORLD / spawnWorld / heightAt) the
# scaffold imports, without running worldgen in a unit test.
FAKE_WORLD_TS = """// GENERATED by worldgen
export const WORLD: any = {
  plaza: { x: 0, z: 0 },
  buildings: [0, 1, 2, 3, 4].map((i) => ({ id: `b${i}`, label: "cottage", x: 12 + i * 6, z: 0,
                                           w: 4, d: 4, h: 3, color: "#a84" })),
};
export function spawnWorld(world: World): void {
  world.push({ shape: "ground", x: 0, y: 0, z: 0, size: 200, color: "#274" });
}
export function heightAt(x: number, z: number): number { return 2 + x * 0.01 + z * 0.02; }
"""
# The failure this guards: a hook module that uses the world API correctly but never assigns
# state.ground / state.walls. A live build did exactly that — the player walked through buildings in
# mid-air and NO gate could see it — so the scaffold wires both itself on a world game.
WORLD_GAME_TS = """import { WORLD, spawnWorld, heightAt } from "./world.ts";
export interface GameState {
  world: World; player: Entity | null; ground: any; walls: any; f: number;
}
export function createState(kit: Kit): GameState {
  return { world: [], player: null, ground: null, walls: null, f: 0 };
}
export function init(state: GameState, kit: Kit): void {
  spawnWorld(state.world);
  const p = WORLD.plaza;
  // y = 0 is UNDERGROUND here — only the scaffold's clamp can stand the player on the terrain.
  state.player = kit.spawn(state.world, { shape: "box", x: p.x, y: 0, z: p.z,
                                          w: 0.8, h: 1.7, d: 0.8, color: "#28303a" });
}
export function update(state: GameState, dt: number, input: Input, kit: Kit): void {
  state.f++;
  const p = state.player!;
  if (state.f > 1) {
    const want = heightAt(p.x, p.z) + 0.85;   // the clamp lags XZ one frame — hence the tolerance
    if (Math.abs(p.y - want) > 0.5) throw new Error(`NOT GROUNDED: y=${p.y} want=${want}`);
    if (!state.walls || state.walls.length !== 5)
      throw new Error(`NOT WALLED: ${state.walls && state.walls.length}`);
  }
}
export function hud(state: GameState, kit: Kit): HudItem[] { return []; }
const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };
"""


@pytest.mark.parametrize("scheme", ["orbital-3d", "first-person-3d"])
def test_world_scaffold_wires_ground_and_walls_itself(tmp_path, scheme):
    spec = _spec(scheme, mode="3d", world={"settlement": "X"})
    _hook_game(tmp_path, spec, WORLD_GAME_TS)
    (tmp_path / "game" / "world.ts").write_text(FAKE_WORLD_TS, encoding="utf-8")
    main = (tmp_path / "game" / "main.ts").read_text()
    assert 'import { WORLD, heightAt } from "./world.ts";' in main
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True


def test_world_less_scaffold_never_imports_world_ts(tmp_path):
    # The import only exists when worldgen seeded a world — otherwise it would not resolve.
    src = _seed(tmp_path, _spec("orbital-3d", mode="3d")).read_text()
    assert "world.ts" not in src
    assert "{world_init}" not in src and "{world_import}" not in src


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
    # slimes SPAWN stacked yet separate — the scaffold's collideWorld pass pushed them apart.
    spec = _spec("top-down")
    spec["design"]["controls"] = {"WASD": "move", "F": "attack"}
    _hook_game(tmp_path, spec, GAME_TS_SOLID)
    assert typecheck(tmp_path) == []
    assert run_headless(tmp_path).get("ok") is True
    assert run_render(tmp_path).get("ok") is True
    errs = CodegenModule().get_errors(_ctx(tmp_path, spec))
    assert errs == []


def test_scaffold_activate_satisfies_spec_key(tmp_path):
    # The dialogue scaffold registers "interact" on the spec's key, not a hardcoded E.
    spec = _spec("top-down", uses=["dialogue"])
    spec["design"]["controls"] = {"WASD": "move", "E": "talk to villagers"}
    _hook_game(tmp_path, spec, GAME_TS_TALK)
    main = (tmp_path / "game" / "main.ts").read_text()
    assert 'kit.register("activate", ["e"]' in main
    assert typecheck(tmp_path) == []


def test_activate_keys_bind_the_spec_control():
    def spec(controls):
        return {"design": {"controls": controls}}
    assert _activate_keys(spec({"WASD": "move", "SPACE": "interact / start dialogue"})) == [" "]
    assert _activate_keys(spec({"E": "talk to villagers"})) == ["e"]
    assert _activate_keys(spec({"Enter": "speak"})) == ["Enter"]
    # multi-token keys keep every real key; movement/mouse tokens fall out
    assert _activate_keys(spec({"E/Space": "interact"})) == ["e", " "]
    assert _activate_keys(spec({"LEFT_CLICK": "talk"})) == ["e"]   # no mouse keys to bind → default
    assert _activate_keys(spec({"F": "attack"})) == ["e"]          # nothing interact-shaped → default
    assert _activate_keys(spec({})) == ["e"]


def test_scaffold_activate_binds_a_space_spec_key(tmp_path):
    # The shipped incident: the spec bound interact to SPACE, the scaffold hardcoded E and its
    # post-init register REPLACED the hook's correct space binding by name (register replaces by
    # name) — unbound_control on " " became unfixable by construction and the build burned its
    # whole step cap. The scaffold must bind the spec's key.
    spec = _spec("top-down", uses=["dialogue"])
    spec["design"]["controls"] = {"WASD": "move", "SPACE": "interact / start dialogue"}
    _hook_game(tmp_path, spec, GAME_TS_TALK)
    main = (tmp_path / "game" / "main.ts").read_text()
    assert 'kit.register("activate", [" "]' in main
    assert typecheck(tmp_path) == []


def test_hook_own_activate_register_wins_over_the_scaffold(tmp_path):
    # Same incident, other half: the scaffold registers BEFORE the hook init runs, so a game that
    # registers its own "interact" (dialogue + combat in one handler) replaces the scaffold's
    # default instead of being clobbered by it — the scaffold only fills a blank.
    spec = _spec("top-down", uses=["dialogue"])
    spec["design"]["controls"] = {"WASD": "move", "SPACE": "interact / start dialogue"}
    game = GAME_TS_TALK.replace(
        'talk: { name: "Elder", lines: ["Hello."] } });\n}',
        'talk: { name: "Elder", lines: ["Hello."] } });\n'
        '  kit.register("activate", [" "], () => { (state as any).presses = '
        '((state as any).presses ?? 0) + 1; });\n}')
    assert 'presses' in game   # the replace landed
    _hook_game(tmp_path, spec, game)
    main = (tmp_path / "game" / "main.ts").read_text()
    assert main.index('kit.register("activate"') < main.index("initGame(state, kit);")
    assert typecheck(tmp_path) == []


# ── planned / authoring integration ───────────────────────────────────────────
def _ctx(tmp_path, spec):
    return build_context(spec, RunState(tmp_path))


def _manifest(tmp_path, names):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"files": [{"name": n, "purpose": "", "exports": []} for n in names]}), encoding="utf-8")
    (d / "data").mkdir(exist_ok=True)
    (d / "data" / "manifest.json").write_text(json.dumps(
        {"datasets": [{"name": "things", "fields": {"hp": "number"}}]}), encoding="utf-8")
    (d / "data" / "things.json").write_text(json.dumps(
        [{"id": "thing_a", "hp": 1}]), encoding="utf-8")
    generate_data_ts(tmp_path)      # the GENERATED main.ts imports ROWS from it


def test_authoring_order_puts_game_ts_last(tmp_path):
    _seed(tmp_path, _spec("top-down"))
    _manifest(tmp_path, ["game.ts", "types.ts", "combat.ts"])
    seed_interfaces(tmp_path)
    order = [f["name"] for f in _authoring_order(tmp_path)]
    assert order == ["types.ts", "combat.ts", "game.ts"]   # contract first, hook entry LAST


def test_loop_builds_scaffolded_game_to_green(tmp_path, monkeypatch):
    """The production shape end-to-end through the build chain: scaffold seeded first (as kickoff
    does), then the driver plans game.ts (fallback), authors it from the model, and every gate —
    including the scheme-aware probe — passes with main.ts never model-written."""

    class FakeConn:
        def generate_with_tools(self, messages, tools=None, **kw):
            return canned_prelude(messages) or {
                "choices": [{"message": {"content": f"```ts\n{GAME_TS}\n```"}}]}

    state = RunState(tmp_path)
    spec = _spec("top-down")
    state.write_spec(spec)
    seed_scaffold(state, spec)                       # kickoff's _seed will then skip (main.ts exists)
    scaffold_src = (tmp_path / "game" / "main.ts").read_text()
    cursor = run_build_to_completion(str(tmp_path), FakeConn(), monkeypatch)
    assert cursor.ok is True
    assert (tmp_path / "game" / "game.ts").read_text().strip() == GAME_TS.strip()
    assert (tmp_path / "game" / "main.ts").read_text() == scaffold_src   # scaffold untouched


# ── hook re-export bridge (run-7 regression) ────────────────────────────────────
def test_reexport_hooks_bridges_sibling_owned_hook(tmp_path):
    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text("// GENERATED control scaffold — never edit\n", encoding="utf-8")
    (d / "game.ts").write_text(
        "export function init(s: any, kit: Kit): void {}\n"
        "export function update(s: any, dt: number, input: Input, kit: Kit): void {}\n"
        ""
        "export function hud(s: any, kit: Kit): HudItem[] { return []; }\n", encoding="utf-8")
    (d / "world.ts").write_text(
        "export function createState(kit: Kit): any { return { world: [] }; }\n", encoding="utf-8")
    res = reexport_hooks(tmp_path)
    assert res["count"] == 1 and res["changes"] == [("reexport", "createState<-world.ts")]
    assert 'export { createState } from "./world.ts";' in (d / "game.ts").read_text()
    assert reexport_hooks(tmp_path)["count"] == 0   # idempotent — bridge already present


def test_reexport_hooks_skips_ambiguous_owner(tmp_path):
    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text("// GENERATED control scaffold — never edit\n", encoding="utf-8")
    (d / "game.ts").write_text("export function init(s: any, kit: Kit): void {}\n", encoding="utf-8")
    (d / "a.ts").write_text("export function createState(kit: Kit): any { return {}; }\n", encoding="utf-8")
    (d / "b.ts").write_text("export const createState = (kit: Kit): any => ({});\n", encoding="utf-8")
    res = reexport_hooks(tmp_path)
    assert all(not c[1].startswith("createState") for c in res["changes"])


def test_missing_hook_classifies_and_bridges(tmp_path):
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
        'import { createState, init as initGame, update as updateGame, hud as hudGame } from "./game.ts";\n'
        'export function createGame(kit: Kit): GameObject {\n'
        '  const state: any = createState(kit);\n'
        '  return { config: { width: 100, height: 100 }, state,\n'
        '    init(kit) { initGame(state, kit); },\n'
        '    update(dt, input, kit) { updateGame(state, dt, input, kit); },\n'
        '    hud(kit) { return hudGame(state, kit); } };\n'
        '}\n', encoding="utf-8")
    (d / "game.ts").write_text(game_ts, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "game.ts", "purpose": "hooks", "exports": ["createState", "init", "update", "hud"]}]}),
        encoding="utf-8")
    return tmp_path


_HOOKS_OK = (
    "export interface GameState { world: World; player: Entity | null; }\n"
    "export function createState(kit: Kit): GameState { return { world: [], player: null }; }\n"
    "export function init(state: GameState, kit: Kit): void { state.player = { x: 0, y: 0, w: 8, h: 8 }; }\n"
    "export function update(state: GameState, dt: number, input: Input, kit: Kit): void {}\n"
    "export function hud(state: GameState, kit: Kit): HudItem[] { return []; }\n"
    "const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };\n")


def test_contract_assertion_localizes_signature_drift_to_game_ts(tmp_path):
    """The run-7 terminal failure: a hook's params in the wrong ORDER surfaced as an error at the
    GENERATED scaffold's call site. With the assertion, tsc reports it INSIDE game.ts."""
    bad = _HOOKS_OK.replace(
        "export function update(state: GameState, dt: number, input: Input, kit: Kit): void",
        "export function update(state: GameState, input: Input, dt: number, kit: Kit): void")
    _assert_run(tmp_path, bad)
    errs = typecheck(tmp_path)
    assert errs, "expected a type error from the swapped update params"
    assert any(f == "game.ts" for f, m in errs), errs


def test_contract_assertion_clean_hooks_typecheck_green(tmp_path):
    _assert_run(tmp_path, _HOOKS_OK)
    assert typecheck(tmp_path) == []


def test_required_state_fields_come_from_the_scheme_s_own_template():
    """Read off the template that will actually be seeded, so it cannot drift from the scaffold."""
    from maestro.codegen.scaffold import required_state_fields

    fields = required_state_fields({"mode": "3d", "design": {"control": {"scheme": "vehicle-3d"}}})
    assert {"player", "world", "walls", "ground"} <= set(fields)

    grid = required_state_fields({"mode": "2d", "design": {"control": {"scheme": "grid-turn"}}})
    assert {"player", "world", "passable"} <= set(grid)
    assert "walls" not in grid          # a 2D scheme is not handed the 3D passes


def test_an_unknown_scheme_still_yields_the_universal_fields():
    from maestro.codegen.scaffold import required_state_fields

    fields = required_state_fields({"mode": "2d", "design": {"control": {"scheme": "nonsense"}}})
    assert {"player", "world"} <= set(fields)


def test_state_contract_notes_quote_the_template_s_own_documentation():
    """Field NAMES are not enough: told only that `ground` exists, an architecture declared it
    `Entity | null` and the scaffold called `state.ground(x, z)` — a frame-0 TypeError."""
    from maestro.codegen.scaffold import state_contract_notes

    notes = "\n".join(state_contract_notes(
        {"mode": "3d", "design": {"control": {"scheme": "vehicle-3d"}}}))
    assert "(x, z) => number" in notes          # ground is a height function, not an entity
    assert "Rect[]" in notes                     # walls are rects, not entities
    assert "state.player" in notes


def test_state_contract_notes_are_scheme_specific():
    from maestro.codegen.scaffold import state_contract_notes

    grid = "\n".join(state_contract_notes(
        {"mode": "2d", "design": {"control": {"scheme": "grid-turn"}}}))
    assert "(x, z) => number" not in grid       # no terrain height fn in a 2D grid game


def test_state_contract_notes_keep_whole_comment_blocks():
    """Taking only the lines that say `state.` cut a note mid-sentence at 'state.cell? (defaults to',
    so the model never saw 'tilemap.tile or 32' and declared cell as an object, not a number."""
    from maestro.codegen.scaffold import state_contract_notes

    notes = "\n".join(state_contract_notes(
        {"mode": "2d", "design": {"control": {"scheme": "platformer"}}}))
    assert "state.cell? (defaults to" in notes
    assert "tilemap.tile or 32" in notes          # the continuation line, which says no `state.`


def test_contract_assertion_is_held_only_for_the_typecheck(tmp_path):
    """The line is dead code that exists so a hook signature drift lands in game.ts instead of the
    GENERATED scaffold. tsc is the only thing that sees it; it is never left on disk."""
    from types import SimpleNamespace
    from maestro.codegen.module import _with_contract_assert
    from maestro.codegen.scaffold import contract_assert_line, has_contract_assert

    _assert_run(tmp_path, _HOOKS_OK.replace(
        "const _scaffoldContract: GameHooks<GameState> = { createState, init, update, hud };\n", ""))
    spec = {"design": {"control": {"scheme": "top-down"}}}
    before = (tmp_path / "game" / "game.ts").read_text()
    assert not has_contract_assert(tmp_path)

    with _with_contract_assert(tmp_path, spec):
        assert contract_assert_line(spec) in (tmp_path / "game" / "game.ts").read_text()

    assert (tmp_path / "game" / "game.ts").read_text() == before   # restored exactly


def test_contract_assertion_left_alone_when_the_game_already_has_it(tmp_path):
    from maestro.codegen.module import _with_contract_assert

    _assert_run(tmp_path, _HOOKS_OK)
    spec = {"design": {"control": {"scheme": "top-down"}}}
    before = (tmp_path / "game" / "game.ts").read_text()
    with _with_contract_assert(tmp_path, spec):
        pass
    assert (tmp_path / "game" / "game.ts").read_text() == before
