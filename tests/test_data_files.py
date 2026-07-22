"""Data files stage — model-designed, typed game data separated from code: validation, the
deterministic data.ts generation, the prompt summary block, the deterministic asset plan, and the
module wiring (the blocking `data` check + the design fix's fallback). No live LLM — services are
stubbed."""

import json
import os
import re
from pathlib import Path

from maestro.codegen import data_files, reskin
from maestro.codegen.data_files import (
    data_dir,
    data_manifest_path,
    data_summary,
    generate_data_ts,
    sprite_plan_from_data,
    validate_data,
    write_design,
)
from maestro.codegen.module import CodegenModule, _design_data_fix
from maestro.modules.context import build_context
from maestro.modules.module import Error, ErrorType
from maestro.state import RunState

GOOD_GAME = """export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200, seed: 1 },
    state: { world: [] as World, p: null as any },
    init(kit) { this.state.p = kit.spawn(this.state.world, { x: 50, y: 50, w: 10, h: 10 }); },
    update(dt, input, kit) {
      if (input.down("d")) kit.walk(this.state.p, 1, 150); else kit.walk(this.state.p, 0, 150);
      kit.integrate(this.state.p, dt);
      this.state.p.x = kit.V.clamp(this.state.p.x, 0, 190);
    },
    draw(g) { g.clear("#000"); g.rect(this.state.p.x, this.state.p.y, 10, 10, "#fff"); },
  };
}"""

MANIFEST = {"datasets": [
    {"name": "enemies", "fields": {"hp": "number", "speed": "number", "drops": "ref:items[]?"}},
    {"name": "items", "fields": {"value": "number"}},
]}
ENEMIES = [
    {"id": "orc", "name": "Orc", "look": "a green orc warrior, plain background",
     "presence": "world", "size": {"w": 24, "h": 24}, "hp": 10, "speed": 50, "drops": ["coin"]},
    {"id": "bat", "hp": 4, "speed": 90},
]
ITEMS = [{"id": "coin", "name": "Coin", "look": "a gold coin, plain background",
          "presence": "ui", "size": {"w": 8, "h": 8}, "value": 1}]


def _write_data(tmp_path, manifest=None, rows=None):
    dd = tmp_path / "game" / "data"
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "manifest.json").write_text(json.dumps(manifest or MANIFEST), encoding="utf-8")
    for name, r in (rows if rows is not None else {"enemies": ENEMIES, "items": ITEMS}).items():
        (dd / f"{name}.json").write_text(json.dumps(r), encoding="utf-8")
    return tmp_path


def _write_planned_game(tmp_path, code=GOOD_GAME):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "main.ts").write_text(code, encoding="utf-8")
    (d / "manifest.json").write_text(
        json.dumps({"files": [{"name": "main.ts", "purpose": "game", "exports": ["createGame"]}]}),
        encoding="utf-8")
    return tmp_path


def _ctx(tmp_path):
    return build_context({"mode": "2d", "design": {}}, RunState(tmp_path))


# ── validate_data ─────────────────────────────────────────────────────────────
def test_validate_good_data_passes(tmp_path):
    _write_data(tmp_path)
    assert validate_data(tmp_path) == []


def test_validate_empty_datasets_is_valid(tmp_path):
    _write_data(tmp_path, manifest={"datasets": []}, rows={})
    assert validate_data(tmp_path) == []


def test_validate_flags_bad_type_vocab(tmp_path):
    _write_data(tmp_path, manifest={"datasets": [{"name": "enemies", "fields": {"hp": "int"}}]},
                rows={"enemies": []})
    errs = validate_data(tmp_path)
    assert any("field 'hp' has invalid type 'int'" in e and "number" in e for e in errs)


def test_validate_flags_unknown_row_field(tmp_path):
    rows = {"enemies": [{"id": "orc", "hp": 1, "speed": 2, "atk": 9}], "items": ITEMS}
    _write_data(tmp_path, rows=rows)
    errs = validate_data(tmp_path)
    assert any("row 1 (id 'orc')" in e and "unknown field 'atk'" in e for e in errs)


