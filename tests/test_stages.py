import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.codegen import stages


def test_plan_parses_stage_lines(monkeypatch, tmp_path):
    reply = {"choices": [{"message": {"content":
        "STAGE 1: build the duel alone.\nSTAGE 2: add the world. Keep the duel as it is."}}]}

    class FakeConnector:
        def generate_with_tools(self, msgs, tools, max_tokens):
            return reply

    import llm_clients.connector as connector
    monkeypatch.setattr(connector, "get_connector", lambda: FakeConnector())
    out = stages.plan("make me a card rpg", "r1")
    assert out == ["build the duel alone.", "add the world. Keep the duel as it is."]


def test_plan_failure_degrades_to_single_stage(monkeypatch):
    import llm_clients.connector as connector
    monkeypatch.setattr(connector, "get_connector",
                        lambda: (_ for _ in ()).throw(RuntimeError("no llm")))
    assert stages.plan("make me a game", "r1") == ["make me a game"]


def test_one_stage_answer_degrades_to_request(monkeypatch):
    reply = {"choices": [{"message": {"content": "STAGE 1: the whole game at once."}}]}

    class FakeConnector:
        def generate_with_tools(self, msgs, tools, max_tokens):
            return reply

    import llm_clients.connector as connector
    monkeypatch.setattr(connector, "get_connector", lambda: FakeConnector())
    assert stages.plan("make me a game", "r1") == ["make me a game"]


def test_next_note_walks_the_plan_once(tmp_path):
    stages.save(tmp_path, "orig request", ["stage one", "stage two", "stage three"])
    assert stages.next_note(tmp_path) == "stage two"
    assert stages.next_note(tmp_path) == "stage three"
    assert stages.next_note(tmp_path) is None
    assert json.loads((tmp_path / "stages.json").read_text())["request"] == "orig request"


def test_next_note_without_plan_is_none(tmp_path):
    assert stages.next_note(tmp_path) is None


def test_advance_kicks_fix_with_stage_note(monkeypatch, tmp_path):
    calls = []

    class FakeRS:
        def __init__(self, run_id):
            self.run_dir = tmp_path

    import maestro.codegen.build_chain as build_chain
    import maestro.state
    monkeypatch.setattr(maestro.state, "RunState", FakeRS)
    monkeypatch.setattr(build_chain, "kickoff",
                        lambda run_id, *, kind, note, max_steps: calls.append(
                            {"kind": kind, "note": note, "max_steps": max_steps}))
    stages.save(tmp_path, "orig", ["one", "two"])
    assert stages.advance("r1") is True
    assert calls == [{"kind": "fix", "note": "two", "max_steps": stages.STAGE_MAX_STEPS}]
    assert stages.advance("r1") is False
