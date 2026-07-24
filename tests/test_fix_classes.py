"""Fix classes — error-class → fixer routing, the authority block a contract mismatch injects, and the
deterministic reconciler's field-append opt-out (so a field mismatch reaches the authority-guided LLM
instead of being laundered into types.ts). classify/authority are pure (no toolchain); the reconcile
opt-out runs real tsc via the gates."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from maestro.codegen import fix_classes
from maestro.codegen.fix_classes import (
    CONTRACT,
    DEFAULT,
    _contract_authority,
    _strip_kit_shadow,
    _strip_phantom_imports,
    classify,
    strip_dead_creategame,
    strip_unplanned_imports,
)
from maestro.codegen.gates import game_dir, reconcile_types, typecheck
from maestro.codegen.module import _detect_single_mover
from maestro.modules.module import Error, ErrorType
from maestro.state import RunState


def _err(message, kind=None, code="typechecks"):
    return Error(type=ErrorType.FIX, code=code, component="game", message=message, kind=kind)


# ── classify ──────────────────────────────────────────────────────────────────
def test_field_mismatch_routes_to_contract():
    e = _err("line 5: error TS2339: Property 'health' does not exist on type 'Player'")
    assert classify(e) is CONTRACT


def test_missing_export_routes_to_contract():
    e = _err("line 1: error TS2305: Module './types' has no exported member 'Enemy'")
    assert classify(e) is CONTRACT


def test_arg_count_error_falls_to_default():
    # TS2554 (arg count) is not yet its own class — degrades to the generic loop, never misrouted.
    e = _err("line 9: error TS2554: Expected 2 arguments, but got 3")
    assert classify(e) is DEFAULT


def test_probe_kind_falls_to_default():
    e = _err("PROBE FAILED: [dead_controls] pressing left moved nothing", kind="dead_controls", code="plays")
    assert classify(e) is DEFAULT


def test_dead_action_routes_to_its_directive():
    # The condition-gated-ability thrash (a cost the player starts unable to pay): the class must
    # steer toward denied-press feedback, never a spec-betraying rebalance.
    e = _err('PROBE FAILED: [dead_action] registered action "overdrive" (keys ["f"]) was pressed '
             "8 time(s) and changed NOTHING", kind="dead_action", code="plays")
    c = classify(e)
    assert c.id == "dead-action"
    assert "feedback" in c.directive.lower()
    assert c.deterministic is None


def test_crash_falls_to_default():
    e = _err("HEADLESS FAILED: TypeError: cannot read x of undefined", kind="crash", code="runs")
    assert classify(e) is DEFAULT


# ── contract authority block ──────────────────────────────────────────────────
def _write_files(tmp_path, files: dict):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    for name, src in files.items():
        (d / name).write_text(src, encoding="utf-8")
    return RunState(tmp_path).run_dir


def test_authority_shows_real_members_and_flags_wrong_name(tmp_path):
    rd = _write_files(tmp_path, {
        "types.ts": "export interface Player extends Kit.Entity {\n  hp: number;\n  x: number;\n}\n",
        # hp accessed 3×, health (the wrong name) 1×
        "main.ts": "const p:any={}; p.hp; p.hp=p.hp-1; p.health;\n",
    })
    e = _err("line 3: error TS2339: Property 'health' does not exist on type 'Player'")
    block = _contract_authority(None, rd, e)
    assert "AUTHORITY" in block
    assert "interface Player" in block                 # real definition injected
    assert ".hp: 3 use(s)" in block                    # the dominant existing member
    assert "the referenced (wrong) name" in block      # health is flagged, not silently endorsed
    assert "health" in block


def test_authority_empty_when_no_type_named(tmp_path):
    rd = _write_files(tmp_path, {"main.ts": "const x = 1;\n"})
    e = _err("line 9: error TS2554: Expected 2 arguments, but got 3")
    assert _contract_authority(None, rd, e) == ""


def test_missing_export_authority_points_at_real_name(tmp_path):
    rd = _write_files(tmp_path, {"types.ts": "export interface Opponent {}\n", "main.ts": "const x=1;\n"})
    e = _err("line 1: error TS2305: Module './types' has no exported member 'Enemy'")
    block = _contract_authority(None, rd, e)
    assert "Enemy" in block and "DIFFERENT name" in block


# ── deterministic field-append opt-out (real tsc) ─────────────────────────────
_TYPES = "export interface Hero {\n  hp: number;\n}\n"
_CONSUMER = ("import { Hero } from './types.ts';\n"
             "export function run(): void { const h = { hp: 1 } as Hero; h.stamina = 5; }\n")


def _game(tmp_path, types, consumer):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    main = ("import { run } from './combat.ts';\n"
            "export function createGame(kit: Kit): GameObject {\n"
            "  return { config: {}, state: {} as any, update(dt, input, kit) { run(); } }; }")
    (d / "main.ts").write_text(main, encoding="utf-8")
    (d / "types.ts").write_text(types, encoding="utf-8")
    (d / "combat.ts").write_text(consumer, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "types.ts", "purpose": "shared", "exports": []},
        {"name": "combat.ts", "purpose": "consumer", "exports": ["run"]},
        {"name": "main.ts", "purpose": "entry", "exports": ["createGame"]}]}), encoding="utf-8")
    return RunState(tmp_path).run_dir


def test_include_fields_false_does_not_append(tmp_path):
    rd = _game(tmp_path, _TYPES, _CONSUMER)
    assert typecheck(rd)                                   # dirty (Hero lacks stamina)
    res = reconcile_types(rd, include_fields=False)
    assert not any(k == "field" for k, _ in res["changes"])   # field NOT laundered into the type
    assert "stamina" not in (game_dir(rd) / "types.ts").read_text()
    assert typecheck(rd)                                   # still dirty → routed to the authority LLM


def test_include_fields_true_still_appends(tmp_path):
    # The default path is unchanged: with fields on, the reconciler still converges the contract.
    rd = _game(tmp_path, _TYPES, _CONSUMER)
    res = reconcile_types(rd, include_fields=True)
    assert ("field", ("Hero", "stamina")) in res["changes"]
    assert typecheck(rd) == []


def test_missing_export_still_reconciled_with_fields_off(tmp_path):
    # A genuinely-missing shared type is additive (not hallucination-prone), so the deterministic pass
    # still declares it even with field-append off — only the field transform is gated.
    consumer = "import { Weapon } from './types.ts';\nexport function run(): void { const w: Weapon = 0 as any; }\n"
    rd = _game(tmp_path, _TYPES, consumer)
    res = reconcile_types(rd, include_fields=False)
    assert ("export", "Weapon") in res["changes"]
    assert "export type Weapon = any;" in (game_dir(rd) / "types.ts").read_text()


def test_does_not_declare_ambient_kit_type_as_export(tmp_path):
    # A file wrongly imports the ambient `Kit` from ./types.ts. Declaring `export type Kit = any` would
    # mask the real ambient type — the reconciler must NOT do it; it routes to the authority LLM.
    consumer = "import { Kit } from './types.ts';\nexport function run(k: Kit): void { const x = k; }\n"
    rd = _game(tmp_path, _TYPES, consumer)
    res = reconcile_types(rd, include_fields=False)
    assert not any(k == "export" and v == "Kit" for k, v in res["changes"])
    assert "export type Kit = any;" not in (game_dir(rd) / "types.ts").read_text()


def test_does_not_declare_export_that_a_sibling_owns(tmp_path):
    # main.ts imports `movePlayer` from ./types.ts, but movePlayer is exported by combat.ts. The fix is
    # the import path, not a stub in types.ts — the reconciler must not declare it.
    consumer = "export function movePlayer(): void {}\nexport function run(): void { movePlayer(); }\n"
    rd = _game(tmp_path, _TYPES, consumer)
    # add a second consumer that mis-imports movePlayer from types.ts
    (game_dir(rd) / "hud.ts").write_text("import { movePlayer } from './types.ts';\n"
                                         "export function draw(): void { movePlayer(); }\n", encoding="utf-8")
    mpath = game_dir(rd) / "manifest.json"
    man = json.loads(mpath.read_text())
    man["files"].append({"name": "hud.ts", "purpose": "hud", "exports": ["draw"]})
    mpath.write_text(json.dumps(man), encoding="utf-8")
    res = reconcile_types(rd, include_fields=False)
    assert not any(k == "export" and v == "movePlayer" for k, v in res["changes"])
    assert "export type movePlayer = any;" not in (game_dir(rd) / "types.ts").read_text()


def test_ambient_shadow_class_matches_and_strips(tmp_path):
    e = _err("types.ts: error TS2459: Module '\"./types\"' declares 'Kit' locally, but it is not exported.")
    assert classify(e).id == "ambient-shadow"
    e2 = _err("main.ts: error TS2708: Cannot use namespace 'Kit' as a value.")
    assert classify(e2).id == "ambient-shadow"

    game = tmp_path / "game"
    game.mkdir()
    (game / "manifest.json").write_text(
        '{"files": [{"name": "types.ts"}, {"name": "ui.ts"}]}', encoding="utf-8")
    (game / "types.ts").write_text(
        "/** ambient */\ndeclare namespace Kit {\n  interface Entity { x: number; }\n}\n"
        "export interface Crop extends Kit.Entity { stage: number; }\n", encoding="utf-8")
    (game / "ui.ts").write_text(
        'import type { Kit } from "./types";\nexport function drawUi(state: any, kit: Kit): void {}\n',
        encoding="utf-8")
    r = _strip_kit_shadow(tmp_path, e)
    assert r and set(r["changes"]) == {("strip", "types.ts"), ("strip", "ui.ts")}
    assert "declare namespace Kit" not in (game / "types.ts").read_text()
    assert "Crop extends Kit.Entity" in (game / "types.ts").read_text()
    assert "import" not in (game / "ui.ts").read_text()


def test_phantom_import_class_strips_imports_of_nonexistent_modules(tmp_path):
    """An import of a local module that isn't on disk (and can't be authored — off-plan) is
    stripped deterministically; an import of a module that EXISTS is untouched and no-ops."""
    e = _err("main.ts: error TS2307: Cannot find module './types' or its corresponding type declarations.")
    assert classify(e).id == "phantom-import"
    # './kit' stays with ambient-shadow (ordering).
    kit = _err("main.ts: error TS2307: Cannot find module './kit'.")
    assert classify(kit).id == "ambient-shadow"

    game = tmp_path / "game"
    game.mkdir()
    (game / "manifest.json").write_text('{"files": [{"name": "main.ts"}]}', encoding="utf-8")
    (game / "main.ts").write_text(
        'import type { State, Entity } from "./types";\n'
        'import { helper } from "./real";\n'
        "export function createGame(kit: Kit) { return {}; }\n", encoding="utf-8")
    (game / "real.ts").write_text("export const helper = 1;\n", encoding="utf-8")

    r = _strip_phantom_imports(tmp_path, e)
    assert r and r["changes"] == [("strip-import", "main.ts")]
    src = (game / "main.ts").read_text()
    assert '"./types"' not in src
    assert '"./real"' in src                      # existing module import untouched

    exists = _err("main.ts: error TS2307: Cannot find module './real'.")
    assert _strip_phantom_imports(tmp_path, exists) is None   # module exists — a real bug, not phantom


def test_start_fix_reports_every_deterministic_change_shape(tmp_path):
    """The summary line must format both change shapes — reconcile's (kind, (a, b)) tuples and
    ambient-shadow's (kind, filename) — a bare-string change once crashed the whole build here. The
    deterministic pass now runs when the driver STARTS a read→edit fix: a change resolves it with no
    llm turn (returns False, stays in the outer sweep)."""
    from maestro.codegen import build_chain
    from maestro.codegen.build_state import BuildCursor
    from maestro.state import RunState
    rs = RunState(str(tmp_path))
    cursor = BuildCursor(build_id="b", t0=0.0)
    # Distinct paths → distinct identities: each gets its own free deterministic pass.
    cases = [([("strip", "main.ts")], "a.ts"), ([("field", ("Hero", "stamina"))], "b.ts")]
    for changes, path in cases:
        cls = fix_classes.FixClass(
            id="stub", matches=lambda e: True,
            deterministic=lambda rd, e, c=changes: {"changes": c, "count": len(c)})
        err = Error(type=ErrorType.FIX, code="typechecks", component="game",
                    message="boom", path=path)
        with patch.object(build_chain, "classify", lambda e: cls):
            started = build_chain._start_fix(str(tmp_path), rs, cursor, err, False)
        assert started is False   # deterministic resolved it — no llm turn


# ── contract-assert: deterministic append of the known line ───────────────────
from maestro.codegen.fix_classes import (
    CONTRACT_ASSERT,
    SINGLE_MOVER,
    _append_contract_assert,
    _strip_redundant_movers,
)


def _spec_3d(tmp_path, scheme="follow-3d"):
    (tmp_path / "spec.json").write_text(json.dumps(
        {"mode": "3d", "design": {"control": {"scheme": scheme}}}), encoding="utf-8")


def test_contracted_error_routes_to_contract_assert():
    assert classify(_err("game.ts is missing the scaffold contract assertion", code="contracted")) \
        is CONTRACT_ASSERT


def test_single_mover_error_routes_to_its_class():
    assert classify(_err("game.ts line 74: kit.moveTopDown3(..., input, ...)", code="single_mover")) \
        is SINGLE_MOVER


def test_append_contract_assert_writes_the_3d_line(tmp_path):
    _spec_3d(tmp_path)
    _write_files(tmp_path, {"game.ts": "export function update() {}\n"})
    res = _append_contract_assert(tmp_path, None)
    assert res["count"] == 1
    body = (tmp_path / "game" / "game.ts").read_text(encoding="utf-8")
    assert "_scaffoldContract" in body
    assert "draw" not in body.splitlines()[-1]   # 3D contract has no draw


def test_append_contract_assert_idempotent(tmp_path):
    _spec_3d(tmp_path)
    _write_files(tmp_path, {"game.ts": "const _scaffoldContract: GameHooks<S> = { };\n"})
    assert _append_contract_assert(tmp_path, None) is None


# ── single-mover: deterministic strip of a standalone redundant movement line ─
def test_strip_redundant_mover_line(tmp_path):
    src = ("export function update(state, dt, input, kit) {\n"
           "  kit.moveTopDown3(p as Kit.Entity, input, dt, 8);\n"
           "  state.t += dt;\n"
           "}\n")
    _write_files(tmp_path, {"game.ts": src})
    res = _strip_redundant_movers(tmp_path, None)
    assert res["count"] == 1
    body = (tmp_path / "game" / "game.ts").read_text(encoding="utf-8")
    assert "moveTopDown3" not in body and "state.t += dt;" in body


def test_strip_leaves_generated_and_expression_calls(tmp_path):
    _write_files(tmp_path, {
        "main.ts": "// GENERATED control scaffold\nkit.drive(state.player, input, dt, 8);\n",
        "game.ts": "const moved = kit.drive(state.player, input, dt, 8) ?? 0;\n",
    })
    # main.ts is GENERATED (skipped); game.ts's call is inside an expression (not a standalone
    # statement line) — neither is stripped, so the pass reports nothing and the LLM path runs.
    assert _strip_redundant_movers(tmp_path, None) is None


def test_detect_single_mover_flags_input_driven_call_in_scaffolded_game(tmp_path):
    _write_files(tmp_path, {
        "main.ts": "// GENERATED control scaffold\nkit.drive(state.player, input, dt, 8);\n",
        "game.ts": "export function update(state, dt, input, kit) {\n"
                   "  kit.moveTopDown3(state.player, input, dt, 8);\n}\n",
    })
    ctx = SimpleNamespace(state=SimpleNamespace(run_dir=tmp_path))
    errs = _detect_single_mover(None, None, ctx)
    assert len(errs) == 1 and errs[0].code == "single_mover" and "moveTopDown3" in errs[0].message
    # seek3/AI movers and non-input calls don't trigger
    _write_files(tmp_path, {"game.ts": "kit.seek3(e, target, 4, dt);\nkit.drive(p, fakeInput, dt);\n"})
    assert _detect_single_mover(None, None, ctx) == []


def test_detect_single_mover_sees_input_guards_and_register_handlers(tmp_path):
    """The two shipped blind spots: `input` BEFORE the mover in a guard, and a mover inside a
    kit.register handler (input-driven by construction, no `input` token near the call)."""
    ctx = SimpleNamespace(state=SimpleNamespace(run_dir=tmp_path))
    _write_files(tmp_path, {
        "game.ts": "export function update(state, dt, input, kit) {\n"
                   "  if (input.pressed(\"w\") && kit.gridMove(state.player, 0, -1, cell, pass)) moved = true;\n"
                   "}\n"})
    errs = _detect_single_mover(None, None, ctx)
    assert len(errs) == 1 and "gridMove" in errs[0].message

    _write_files(tmp_path, {
        "game.ts": "export function init(state, kit) {\n"
                   "  kit.register(\"move_up\", [\"w\"], () => {\n"
                   "    if (!state.inCombat && kit.gridMove(state.player, 0, -1, cell, pass)) { }\n"
                   "  });\n"
                   "}\n"})
    errs = _detect_single_mover(None, None, ctx)
    assert len(errs) == 1 and "gridMove" in errs[0].message

    # An NPC mover after a CLOSED register handler stays legal.
    _write_files(tmp_path, {
        "game.ts": "export function init(state, kit) {\n"
                   "  kit.register(\"interact\", [\"e\"], () => { state.talking = true; });\n"
                   "  kit.gridMove(state.npc, 1, 0, cell, pass);\n"
                   "}\n"})
    assert _detect_single_mover(None, None, ctx) == []

    # A register-wrapped mover on a key the scaffold does NOT bind is that key's ONLY wiring —
    # the correct realization of a spec control like Q, not a double-move.
    _write_files(tmp_path, {
        "game.ts": "export function init(state, kit) {\n"
                   "  kit.register(\"move_left\", [\"q\"], () => {\n"
                   "    kit.gridMove(state.player, -1, 0, cell, pass);\n"
                   "  });\n"
                   "}\n"})
    assert _detect_single_mover(None, None, ctx) == []


def test_strip_deletes_scaffold_key_register_handlers_and_keeps_the_rest(tmp_path):
    """The LLM lane relocates instead of deleting, so the register-wrapped double-move must fall to
    the deterministic pass: scaffold-key handlers deleted whole, the q handler and interact kept."""
    from maestro.codegen.fix_classes import _strip_redundant_movers
    src = ("export function init(state, kit) {\n"
           "  kit.register(\"move_up\", [\"w\"], () => {\n"
           "    if (kit.gridMove(state.player, 0, -1, cell, pass)) { check(state); }\n"
           "  });\n"
           "  kit.register(\"move_left\", [\"q\"], () => {\n"
           "    kit.gridMove(state.player, -1, 0, cell, pass);\n"
           "  });\n"
           "  kit.register(\"interact\", [\"e\"], () => { state.talking = true; });\n"
           "}\n")
    _write_files(tmp_path, {"game.ts": src})
    res = _strip_redundant_movers(tmp_path, None)
    assert res and res["count"] == 1
    body = (tmp_path / "game" / "game.ts").read_text()
    assert "move_up" not in body
    assert "move_left" in body and "interact" in body
    ctx = SimpleNamespace(state=SimpleNamespace(run_dir=tmp_path))
    assert _detect_single_mover(None, None, ctx) == []


# ── write-time strip of unplanned imports ─────────────────────────────────────
def test_strip_unplanned_imports_removes_phantom_keeps_planned(tmp_path):
    _write_files(tmp_path, {
        "game.ts": 'import type { GameState } from "./types.ts";\n'
                   'import { ENEMIES } from "./data.ts";\n'
                   'import { stepCombat } from "./combat.ts";\n'
                   "export function update() {}\n",
        "data.ts": "export const ENEMIES = [];\n",
    })
    # combat.ts is planned (not yet authored) — kept; data.ts on disk — kept; types.ts neither — stripped.
    changed = strip_unplanned_imports(tmp_path, {"game.ts", "combat.ts"})
    body = (tmp_path / "game" / "game.ts").read_text(encoding="utf-8")
    assert changed == 1
    assert "./types.ts" not in body and "./data.ts" in body and "./combat.ts" in body


def test_strip_dead_creategame_removes_block_keeps_hooks(tmp_path):
    src = ("export function createState(kit: Kit) { return { world: [] }; }\n"
           "export function createGame(kit: Kit): Kit.GameObject {\n"
           "  const s = createState(kit);\n"
           "  return { config: { mode: \"3d\" }, state: s, update(dt, i, k) { step(s); } };\n"
           "}\n"
           "export function update(state, dt, input, kit) { step(state); }\n")
    _write_files(tmp_path, {"game.ts": src,
                            "main.ts": "// GENERATED\nfunction createGame() {}\n"})
    assert strip_dead_creategame(tmp_path) == 1
    body = (tmp_path / "game" / "game.ts").read_text(encoding="utf-8")
    assert "createGame" not in body
    assert "export function createState" in body and "export function update" in body
    main = (tmp_path / "game" / "main.ts").read_text(encoding="utf-8")
    assert "createGame" in main   # GENERATED file untouched


# ── missing-name class ────────────────────────────────────────────────────────
def test_missing_name_routes_ts2304():
    from maestro.codegen.fix_classes import MISSING_NAME
    e = _err("b.ts(3,5): error TS2304: Cannot find name 'spawnWave'", code="typechecks")
    assert classify(e) is MISSING_NAME


def test_missing_name_imports_from_the_one_exporter(tmp_path):
    from maestro.codegen.fix_classes import _import_missing_names
    rd = _write_files(tmp_path, {
        "waves.ts": "export function spawnWave(n: number): void {}\n",
        "b.ts": "export function step(): void { spawnWave(1); }\n",
    })
    e = _err("b.ts(1,30): error TS2304: Cannot find name 'spawnWave'", code="typechecks")
    e = Error(type=e.type, code=e.code, component=e.component, message=e.message, path="b.ts")
    res = _import_missing_names(rd, e)
    assert res["count"] == 1
    body = (tmp_path / "game" / "b.ts").read_text()
    assert 'import { spawnWave } from "./waves.ts";' in body.splitlines()[0]


def test_missing_name_merges_into_existing_import(tmp_path):
    from maestro.codegen.fix_classes import _import_missing_names
    rd = _write_files(tmp_path, {
        "waves.ts": "export function spawnWave(n: number): void {}\nexport const MAX = 3;\n",
        "b.ts": 'import { MAX } from "./waves.ts";\nexport function step(): void { spawnWave(MAX); }\n',
    })
    e = Error(type=ErrorType.FIX, code="typechecks", component="game", path="b.ts",
              message="b.ts(2,32): error TS2304: Cannot find name 'spawnWave'")
    res = _import_missing_names(rd, e)
    assert res["count"] == 1
    first = (tmp_path / "game" / "b.ts").read_text().splitlines()[0]
    assert "MAX" in first and "spawnWave" in first and first.count("import") == 1


def test_missing_name_leaves_hook_params_and_ambiguity_alone(tmp_path):
    from maestro.codegen.fix_classes import _import_missing_names, _missing_name_authority
    rd = _write_files(tmp_path, {
        "a.ts": "export const boom = 1;\n",
        "c.ts": "export const boom = 2;\n",
        "b.ts": "export function step(): void { boom; kit.rng(); }\n",
    })
    e = Error(type=ErrorType.FIX, code="typechecks", component="game", path="b.ts",
              message="b.ts(1,1): error TS2304: Cannot find name 'boom'\n"
                      "b.ts(1,2): error TS2304: Cannot find name 'kit'")
    assert _import_missing_names(rd, e) is None          # two exporters + a hook param → no edit
    auth = _missing_name_authority({}, rd, e)
    assert "HOOK PARAMETER" in auth and "a.ts" in auth and "c.ts" in auth


# ── dominance-gated field append ──────────────────────────────────────────────
def test_dominant_field_is_appended(tmp_path):
    from maestro.codegen.fix_classes import _append_dominant_fields
    rd = _write_files(tmp_path, {
        "types.ts": "export interface GameState {\n  score: number;\n}\n",
        "game.ts": "let s: any;\ns.combo += 1;\nif (s.combo > 2) {}\n",
    })
    e = _err("game.ts(2,3): error TS2339: Property 'combo' does not exist on type 'GameState'")
    changes = _append_dominant_fields(rd, e)
    assert ("field", ("GameState", "combo")) in changes
    assert "combo?: any;" in (tmp_path / "game" / "types.ts").read_text()


def test_near_miss_field_is_not_appended(tmp_path):
    """`health` beside an existing `hp`-like member is the caller-typo case — stays with the LLM."""
    from maestro.codegen.fix_classes import _append_dominant_fields
    rd = _write_files(tmp_path, {
        "types.ts": "export interface GameState {\n  health: number;\n}\n",
        "game.ts": "let s: any;\ns.helth = 3;\ns.helth -= 1;\n",
    })
    e = _err("game.ts(2,3): error TS2339: Property 'helth' does not exist on type 'GameState'")
    assert _append_dominant_fields(rd, e) == []


def test_single_use_field_is_not_appended(tmp_path):
    from maestro.codegen.fix_classes import _append_dominant_fields
    rd = _write_files(tmp_path, {
        "types.ts": "export interface GameState {\n  score: number;\n}\n",
        "game.ts": "let s: any;\ns.oneOff = 1;\n",
    })
    e = _err("game.ts(2,3): error TS2339: Property 'oneOff' does not exist on type 'GameState'")
    assert _append_dominant_fields(rd, e) == []


def test_kit_type_is_never_widened(tmp_path):
    from maestro.codegen.fix_classes import _append_dominant_fields
    rd = _write_files(tmp_path, {
        "types.ts": "export interface GameState { score: number; }\n",
        "game.ts": "let e: any;\ne.mana = 1;\ne.mana += 2;\n",
    })
    e = _err("game.ts(2,3): error TS2339: Property 'mana' does not exist on type 'Entity'")
    assert _append_dominant_fields(rd, e) == []          # Entity is ambient — not declared in game files