def test_validate_flags_type_mismatch_with_row_and_id(tmp_path):
    rows = {"enemies": [{"id": "orc", "hp": 1, "speed": 2},
                        {"id": "imp", "hp": 3, "speed": 4},
                        {"id": "ogre", "hp": "lots", "speed": 5}], "items": ITEMS}
    _write_data(tmp_path, rows=rows)
    errs = validate_data(tmp_path)
    assert any("data/enemies.json row 3 (id 'ogre'): field 'hp' must be number, got string" in e
               for e in errs)


def test_validate_flags_duplicate_id(tmp_path):
    rows = {"enemies": [{"id": "orc", "hp": 1, "speed": 2}, {"id": "orc", "hp": 3, "speed": 4}],
            "items": ITEMS}
    _write_data(tmp_path, rows=rows)
    errs = validate_data(tmp_path)
    assert any("duplicate id 'orc'" in e for e in errs)


def test_validate_flags_broken_ref(tmp_path):
    rows = {"enemies": [{"id": "orc", "hp": 1, "speed": 2, "drops": ["sword"]}], "items": ITEMS}
    _write_data(tmp_path, rows=rows)
    errs = validate_data(tmp_path)
    assert any("ref 'sword' not found in dataset 'items'" in e for e in errs)


def test_validate_flags_bad_presence_and_size(tmp_path):
    rows = {"enemies": [{"id": "orc", "hp": 1, "speed": 2, "presence": "hud",
                         "size": {"w": 5}}], "items": ITEMS}
    _write_data(tmp_path, rows=rows)
    errs = validate_data(tmp_path)
    assert any("presence must be" in e and "'hud'" in e for e in errs)
    assert any("size must be" in e for e in errs)


def test_validate_flags_missing_required_field_and_rows_file(tmp_path):
    _write_data(tmp_path, rows={"enemies": [{"id": "orc", "hp": 1}]})   # speed missing, no items.json
    errs = validate_data(tmp_path)
    assert any("missing required field 'speed'" in e for e in errs)
    assert any("data/items.json: missing" in e for e in errs)


def test_validate_flags_envelope_declared_as_field(tmp_path):
    _write_data(tmp_path, manifest={"datasets": [{"name": "x", "fields": {"look": "string"}}]},
                rows={"x": []})
    errs = validate_data(tmp_path)
    assert any("'look' is an envelope field" in e for e in errs)


# ── generate_data_ts ──────────────────────────────────────────────────────────
def test_generate_emits_typed_interface_and_const(tmp_path):
    _write_data(tmp_path)
    generate_data_ts(tmp_path)
    src = (tmp_path / "game" / "data.ts").read_text()
    assert src.startswith("// GENERATED from data/*.json")
    assert 'export type Presence = "world" | "ui" | "both";' in src
    assert "export interface Size { w: number; h: number; d?: number }" in src
    assert "export interface EnemiesRow {" in src
    assert "hp: number;" in src
    assert "drops?: string[];" in src          # optional ref[] → string[], ? honored
    assert "export const ENEMIES: readonly EnemiesRow[] = [" in src
    assert "export const ITEMS: readonly ItemsRow[] = [" in src
    assert '"id": "orc"' in src


def test_generate_is_idempotent(tmp_path):
    _write_data(tmp_path)
    generate_data_ts(tmp_path)
    p = tmp_path / "game" / "data.ts"
    os.utime(p, (1, 1))
    generate_data_ts(tmp_path)
    assert p.stat().st_mtime == 1              # unchanged content → not rewritten


def test_generate_removes_file_when_no_datasets(tmp_path):
    _write_data(tmp_path)
    generate_data_ts(tmp_path)
    assert (tmp_path / "game" / "data.ts").exists()
    _write_data(tmp_path, manifest={"datasets": []}, rows={})
    generate_data_ts(tmp_path)
    assert not (tmp_path / "game" / "data.ts").exists()


