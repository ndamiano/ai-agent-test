import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.codegen import play_gate


# ---------------------------------------------------------------- parsing turns

def test_parse_turn_key_action():
    turn = play_gate._parse_turn(json.dumps({
        "verdict": "met", "observed": "the panel opened",
        "action": {"key": "e"}, "expected": "the potion is consumed"}))
    assert turn["verdict"] == "met"
    assert play_gate._action_of(turn["action"]) == ("key", "e")


def test_parse_turn_fenced_and_bad_verdict_defaults_unclear():
    text = '```json\n{"verdict": "maybe", "action": {"key": "Tab"}, "expected": "inventory"}\n```'
    turn = play_gate._parse_turn(text)
    assert turn["verdict"] == "unclear"
    assert play_gate._action_of(turn["action"]) == ("key", "Tab")


def test_parse_turn_without_action_is_none():
    assert play_gate._parse_turn('{"verdict": "met", "observed": "x"}') is None
    assert play_gate._parse_turn("not json at all") is None


def test_action_click_bounds():
    assert play_gate._action_of({"click": {"x": 10, "y": 20}}) == ("click", (10, 20))
    assert play_gate._action_of({"click": {"x": 99999, "y": 20}}) is None


def test_action_key_name_mapping():
    assert play_gate._action_of({"key": "space"}) == ("key", "Space")
    assert play_gate._action_of({"key": "W"}) == ("key", "w")


def test_action_hold_clamps_seconds():
    kind, (name, seconds) = play_gate._action_of({"hold": {"key": "w", "seconds": 30}})
    assert kind == "hold" and name == "w" and seconds == 3.0


# ---------------------------------------------------------------- parsing the report

def test_parse_report_keeps_shaped_facts_only():
    turns = [{"verdict": "unmet"}]
    report = play_gate._parse_report(json.dumps({
        "broken": [{"action": "e", "expected": "potion used", "observed": "nothing"},
                   {"expected": "orphan without action"}, "not a dict"],
        "judgment": {"works": "movement", "impressions": "shallow"}}), turns)
    assert len(report["broken"]) == 1
    assert report["judgment"]["impressions"] == "shallow"
    assert report["turns"] is turns


def test_parse_report_off_shape_still_reports_turns():
    report = play_gate._parse_report("the model rambled", [{"verdict": "met"}])
    assert report["broken"] == [] and report["turns"]


# ---------------------------------------------------------------- transcript eliding

def test_elided_keeps_last_window_of_screenshots():
    history = []
    for n in range(6):
        history.append({"role": "user", "content": [
            {"type": "text", "text": f"Turn {n}"},
            {"type": "image_url", "image_url": {"url": "data:..."}}]})
        history.append({"role": "assistant", "content": "{}"})
    out = play_gate._elided(history)
    stubs = [m for m in out if m["role"] == "user" and isinstance(m["content"], str)]
    pixels = [m for m in out if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(pixels) == play_gate.IMAGE_WINDOW
    assert len(stubs) == 6 - play_gate.IMAGE_WINDOW
    assert stubs[0]["content"].startswith("Turn 0")


# ---------------------------------------------------------------- the note

def test_note_carries_the_fact():
    note = play_gate.note_for_fact({"action": "e", "expected": "potion consumed", "observed": ""})
    assert "Input pressed: e" in note
    assert "potion consumed" in note
    assert "(no visible change)" in note
    assert "keep the game playing the way it already does" in note


# ---------------------------------------------------------------- the loop

def _run(tmp_path, monkeypatch, reports, state=None):
    """Drive after_build with a stubbed session; returns (kicked notes, run_dir)."""
    run_dir = tmp_path / "runs" / "r1"
    (run_dir / "game").mkdir(parents=True)
    if state:
        (run_dir / play_gate.STATE_FILE).write_text(json.dumps(state))

    kicked = []

    class _RS:
        def __init__(self, run_id):
            self.run_dir = run_dir
        def read_spec(self):
            return {"request": "a game"}

    import maestro.state
    import maestro.codegen.build_chain as build_chain
    import maestro.codegen.staging as staging
    import tools.execution_context as ec

    monkeypatch.setattr(maestro.state, "RunState", _RS)
    monkeypatch.setattr(staging, "game_dir", lambda rd: rd / "game")
    monkeypatch.setattr(ec, "run_scope", lambda rid, bid: __import__("contextlib").nullcontext())
    monkeypatch.setattr(build_chain, "kickoff",
                        lambda run_id, **kw: kicked.append(kw.get("note")))
    monkeypatch.setattr(play_gate, "play", lambda gdir, req: reports.pop(0))
    started = play_gate.after_build("r1", "b1")
    return started, kicked, run_dir


def test_broken_fact_kicks_one_fix(tmp_path, monkeypatch):
    report = {"broken": [{"action": "e", "expected": "use", "observed": "nothing"},
                         {"action": "q", "expected": "drink", "observed": "nothing"}],
              "judgment": {}, "turns": []}
    started, kicked, run_dir = _run(tmp_path, monkeypatch, [report])
    assert started and len(kicked) == 1
    assert "Input pressed: e" in kicked[0]
    assert json.loads((run_dir / play_gate.REPORT_FILE).read_text())["broken"]


def test_clean_report_starts_nothing_and_writes_report(tmp_path, monkeypatch):
    started, kicked, run_dir = _run(tmp_path, monkeypatch,
                                    [{"broken": [], "judgment": {"works": "all"}, "turns": [1]}])
    assert not started and not kicked
    assert (run_dir / play_gate.REPORT_FILE).exists()


def test_failed_session_stands_aside(tmp_path, monkeypatch):
    started, kicked, run_dir = _run(tmp_path, monkeypatch, [None])
    assert not started and not kicked
    assert not (run_dir / play_gate.REPORT_FILE).exists()


def test_same_fact_twice_stops(tmp_path, monkeypatch):
    report = {"broken": [{"action": "e", "expected": "use", "observed": "no"}],
              "judgment": {}, "turns": []}
    started, kicked, _ = _run(tmp_path, monkeypatch, [report],
                              state={"rounds": 1, "last_fact": "e|use"})
    assert not started and not kicked


def test_max_rounds_stops(tmp_path, monkeypatch):
    report = {"broken": [{"action": "w", "expected": "move", "observed": "no"}],
              "judgment": {}, "turns": []}
    started, kicked, _ = _run(tmp_path, monkeypatch, [report],
                              state={"rounds": play_gate.MAX_ROUNDS, "last_fact": None})
    assert not started and not kicked
