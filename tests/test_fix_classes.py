"""Fix classes — error-class → fixer routing, the authority block a contract mismatch injects, and the
deterministic reconciler's field-append opt-out (so a field mismatch reaches the authority-guided LLM
instead of being laundered into types.ts). classify/authority are pure (no toolchain); the reconcile
opt-out runs real tsc via the gates."""

import json

from maestro.codegen.fix_classes import CONTRACT, DEFAULT, _contract_authority, classify
from maestro.codegen.gates import game_dir, reconcile_types, typecheck
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
    import json as _json
    mpath = game_dir(rd) / "manifest.json"
    man = _json.loads(mpath.read_text())
    man["files"].append({"name": "hud.ts", "purpose": "hud", "exports": ["draw"]})
    mpath.write_text(_json.dumps(man), encoding="utf-8")
    res = reconcile_types(rd, include_fields=False)
    assert not any(k == "export" and v == "movePlayer" for k, v in res["changes"])
    assert "export type movePlayer = any;" not in (game_dir(rd) / "types.ts").read_text()


def test_ambient_shadow_class_matches_and_strips(tmp_path):
    from maestro.codegen.fix_classes import classify, _strip_kit_shadow
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
    from maestro.codegen.fix_classes import classify, _strip_phantom_imports

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


def test_dispatch_fix_reports_every_deterministic_change_shape(tmp_path):
    """The summary line must format both change shapes — reconcile's (kind, (a, b)) tuples and
    ambient-shadow's (kind, filename) — a bare-string change once crashed the whole build here."""
    from unittest.mock import MagicMock, patch
    from maestro.codegen.module import dispatch_fix
    from maestro.codegen import fix_classes

    for changes in ([("strip", "main.ts")], [("field", ("Hero", "stamina"))]):
        cls = fix_classes.FixClass(
            id="stub", matches=lambda e: True,
            deterministic=lambda rd, e, c=changes: {"changes": c, "count": len(c)})
        services = MagicMock()
        context = MagicMock()
        context.state.run_dir = tmp_path
        with patch.object(fix_classes, "classify", lambda e: cls):
            dispatch_fix(None, context, _err("boom"), 0, services, lambda n, a: None)
        assert services._report.called