def test_generated_row_keys_are_envelope_then_declared_order(tmp_path):
    rows = {"enemies": [{"speed": 2, "hp": 1, "id": "orc", "name": "Orc"}], "items": ITEMS}
    _write_data(tmp_path, rows=rows)
    generate_data_ts(tmp_path)
    src = (tmp_path / "game" / "data.ts").read_text()
    line = next(l for l in src.splitlines() if '"orc"' in l)
    assert list(json.loads(line.strip().rstrip(",")).keys()) == ["id", "name", "hp", "speed"]


# ── data_summary ──────────────────────────────────────────────────────────────
def test_summary_names_const_fields_and_one_example(tmp_path):
    _write_data(tmp_path)
    s = data_summary(tmp_path)
    assert s.startswith('# GAME DATA (import from "./data.ts"')
    assert "enemies (2 rows) — const ENEMIES: EnemiesRow[]" in s
    assert "hp: number" in s
    assert '"id": "orc"' in s                  # ONE example row
    assert '"id": "bat"' not in s              # never all rows


def test_summary_empty_when_no_data(tmp_path):
    (tmp_path / "game").mkdir()
    assert data_summary(tmp_path) == ""
    _write_data(tmp_path, manifest={"datasets": []}, rows={})
    assert data_summary(tmp_path) == ""


# ── sprite_plan_from_data ─────────────────────────────────────────────────────
def test_plan_2d_picks_look_rows_with_sizes(tmp_path):
    _write_data(tmp_path)
    plan = sprite_plan_from_data(tmp_path, "2d")
    assert [(p["id"], p["w"], p["h"]) for p in plan] == [("orc", 24, 24), ("coin", 8, 8)]
    assert plan[0]["prompt"] == "a green orc warrior, plain background"   # bat has no look → excluded


def test_plan_3d_filters_on_world_presence(tmp_path):
    _write_data(tmp_path)
    plan = sprite_plan_from_data(tmp_path, "3d")
    assert [p["id"] for p in plan] == ["orc"]              # coin is ui-only
    assert plan[0]["d"] == 1                               # no d in size → default


def test_plan_none_without_manifest_or_looks(tmp_path):
    (tmp_path / "game").mkdir()
    assert sprite_plan_from_data(tmp_path, "2d") is None
    rows = {"enemies": [{"id": "orc", "hp": 1, "speed": 2}], "items": []}
    _write_data(tmp_path, rows=rows)
    assert sprite_plan_from_data(tmp_path, "2d") is None   # rows exist but none carries a look


def test_plan_dedupes_across_datasets_first_wins(tmp_path):
    manifest = {"datasets": [{"name": "a", "fields": {}}, {"name": "b", "fields": {}}]}
    rows = {"a": [{"id": "orc", "look": "first orc"}], "b": [{"id": "orc", "look": "second orc"}]}
    _write_data(tmp_path, manifest=manifest, rows=rows)
    plan = sprite_plan_from_data(tmp_path, "2d")
    assert len(plan) == 1 and plan[0]["prompt"] == "first orc"


# ── write_design ──────────────────────────────────────────────────────────────
def test_write_design_lands_manifest_rows_and_survives_validation(tmp_path):
    (tmp_path / "game").mkdir()
    design = [{"name": "enemies", "fields": {"hp": "number"}, "rows": [{"id": "orc", "hp": 3}]}]
    assert write_design(tmp_path, design) == []
    assert json.loads(data_manifest_path(tmp_path).read_text()) == \
        {"datasets": [{"name": "enemies", "fields": {"hp": "number"}}]}   # decls only, no rows
    assert json.loads((data_dir(tmp_path) / "enemies.json").read_text()) == [{"id": "orc", "hp": 3}]
    assert validate_data(tmp_path) == []


def test_write_design_drops_offending_dataset_wholesale(tmp_path):
    (tmp_path / "game").mkdir()
    design = [
        {"name": "enemies", "fields": {"hp": "number"}, "rows": [{"id": "orc", "hp": 3}]},
        {"name": "items", "fields": {"value": "number"}, "rows": [{"id": "coin", "value": "NaN"}]},
    ]
    dropped = write_design(tmp_path, design)
    assert dropped == ["items"]
    assert [d["name"] for d in json.loads(data_manifest_path(tmp_path).read_text())["datasets"]] \
        == ["enemies"]
    assert not (data_dir(tmp_path) / "items.json").exists()   # dropped dataset's rows removed
    assert validate_data(tmp_path) == []


