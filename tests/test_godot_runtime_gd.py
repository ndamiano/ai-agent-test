"""GDScript-level self-test for the Godot runtime's pure core (ir.gd).

Skipped unless a `godot` binary is on PATH (same spirit as tests/integration). When present, it
copies the runtime to a temp project, drops a SceneTree self-test beside it, and runs it headless —
catching GDScript parse errors and IRCore logic regressions the Python tests can't see.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).parent.parent / "src" / "godot" / "runtime"
_GODOT = shutil.which("godot") or shutil.which("godot4")

_SELFTEST = """
extends SceneTree

const IRCore = preload("res://ir.gd")

func _fail(msg):
\tprint("SELFTEST FAIL: ", msg)
\tquit(1)

func _initialize():
\tvar ir = {"flags": ["a"], "variables": [{"id": "g", "default": 5}]}
\tvar st = IRCore.make_state(ir)
\tif st["vars"]["g"] != 5: _fail("default var"); return
\tIRCore.apply_effect(st, {"set_flag": "a"})
\tif not IRCore.eval_cond(st, {"flag": "a"}): _fail("set_flag"); return
\tif not IRCore.eval_cond(st, {"var": "g", "op": ">=", "value": 5}): _fail("var cmp"); return
\tif IRCore.eval_cond(st, {"not": {"flag": "a"}}): _fail("not"); return
\tIRCore.apply_effects(st, [{"add_var": {"var": "g", "delta": -2}}, {"add_item": "key"}])
\tif st["vars"]["g"] != 3: _fail("add_var"); return
\tif not IRCore.eval_cond(st, {"item": "key"}): _fail("add_item"); return
\tif not IRCore.eval_cond(st, {"all": [{"flag": "a"}, {"item": "key"}]}): _fail("all"); return
\tprint("SELFTEST OK")
\tquit(0)
"""


# Drives combat.gd's turn loop with a stub Game driver (all awaits resolve synchronously):
# a DoT wipe under unmatched when-conditions must resolve instead of spinning forever, a poison
# kill at the victim's turn start must end combat before the enemy acts again, and vn.menu_pick
# (the path Game._flow routes a combat menu end through) must filter/apply/return correctly.
_COMBAT_SELFTEST = """
extends SceneTree

const Combat = preload("res://combat.gd")
const Vn = preload("res://vn.gd")

class FakeGame:
\tvar state = {"flags": {}, "vars": {}, "inv": []}
\tvar chars = {}
\tvar node_by_id = {}
\tvar stat_by_id = {}
\tvar ability_by_id = {}
\tvar status_by_id = {}
\tvar combatant_by_id = {}
\tvar encounter_by_id = {}
\tvar lines = []
\tfunc set_scene(_bg): pass
\tfunc show_line(_sp, text): lines.append(text)
\tfunc hide_dialogue(): pass
\tfunc set_hud(_t): pass
\tfunc show_menu(_labels): return 0

func _fail(msg):
\tprint("SELFTEST FAIL: ", msg)
\tquit(1)

func _make_game() -> FakeGame:
\tvar g = FakeGame.new()
\tg.stat_by_id = {"hp": {"id": "hp", "default": 5, "role": "resource_depletable",
\t\t"min": 0, "max": 5}}
\tg.status_by_id = {"poison": {"id": "poison",
\t\t"tick": [{"stat": "hp", "op": "damage", "formula": {"base": 99}}]}}
\tg.ability_by_id = {
\t\t"curse": {"id": "curse", "name": "Curse", "targeting": {"shape": "self"},
\t\t\t"effects": [{"status": "poison", "duration": 3}]},
\t\t"hit": {"id": "hit", "name": "Hit",
\t\t\t"targeting": {"shape": "single", "faction": "enemy"},
\t\t\t"effects": [{"stat": "hp", "op": "damage", "formula": {"base": 1}}]}}
\tg.combatant_by_id = {
\t\t"hero": {"id": "hero", "abilities": ["curse"]},
\t\t"goblin": {"id": "goblin", "abilities": ["hit"]}}
\treturn g

func _initialize():
\t_run()

func _run():
\tvar g = _make_game()
\tg.encounter_by_id = {"e1": {"id": "e1",
\t\t"combatants": [{"ref": "hero", "faction": "player"}],
\t\t"victory": {"when": {"flag": "nope"}}, "defeat": {"when": {"flag": "nope"}},
\t\t"on_defeat": {"type": "end", "ending": "wiped"}}}
\tvar res = await Combat.new(g).run("e1")
\tif res == null or res.get("ending") != "wiped": _fail("dead field did not resolve"); return

\tg = _make_game()
\tg.encounter_by_id = {"e2": {"id": "e2",
\t\t"combatants": [{"ref": "hero", "faction": "player"}, {"ref": "goblin", "faction": "enemy"}],
\t\t"victory": {"all_defeated": "enemy"}}}
\tres = await Combat.new(g).run("e2")
\tif res == null or res.get("ending") != "game_over": _fail("default defeat not returned"); return
\tvar goblin_acts = 0
\tfor l in g.lines:
\t\tif l.begins_with("goblin uses"):
\t\t\tgoblin_acts += 1
\tif goblin_acts != 1: _fail("enemy acted %d times, not 1" % goblin_acts); return

\tg = _make_game()
\tvar target = await Vn.new(g).menu_pick({"type": "menu", "choices": [
\t\t{"text": "locked", "requires": {"flag": "nope"}, "target": "n_locked"},
\t\t{"text": "go", "target": "n_go", "effects": [{"set_flag": "went"}]}]})
\tif target != "n_go": _fail("menu_pick target"); return
\tif not g.state["flags"].get("went", false): _fail("menu_pick effects"); return

\tprint("SELFTEST OK")
\tquit(0)
"""


def _run_selftest(tmp_path, script):
    proj = tmp_path / "proj"
    shutil.copytree(_RUNTIME, proj)
    (proj / "SelfTest.gd").write_text(script)
    proc = subprocess.run(
        [_GODOT, "--headless", "--path", str(proj), "--script", "res://SelfTest.gd"],
        capture_output=True, text=True, timeout=120)
    assert "SELFTEST OK" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_ircore_parity(tmp_path):
    _run_selftest(tmp_path, _SELFTEST)


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_combat_resolution_and_menu_flow(tmp_path):
    _run_selftest(tmp_path, _COMBAT_SELFTEST)


def test_combat_checks_end_after_status_tick():
    body = (_RUNTIME / "combat.gd").read_text().split("func run(")[1].split("\nfunc ")[0]
    assert body.index("_any_alive") < body.index("_tick_statuses(u)")
    assert "_check_end" in body[body.index("_tick_statuses(u)"):body.index("await _player_turn")]


def test_flow_routes_menu_end():
    flow = (_RUNTIME / "Game.gd").read_text().split("func _flow(")[1].split("\nfunc ")[0]
    assert '"menu"' in flow
    assert "menu_pick" in flow
