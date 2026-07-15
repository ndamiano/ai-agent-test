"""The append-only type-contract reconciler: a consumer file reaching a field the shared interface
lacks produces a TS2339/TS2551 error; reconcile_types APPENDS the missing field to the named interface
(never removes → converges). Real tsc runs (via the gates), so these need the runtime toolchain."""

import json

from maestro.codegen.gates import game_dir, reconcile_types, typecheck
from maestro.state import RunState


def _run_dir(tmp_path) -> RunState:
    return RunState(tmp_path)


def _write_game(tmp_path, types_src, consumer_src, consumer="combat.ts"):
    """A multi-file game sharing a types.ts: main.ts (entry) + a consumer that reads the shared shapes.
    Mirrors test_codegen._write_game's manifest so the folder is a real game."""
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    main = ("import { run } from './" + consumer + "';\n"
            "export function createGame(kit: Kit): GameObject {\n"
            "  return { config: {}, state: {} as any, update(dt, input, kit) { run(); } }; }")
    (d / "main.ts").write_text(main, encoding="utf-8")
    (d / "types.ts").write_text(types_src, encoding="utf-8")
    (d / consumer).write_text(consumer_src, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "types.ts", "purpose": "shared", "exports": []},
        {"name": consumer, "purpose": "consumer", "exports": ["run"]},
        {"name": "main.ts", "purpose": "entry", "exports": ["createGame"]}]}), encoding="utf-8")


TYPES = """export interface Hero {
  hp: number;
  maxHp: number;
}

export interface GameState {
  score: number;
}
"""

# reads Hero.attackCooldown (missing) — a TS2339.
COMBAT = """import { Hero } from './types.ts';
export function run(): void {
  const h = { hp: 10, maxHp: 10 } as Hero;
  h.attackCooldown = 5;
}
"""


def test_appends_missing_field(tmp_path):
    _write_game(tmp_path, TYPES, COMBAT)
    rd = _run_dir(tmp_path).run_dir
    assert typecheck(rd)   # dirty before
    res = reconcile_types(rd)
    assert ("field", ("Hero", "attackCooldown")) in res["changes"]
    assert res["count"] == 1
    body = (game_dir(rd) / "types.ts").read_text()
    assert "attackCooldown?: any;" in body
    assert typecheck(rd) == []   # reconciled → clean


def test_idempotent(tmp_path):
    _write_game(tmp_path, TYPES, COMBAT)
    rd = _run_dir(tmp_path).run_dir
    reconcile_types(rd)
    before = (game_dir(rd) / "types.ts").read_text()
    res2 = reconcile_types(rd)
    assert res2["count"] == 0 and res2["changes"] == []
    assert (game_dir(rd) / "types.ts").read_text() == before


def test_leaves_type_alias_untouched(tmp_path):
    # Player is an object type ALIAS (not an export interface) — reconcile only appends to interfaces,
    # so it makes NO change here and leaves the missing field for the read→write fix loop.
    consumer = """import { Player } from './types.ts';
export function run(): void {
  const p = { hp: 1 } as Player;
  p.mana = 3;
}
"""
    types = TYPES + "\nexport type Player = { hp: number };\n"
    _write_game(tmp_path, types, consumer)
    rd = _run_dir(tmp_path).run_dir
    res = reconcile_types(rd)
    assert not any(k == "field" and v[0] == "Player" for k, v in res["changes"])
    assert "export type Player = { hp: number }" in (game_dir(rd) / "types.ts").read_text()   # untouched


def test_picks_the_right_interface_of_several(tmp_path):
    consumer = """import { Hero, GameState } from './types.ts';
export function run(): void {
  const g = { score: 0 } as GameState;
  g.level = 2;
}
"""
    _write_game(tmp_path, TYPES, consumer)
    rd = _run_dir(tmp_path).run_dir
    res = reconcile_types(rd)
    assert res["changes"] == [("field", ("GameState", "level"))]
    body = (game_dir(rd) / "types.ts").read_text()
    # appended inside GameState, not Hero.
    gs = body[body.index("interface GameState"):]
    assert "level?: any;" in gs
    assert "level?: any;" not in body[:body.index("interface GameState")]