def test_write_design_broken_ref_drops_only_the_referencer(tmp_path):
    (tmp_path / "game").mkdir()
    design = [
        {"name": "enemies", "fields": {"drops": "ref:items"},
         "rows": [{"id": "orc", "drops": "sword"}]},           # 'sword' does not exist in items
        {"name": "items", "fields": {"value": "number"}, "rows": [{"id": "coin", "value": 1}]},
    ]
    assert write_design(tmp_path, design) == ["enemies"]       # the innocent ref target survives
    assert [d["name"] for d in json.loads(data_manifest_path(tmp_path).read_text())["datasets"]] \
        == ["items"]
    assert validate_data(tmp_path) == []


def test_write_design_normalizes_envelope_decls_and_improvised_types(tmp_path):
    """Regression: the observed local-model design — envelope fields re-declared in `fields`, a
    nested size decl, and a {"$enum": [...]} type — must land normalized, not drop the dataset."""
    (tmp_path / "game").mkdir()
    design = [{
        "name": "enemies",
        "fields": {"hp": "number", "look": "string", "presence": "string",
                   "size": {"w": "number", "h": "number"},
                   "behavior": {"$enum": ["chase", "wander"]}},
        "rows": [
            {"id": "goblin", "look": "a goblin", "presence": "world",
             "size": {"w": 20, "h": 20}, "hp": 8, "behavior": "chase"},
            {"id": "slime", "look": "a slime", "presence": "world",
             "size": {"w": 28, "h": 28}, "hp": 30, "behavior": "wander"},
        ]}]
    assert write_design(tmp_path, design) == []
    decls = json.loads(data_manifest_path(tmp_path).read_text())["datasets"][0]["fields"]
    assert decls == {"hp": "number", "behavior": "string"}   # envelope stripped, $enum → string
    assert validate_data(tmp_path) == []


def test_write_design_infers_optional_for_partial_fields(tmp_path):
    (tmp_path / "game").mkdir()
    design = [{"name": "items", "fields": {"tag": {"$enum": ["a"]}},
               "rows": [{"id": "coin", "tag": "a"}, {"id": "gem"}]}]
    assert write_design(tmp_path, design) == []
    decls = json.loads(data_manifest_path(tmp_path).read_text())["datasets"][0]["fields"]
    assert decls == {"tag": "string?"}   # a row omits it → optional


def test_write_design_survives_the_run2_local_model_design(tmp_path):
    """Regression: the full raw design a local Qwen produced on 2026-07-21 (run 2) — nulls for
    absent values, `?` on field NAMES, one-level object fields — previously dropped ALL datasets.
    Must land all three, flattened + typed."""
    (tmp_path / "game").mkdir()
    raw = (Path(__file__).parent / "fixtures" / "design_local_qwen_run2.txt").read_text()
    design = json.loads(re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.S).group(1))
    assert write_design(tmp_path, design["datasets"]) == []
    manifest = json.loads(data_manifest_path(tmp_path).read_text())
    by_name = {d["name"]: d["fields"] for d in manifest["datasets"]}
    assert set(by_name) == {"enemies", "items", "status_effects"}
    assert by_name["enemies"]["drops_coins_min"] == "number"       # nested object flattened
    assert by_name["items"]["effect_type"] == "string?"            # ? moved off the name, onto the type
    rows = json.loads((data_dir(tmp_path) / "enemies.json").read_text())
    assert len(rows) == 6
    assert all("inflicts_status" not in r for r in rows)           # all-null field deleted
    assert validate_data(tmp_path) == []


