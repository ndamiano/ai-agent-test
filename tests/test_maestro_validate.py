import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.validate import run_check, validate


# ── individual checks (artifact dict, no run dir needed except compiles) ─────

ART = {
    "premise": {
        "central_question": "Will Alex stay?",
        "characters": [
            {"id": "alex", "name": "Alex", "voice": "wry"},
            {"id": "sam", "name": "Sam", "voice": "blunt"},
            {"id": "kit", "name": "Kit", "voice": "soft"},
        ],
        "endings": [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}],
    },
    "graph": {"nodes": [{"id": "n1"}, {"id": "n2"}]},
    "node_scripts": {"node_ids": ["n1", "n2"]},
}


def _ok(check, artifact=ART, run_dir=None):
    return run_check(check, artifact, run_dir)


def test_exists_pass_and_fail():
    assert _ok({"type": "exists", "path": "premise.central_question"})[0] is True
    ok, detail = _ok({"type": "exists", "path": "premise.missing"})
    assert ok is False and "missing or empty" in detail


def test_count_min_max_eq():
    assert _ok({"type": "count", "path": "premise.characters", "min": 3})[0] is True
    assert _ok({"type": "count", "path": "premise.characters", "max": 2})[0] is False
    assert _ok({"type": "count", "path": "premise.endings", "eq": 3})[0] is True
    assert _ok({"type": "count", "path": "premise.endings", "eq": 4})[0] is False


def test_distinct_pass_and_dupe():
    assert _ok({"type": "distinct", "path": "premise.characters", "key": "id"})[0] is True
    dupe_art = {"xs": [{"id": "a"}, {"id": "a"}]}
    ok, detail = run_check({"type": "distinct", "path": "xs", "key": "id"}, dupe_art, None)
    assert ok is False and "duplicate" in detail


def test_each_has():
    assert _ok({"type": "each_has", "path": "premise.characters", "fields": ["name", "voice"]})[0] is True
    bad = {"xs": [{"name": "A"}, {"name": "B"}]}  # missing voice
    ok, detail = run_check({"type": "each_has", "path": "xs", "fields": ["voice"]}, bad, None)
    assert ok is False and "missing 'voice'" in detail


def test_refs_resolve_pass_and_fail():
    good = {"type": "refs_resolve", "from": "node_scripts.node_ids", "to": "graph.nodes", "to_key": "id"}
    assert _ok(good)[0] is True

    broken_art = {**ART, "node_scripts": {"node_ids": ["n1", "n9"]}}
    ok, detail = run_check(good, broken_art, None)
    assert ok is False and "n9" in detail


def test_unknown_and_malformed_checks():
    ok, detail = run_check({"type": "bogus"}, ART, None)
    assert ok is False and "unknown check type" in detail
    ok, detail = run_check({"type": "count"}, ART, None)  # no path
    assert ok is False and "malformed" in detail


def test_compiles_check_delegates(monkeypatch):
    import renpy.compiler as compiler
    monkeypatch.setattr(compiler, "compile_renpy", lambda wd: {"ok": True, "reason": None})
    assert run_check({"type": "compiles"}, ART, "/tmp/whatever")[0] is True

    monkeypatch.setattr(compiler, "compile_renpy", lambda wd: {"ok": False, "reason": "2 lint error(s)"})
    ok, detail = run_check({"type": "compiles"}, ART, "/tmp/whatever")
    assert ok is False and "2 lint error" in detail


# ── validate over a spec + durable state ─────────────────────────────────────

def test_validate_returns_failure_list(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"characters": [{"id": "a", "name": "A", "voice": "x"}]})

    spec = Spec({
        "frozen": True,
        "components": [{
            "id": "premise",
            "done_conditions": [
                {"type": "exists", "path": "premise.central_question"},   # fails
                {"type": "count", "path": "premise.characters", "min": 1},  # passes
            ],
        }],
    })

    failures = validate(spec, state)
    assert len(failures) == 1
    assert failures[0]["component_id"] == "premise"
    assert failures[0]["check"]["type"] == "exists"


def test_validate_empty_when_all_satisfied(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {
        "central_question": "Q?",
        "characters": [{"id": "a", "name": "A", "voice": "x"}],
    })
    spec = Spec({"frozen": True, "components": [{
        "id": "premise",
        "done_conditions": [
            {"type": "exists", "path": "premise.central_question"},
            {"type": "count", "path": "premise.characters", "min": 1},
        ],
    }]})
    assert validate(spec, state) == []


def test_validate_scoped_to_component(tmp_path):
    state = RunState(tmp_path)
    spec = Spec({"components": [
        {"id": "a", "done_conditions": [{"type": "exists", "path": "a.x"}]},
        {"id": "b", "done_conditions": [{"type": "exists", "path": "b.y"}]},
    ]})
    failures = validate(spec, state, component_id="b")
    assert {f["component_id"] for f in failures} == {"b"}
