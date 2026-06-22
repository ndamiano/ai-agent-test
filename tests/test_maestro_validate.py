import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.validate import run_check, validate


def test_malformed_check_is_a_failure_not_a_crash(tmp_path):
    # A spec is LLM-authored: a done-condition can be a bare prose string. That must be a reported
    # failure, never an AttributeError that 500s the games API or kills a build step.
    ok, detail, _ = run_check("Window opens at 1000x700", {}, None)
    assert ok is False and "malformed" in detail

    state = RunState(tmp_path)
    spec = Spec({"frozen": True, "components": [
        {"id": "engine_core", "done_conditions": ["prose, not a typed check",
                                                  {"type": "exists", "path": "x.y"}]}]})
    fails = validate(spec, state)                       # must not raise
    assert any("malformed" in (f.get("detail") or "") for f in fails)
    # the skip_types path (the sub-loop's cheap validate) must also tolerate the string
    assert validate(spec, state, skip_types={"compiles"}) is not None


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
    ok, detail, _ = _ok({"type": "exists", "path": "premise.missing"})
    assert ok is False and "missing or empty" in detail


def test_count_min_max_eq():
    assert _ok({"type": "count", "path": "premise.characters", "min": 3})[0] is True
    assert _ok({"type": "count", "path": "premise.characters", "max": 2})[0] is False
    assert _ok({"type": "count", "path": "premise.endings", "eq": 3})[0] is True
    assert _ok({"type": "count", "path": "premise.endings", "eq": 4})[0] is False


def test_distinct_pass_and_dupe():
    assert _ok({"type": "distinct", "path": "premise.characters", "key": "id"})[0] is True
    dupe_art = {"xs": [{"id": "a"}, {"id": "a"}]}
    ok, detail, _ = run_check({"type": "distinct", "path": "xs", "key": "id"}, dupe_art, None)
    assert ok is False and "duplicate" in detail


def test_each_has():
    assert _ok({"type": "each_has", "path": "premise.characters", "fields": ["name", "voice"]})[0] is True
    bad = {"xs": [{"name": "A"}, {"name": "B"}]}  # missing voice
    ok, detail, _ = run_check({"type": "each_has", "path": "xs", "fields": ["voice"]}, bad, None)
    assert ok is False and "missing 'voice'" in detail


# ── character-core floor: the locked portable-character field set ───────────

_CORE_FIELDS = ["id", "name", "voice", "temperament", "drive",
                "history", "competencies", "example_lines"]


def test_character_core_floor_rejects_thin_character():
    # The pre-change shape (id+name+voice) no longer clears the floor.
    thin = {"premise": {"characters": [{"id": "a", "name": "A", "voice": "wry"}]}}
    ok, detail, _ = run_check(
        {"type": "each_has", "path": "premise.characters", "fields": _CORE_FIELDS}, thin, None)
    assert ok is False and "missing 'temperament'" in detail


def test_character_core_floor_rejects_empty_list_fields():
    # history/competencies/example_lines must be non-empty lists, not [].
    empty = {"premise": {"characters": [{
        "id": "a", "name": "A", "voice": "wry", "temperament": "calm",
        "drive": "save as many as she can", "history": [], "competencies": ["triage"],
        "example_lines": ["he'll live, next"],
    }]}}
    ok, detail, _ = run_check(
        {"type": "each_has", "path": "premise.characters", "fields": _CORE_FIELDS}, empty, None)
    assert ok is False and "missing 'history'" in detail


def test_character_core_floor_passes_full_character():
    full = {"premise": {"characters": [{
        "id": "vesna_kol", "name": "Vesna Kol", "voice": "plain, fast, clinical",
        "temperament": "calm, decisive", "drive": "save as many as she can",
        "history": ["triaged her own brother to save a ward"],
        "competencies": ["triage", "field surgery"],
        "example_lines": ["I'd rather save one than none.", "He'll live. Next."],
    }]}}
    ok, _, _ = run_check(
        {"type": "each_has", "path": "premise.characters", "fields": _CORE_FIELDS}, full, None)
    assert ok is True


def test_refs_resolve_pass_and_fail():
    good = {"type": "refs_resolve", "from": "node_scripts.node_ids", "to": "graph.nodes", "to_key": "id"}
    assert _ok(good)[0] is True

    broken_art = {**ART, "node_scripts": {"node_ids": ["n1", "n9"]}}
    ok, detail, _ = run_check(good, broken_art, None)
    assert ok is False and "n9" in detail


def test_unknown_and_malformed_checks():
    ok, detail, _ = run_check({"type": "bogus"}, ART, None)
    assert ok is False and "unknown check type" in detail
    ok, detail, _ = run_check({"type": "count"}, ART, None)  # no path
    assert ok is False and "malformed" in detail


def test_compiles_check_delegates(monkeypatch):
    import renpy.compiler as compiler
    monkeypatch.setattr(compiler, "compile_renpy", lambda wd, **kw: {"ok": True, "reason": None})
    assert run_check({"type": "compiles"}, ART, "/tmp/whatever")[0] is True

    monkeypatch.setattr(compiler, "compile_renpy", lambda wd, **kw: {"ok": False, "reason": "2 lint error(s)"})
    ok, detail, _ = run_check({"type": "compiles"}, ART, "/tmp/whatever")
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


def test_validate_skip_types(tmp_path):
    state = RunState(tmp_path)
    spec = Spec({"components": [
        {"id": "node_scripts", "done_conditions": [
            {"type": "exists", "path": "node_scripts.x"},
            {"type": "compiles"},
        ]},
    ]})
    # Both fail normally; skipping compiles avoids the expensive build and leaves only exists.
    types = {f["check"]["type"] for f in validate(spec, state)}
    assert types == {"exists", "compiles"}
    skipped = {f["check"]["type"] for f in validate(spec, state, skip_types={"compiles"})}
    assert skipped == {"exists"}
