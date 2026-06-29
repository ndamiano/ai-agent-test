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


@pytest.mark.skipif(_GODOT is None, reason="no godot binary on PATH")
def test_ircore_parity(tmp_path):
    proj = tmp_path / "proj"
    shutil.copytree(_RUNTIME, proj)
    (proj / "SelfTest.gd").write_text(_SELFTEST)

    proc = subprocess.run(
        [_GODOT, "--headless", "--path", str(proj), "--script", "res://SelfTest.gd"],
        capture_output=True, text=True, timeout=120)
    assert "SELFTEST OK" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stderr