def test_normalize_strips_null_row_values(tmp_path):
    (tmp_path / "game").mkdir()
    design = [{"name": "fx", "fields": {"dps": "number?"},
               "rows": [{"id": "poison", "dps": 2, "look": None, "presence": None},
                        {"id": "slow", "dps": None}]}]
    assert write_design(tmp_path, design) == []
    rows = json.loads((data_dir(tmp_path) / "fx.json").read_text())
    assert rows == [{"id": "poison", "dps": 2}, {"id": "slow"}]


def test_normalize_declares_undeclared_row_fields(tmp_path):
    (tmp_path / "game").mkdir()
    design = [{"name": "items", "fields": {},
               "rows": [{"id": "coin", "value": 10}, {"id": "gem", "value": 50}]}]
    assert write_design(tmp_path, design) == []
    decls = json.loads(data_manifest_path(tmp_path).read_text())["datasets"][0]["fields"]
    assert decls == {"value": "number"}


def test_normalize_relaxes_required_field_sparse_rows_omit(tmp_path):
    """Regression (run 4): decl `pass_through_walls: "boolean"` required, only the ghost row carries
    it — sparse rows mean "absent = default", so the decl relaxes to optional instead of dropping
    the dataset."""
    (tmp_path / "game").mkdir()
    design = [{"name": "enemies",
               "fields": {"hp": "number", "pass_through_walls": "boolean"},
               "rows": [{"id": "goblin", "hp": 15},
                        {"id": "ghost", "hp": 10, "pass_through_walls": True}]}]
    assert write_design(tmp_path, design) == []
    decls = json.loads(data_manifest_path(tmp_path).read_text())["datasets"][0]["fields"]
    assert decls == {"hp": "number", "pass_through_walls": "boolean?"}
    assert validate_data(tmp_path) == []


def test_write_design_still_drops_unresolvable_types(tmp_path):
    (tmp_path / "game").mkdir()
    design = [{"name": "items", "fields": {"blob": {"nested": "object"}},
               "rows": [{"id": "coin", "blob": {"x": {"y": 1}}}]}]   # deep nesting — even flattening can't type it
    assert write_design(tmp_path, design) == ["items"]


# ── module wiring ─────────────────────────────────────────────────────────────
def test_data_check_blocks_before_authored_when_design_missing(tmp_path):
    d = tmp_path / "game"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(
        {"files": [{"name": "main.ts", "purpose": "game", "exports": ["createGame"]}]}))
    errs = CodegenModule().get_errors(_ctx(tmp_path))
    assert [e.code for e in errs] == ["data"]              # blocking: authored is suppressed
    assert errs[0].type is ErrorType.BUILD
    assert "no data design yet" in errs[0].message


def test_data_check_reports_violations_as_fix_error(tmp_path):
    _write_planned_game(tmp_path)
    _write_data(tmp_path, rows={"enemies": [{"id": "orc", "hp": "x", "speed": 1}], "items": ITEMS})
    errs = CodegenModule().get_errors(_ctx(tmp_path))
    assert [e.code for e in errs] == ["data"]
    assert errs[0].type is ErrorType.FIX
    assert "must be number, got string" in errs[0].message


def test_valid_data_regenerates_stale_data_ts_and_game_stays_green(tmp_path):
    _write_planned_game(tmp_path)
    _write_data(tmp_path)
    assert CodegenModule().get_errors(_ctx(tmp_path)) == []   # data.ts generated + typechecks
    p = tmp_path / "game" / "data.ts"
    src = p.read_text()
    p.write_text("// stale garbage", encoding="utf-8")
    assert CodegenModule().get_errors(_ctx(tmp_path)) == []
    assert p.read_text() == src                               # the detector re-synced it


IMPORTING_GAME = """import { ENEMIES } from "./data.ts";
export function createGame(kit: Kit): GameObject {
  return {
    config: { width: 200, height: 200, seed: 1 },
    state: { world: [] as World, p: null as any, hp: 0 },
    init(kit) {
      this.state.p = kit.spawn(this.state.world, { x: 50, y: 50, w: 10, h: 10 });
      this.state.hp = ENEMIES[0].hp;
    },
    update(dt, input, kit) {
      if (input.down("d")) kit.walk(this.state.p, 1, 150); else kit.walk(this.state.p, 0, 150);
      kit.integrate(this.state.p, dt);
      this.state.p.x = kit.V.clamp(this.state.p.x, 0, 190);
    },
    draw(g) { g.clear("#000"); g.rect(this.state.p.x, this.state.p.y, 10, 10, "#fff"); },
  };
}"""


