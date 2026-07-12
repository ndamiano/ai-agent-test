"""Codegen build path: gates, module checks, and the AgentLoop driving a game to passing.

The kit + node runners are real (the gates shell out to runtime/*.mjs); the LLM is stubbed so the
loop test is deterministic and needs no live model.
"""

from pathlib import Path

import pytest

from maestro.agent_loop import AgentLoop
from maestro.codegen.gates import RUNTIME_DIR, extract_code, run_headless, run_probe
from maestro.codegen.module import CodegenModule
from maestro.codegen.tools import build_codegen_tools
from maestro.modules.module import ErrorType
from maestro.state import RunState

PONG = (RUNTIME_DIR / "games" / "pong.js").read_text(encoding="utf-8")
BROKEN = "export function createGame(kit){ return { init(){ throw new Error('boom'); }, update(){}, config:{} }; }"


def _run_dir(tmp_path) -> RunState:
    return RunState(tmp_path)


# ── gates ─────────────────────────────────────────────────────────────────────
def test_run_headless_green_on_pong(tmp_path):
    (tmp_path / "game.js").write_text(PONG)
    assert run_headless(_run_dir(tmp_path).run_dir).get("ok") is True


def test_run_headless_reports_missing(tmp_path):
    hl = run_headless(tmp_path)
    assert hl["ok"] is False and hl["phase"] == "missing"


def test_run_headless_catches_crash(tmp_path):
    (tmp_path / "game.js").write_text(BROKEN)
    hl = run_headless(tmp_path)
    assert hl["ok"] is False and "boom" in hl.get("error", "")


def test_run_probe_green_on_pong(tmp_path):
    (tmp_path / "game.js").write_text(PONG)
    assert run_probe(tmp_path).get("ok") is True


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


def test_authored_error_when_no_file(tmp_path):
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["authored"]
    assert errs[0].type is ErrorType.BUILD


def test_authored_blocks_runs_and_plays(tmp_path):
    # blocking `authored` must suppress the later checks so an empty run reports one thing to do.
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert len(errs) == 1


def test_clean_game_has_no_errors(tmp_path):
    (tmp_path / "game.js").write_text(PONG)
    assert CodegenModule().get_errors(_ctx(_run_dir(tmp_path))) == []


def test_runs_error_on_crash(tmp_path):
    (tmp_path / "game.js").write_text(BROKEN)
    errs = CodegenModule().get_errors(_ctx(_run_dir(tmp_path)))
    assert [e.code for e in errs] == ["runs"]
    assert errs[0].type is ErrorType.FIX


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
    assert (tmp_path / "game.js").read_text().strip() == PONG.strip()


def test_loop_refuses_unfrozen_spec(tmp_path):
    state = _run_dir(tmp_path)
    spec = {"frozen": False, "mode": "2d", "design": {}}
    state.write_spec(spec)
    loop = AgentLoop(spec, state, [CodegenModule()], build_codegen_tools(state),
                     connector=_FakeConn(PONG), max_steps=5)
    with pytest.raises(RuntimeError):
        loop.run()