def test_handles_interface_with_nested_object_field(tmp_path):
    # Hero has a nested object-typed field; balancing braces must find Hero's OWN closing brace, so the
    # append lands after `pos`, not inside it.
    types = """export interface Hero {
  hp: number;
  pos: { x: number; y: number };
}

export interface GameState {
  score: number;
}
"""
    consumer = """import { Hero } from './types.ts';
export function run(): void {
  const h = { hp: 1, pos: { x: 0, y: 0 } } as Hero;
  h.attackCooldown = 5;
}
"""
    _write_game(tmp_path, types, consumer)
    rd = _run_dir(tmp_path).run_dir
    res = reconcile_types(rd)
    assert res["changes"] == [("field", ("Hero", "attackCooldown"))]
    assert typecheck(rd) == []
    body = (game_dir(rd) / "types.ts").read_text()
    hero = body[body.index("interface Hero"):body.index("interface GameState")]
    assert "attackCooldown?: any;" in hero
    assert "{ x: number; y: number }" in hero   # nested field intact


def test_declares_missing_export(tmp_path):
    # A consumer imports a type types.ts never exports (TS2305) → reconcile declares `export type X = any`.
    consumer = """import { Loot } from './types.ts';
export function run(): void { const x: Loot = {} as Loot; void x; }
"""
    _write_game(tmp_path, TYPES, consumer)
    rd = _run_dir(tmp_path).run_dir
    res = reconcile_types(rd)
    assert ("export", "Loot") in res["changes"]
    assert "export type Loot = any;" in (game_dir(rd) / "types.ts").read_text()
    assert typecheck(rd) == []


def test_relaxes_required_field_for_partial_literal(tmp_path):
    # A partial object literal omits a required field (TS2741) → reconcile makes that field optional.
    types = """export interface Hero {
  id: string;
  hp: number;
  maxHp: number;
}
export interface GameState { score: number; }
"""
    consumer = """import { Hero } from './types.ts';
export function run(): void { const h: Hero = { id: 'a', hp: 1 }; void h; }
"""
    _write_game(tmp_path, types, consumer)
    rd = _run_dir(tmp_path).run_dir
    res = reconcile_types(rd)
    assert any(k == "relax" for k, _ in res["changes"])
    assert "maxHp?:" in (game_dir(rd) / "types.ts").read_text()
    assert typecheck(rd) == []


def test_does_not_relax_for_wrong_named_type(tmp_path):
    # Passing a WRONG named type where an interface is expected (TS2739, source is a named type, not a
    # `{…}` literal) is a real call bug — reconcile must NOT relax the identity fields to hide it.
    types = """export interface Entity {
  id: string;
  x: number;
  y: number;
}
export interface Other { tag: string; }
export interface GameState { score: number; }
"""
    consumer = """import { Entity, Other } from './types.ts';
export function take(e: Entity): void { void e; }
export function run(): void { const o: Other = { tag: 't' }; take(o); }
"""
    _write_game(tmp_path, types, consumer)
    rd = _run_dir(tmp_path).run_dir
    reconcile_types(rd)
    body = (game_dir(rd) / "types.ts").read_text()
    assert "id: string;" in body and "id?:" not in body   # identity field NOT relaxed
    assert "x: number;" in body and "x?:" not in body


def test_converts_union_used_as_value_to_enum(tmp_path):
    # A union type used as a value (`Kind.Player`, TS2693) → reconcile turns it into an enum whose
    # members are the usages, each keeping its string value.
    types = """export type Kind = 'player' | 'goblin';
export interface Entity { id: string; kind?: Kind; }
export interface GameState { score: number; }
"""
    consumer = """import { Kind } from './types.ts';
export function run(): void { const k = Kind.Player; const g = Kind.Goblin; void k; void g; }
"""
    _write_game(tmp_path, types, consumer)
    rd = _run_dir(tmp_path).run_dir
    res = reconcile_types(rd)
    assert ("enum", "Kind") in res["changes"]
    body = (game_dir(rd) / "types.ts").read_text()
    assert "export enum Kind" in body
    assert 'Player = "player"' in body and 'Goblin = "goblin"' in body
    assert typecheck(rd) == []