def test_game_importing_data_ts_passes_every_gate(tmp_path):
    # the whole contract end-to-end: tsc resolves ./data.ts (typed rows), esbuild bundles it,
    # and the sim gates run the bundle.
    _write_planned_game(tmp_path, code=IMPORTING_GAME)
    _write_data(tmp_path)
    assert CodegenModule().get_errors(_ctx(tmp_path)) == []


class _Svc:
    def __init__(self, content):
        self.content = content
        self.reports = []

    def infer(self, msgs, schemas, **kw):
        return {"choices": [{"message": {"content": self.content}}]}

    def _report(self, s):
        self.reports.append(s)


def _design_error():
    return Error(type=ErrorType.BUILD, code="data", component="game", message="no data design yet")


def test_design_fix_fallback_writes_empty_design_on_garbage(tmp_path):
    _write_planned_game(tmp_path)
    svc = _Svc("I think the game needs, hmm, ```ts\nconst x = 1;\n```")
    _design_data_fix(CodegenModule(), _ctx(tmp_path), _design_error(), 0, svc, None)
    assert json.loads(data_manifest_path(tmp_path).read_text()) == {"datasets": []}
    assert not (tmp_path / "game" / "data.ts").exists()
    assert CodegenModule().get_errors(_ctx(tmp_path)) == []   # build proceeds on the empty design


def test_design_fix_lands_a_valid_design(tmp_path):
    _write_planned_game(tmp_path)
    design = {"datasets": [{"name": "waves", "fields": {"count": "number"},
                            "rows": [{"id": "wave_1", "count": 3}]}]}
    svc = _Svc(f"```json\n{json.dumps(design)}\n```")
    _design_data_fix(CodegenModule(), _ctx(tmp_path), _design_error(), 0, svc, None)
    assert validate_data(tmp_path) == []
    src = (tmp_path / "game" / "data.ts").read_text()
    assert "export const WAVES: readonly WavesRow[]" in src
    assert any("designed 1 dataset(s): waves" in r for r in svc.reports)


# ── reskin wiring ─────────────────────────────────────────────────────────────
class _Result:
    ok = False


def _boom(*a, **k):
    raise AssertionError("LLM planner called despite a data plan")


def test_skin_2d_prefers_the_data_plan(tmp_path, monkeypatch):
    _write_planned_game(tmp_path)
    _write_data(tmp_path)
    monkeypatch.setattr(reskin, "plan_assets", _boom)
    monkeypatch.setattr(reskin, "_reskin_and_gate", lambda *a, **k: _Result())
    monkeypatch.setattr(reskin, "generate_sprites", lambda rd, s: set())
    out = reskin._skin_2d("rid", RunState(tmp_path), {"design": {}}, None,
                          {"main.ts": "g.rect(0,0,1,1,'#fff')"}, 1)
    assert [s["id"] for s in out["sprites"]] == ["orc", "coin"]


def test_skin_3d_data_plan_unions_required_mesh_tags(tmp_path, monkeypatch):
    _write_planned_game(tmp_path)
    _write_data(tmp_path)
    monkeypatch.setattr(reskin, "plan_meshes", _boom)
    monkeypatch.setattr(reskin, "_reskin_and_gate", lambda *a, **k: _Result())
    monkeypatch.setattr(reskin, "generate_meshes", lambda rd, m: set())
    files = {"main.ts": 'config: { mode: "3d" }\nworld.push({ shape: "box", mesh: "old_barn" });'}
    out = reskin._skin_3d("rid", RunState(tmp_path), {"design": {}}, None, files, 1)
    ids = [m["id"] for m in out["meshes"]]
    assert ids == ["orc", "old_barn"]          # data rows + the source's required tag, none dropped
