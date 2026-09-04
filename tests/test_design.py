"""The design stage: the ask goes in verbatim, a systems design comes back as the run's prompt.

The seam that matters is spec.json. `request` ABSENT means the design is still being written — the
create page reads nothing else to know that — and every way the design can fail lands the ask
itself as the request, because a designer that dies must never cost the user their build.
"""

import pytest

from maestro.codegen import build_chain, design
import maestro.codegen.run as run_mod
from maestro.state import RunState

ASK = "Make me an f1 racing game."
DESIGN = "Build this top-down F1 racing game. It is made of these systems.\nSYSTEMS: Track, Car."


def _reply(content):
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(run_mod, "_emit", lambda et, rid, **p: captured.append((et, rid, p)))
    return captured


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(run_mod.db_store, "create_game", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.db_store, "update_prompt_meta", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.db_store, "charge_game", lambda *a, **k: None)


@pytest.fixture
def kicked(monkeypatch):
    """The builds the design's completion starts."""
    started = []
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: started.append((rid, kw)) or "bid")
    return started


@pytest.fixture
def connector(monkeypatch):
    """The designer's one call. `reply` is whatever the fake hands back; `raises` makes it die."""
    class Fake:
        reply = _reply(DESIGN)
        raises = None
        model_name = "m"
        reasoning = "medium"
        seen = []

        def generate_with_tools(self, msgs, tools, max_tokens=None, **kw):
            if Fake.raises:
                raise Fake.raises
            Fake.seen.append(msgs)
            return Fake.reply

        def build_llm_job(self, msgs, tools, max_tokens):
            Fake.seen.append(msgs)
            return {"body": {"messages": msgs}}, "m"

    Fake.seen = []
    monkeypatch.setattr(design, "get_connector", lambda: Fake())
    return Fake


def test_the_ask_is_stored_verbatim_with_no_request_yet(tmp_runs, events, connector, monkeypatch):
    """`request` absent is the ONLY signal the create page has for "still designing" — an empty
    string there would read as a prompt the human may edit and build."""
    enqueued = []
    monkeypatch.setattr(design.db_store, "enqueue_job",
                        lambda q, p, **kw: enqueued.append((q, kw)) or "job1")

    run_id = run_mod.create_run("u1")
    spec = run_mod.propose_prompt(ASK, run_id)

    assert spec["ask"] == ASK and "request" not in spec
    assert RunState(run_id).read_spec() == spec
    assert enqueued[0][0] == "llm"
    assert enqueued[0][1]["metadata"] == {"stage": "design", "run_id": run_id}
    # Nothing is announced yet: the human has nothing to read until the design lands.
    assert events == []


def test_the_landed_design_becomes_the_prompt_and_the_build_starts(
        tmp_runs, events, connector, kicked, monkeypatch):
    """What the completion writes is what the build sends, and the build starts on it at once —
    nobody reads the design first."""
    monkeypatch.setattr(design.db_store, "enqueue_job", lambda *a, **kw: "job1")
    run_id = run_mod.create_run("u1")
    run_mod.propose_prompt(ASK, run_id)

    design.on_complete(run_id, _reply(DESIGN), None)

    spec = RunState(run_id).read_spec()
    assert spec["request"] == DESIGN
    assert spec["ask"] == ASK          # the words the designer read are kept
    assert [e[0] for e in events] == ["prompt_proposed"]
    assert kicked == [(run_id, {"kind": "build"})]


def test_a_refused_kickoff_leaves_the_design_landed(tmp_runs, events, connector, monkeypatch):
    """A build the budget refuses must not lose the design — the run stays designed and idle, and
    the page's Build button retries it."""
    monkeypatch.setattr(design.db_store, "enqueue_job", lambda *a, **kw: "job1")
    monkeypatch.setattr(build_chain, "kickoff",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no compute")))
    run_id = run_mod.create_run("u1")
    run_mod.propose_prompt(ASK, run_id)

    design.on_complete(run_id, _reply(DESIGN), None)

    assert RunState(run_id).read_spec()["request"] == DESIGN
    assert [e[0] for e in events] == ["prompt_proposed"]


@pytest.mark.parametrize("result, error", [
    (None, "the worker died"),          # the job failed
    (_reply(""), None),                 # the model answered nothing
    (_reply("   \n "), None),           # ...or only whitespace
    ({}, None),                         # a reply with no choices at all
])
def test_a_design_that_never_lands_leaves_the_ask_as_the_prompt(
        tmp_runs, events, connector, kicked, monkeypatch, result, error):
    """A dead designer must not cost the user their build: the words they wrote build instead."""
    monkeypatch.setattr(design.db_store, "enqueue_job", lambda *a, **kw: "job1")
    run_id = run_mod.create_run("u1")
    run_mod.propose_prompt(ASK, run_id)

    design.on_complete(run_id, result, error)

    assert RunState(run_id).read_spec()["request"] == ASK
    assert [e[0] for e in events] == ["prompt_proposed"]
    assert [r for r, _ in kicked] == [run_id]


def test_an_enqueue_that_is_refused_lands_the_ask_immediately(tmp_runs, events, connector,
                                                              monkeypatch):
    """A refused enqueue (no compute, no queue) has no completion coming, so the fallback has to
    happen here or the run designs forever."""
    monkeypatch.setattr(design.db_store, "enqueue_job",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no compute")))
    run_id = run_mod.create_run("u1")

    run_mod.propose_prompt(ASK, run_id)

    assert RunState(run_id).read_spec()["request"] == ASK
    assert [e[0] for e in events] == ["prompt_proposed"]


def test_the_designer_reads_the_ask_through_its_own_prompt_file(tmp_runs, connector, monkeypatch):
    """One .txt per LLM call: the ask is interpolated into prompts/design.txt, never inlined."""
    run_id = run_mod.create_run("u1")
    design.generate(run_id, ASK)

    sent = connector.seen[0][-1]["content"]
    assert ASK in sent
    assert "SYSTEMS:" in sent and "{request}" not in sent


def test_the_human_edit_moves_only_the_request(tmp_runs, events, connector, kicked, monkeypatch):
    """The ask is the record of what was actually wanted; editing the design must not rewrite it,
    and the title stays the user's words rather than the design's first line."""
    monkeypatch.setattr(design.db_store, "enqueue_job", lambda *a, **kw: "job1")
    run_id = run_mod.create_run("u1")
    run_mod.propose_prompt(ASK, run_id)
    design.on_complete(run_id, _reply(DESIGN), None)
    title = RunState(run_id).read_spec()["title"]

    run_mod.set_prompt(run_id, DESIGN + "\nAUDIO: engine tone.")

    spec = RunState(run_id).read_spec()
    assert spec["ask"] == ASK and spec["title"] == title
    assert spec["request"].endswith("AUDIO: engine tone.")
    assert events[-1][0] == "prompt_updated"


def test_the_cli_designs_before_it_builds(tmp_runs, events, connector, monkeypatch, capsys):
    """The CLI has no completion handler to wait on, so its design is synchronous — and what it
    leaves on disk is the design, which is what --build then sends."""
    monkeypatch.setattr(run_mod.store, "list_users", lambda: [type("U", (), {"id": "u1"})()])

    run_id = run_mod._new_run(ASK)

    spec = RunState(run_id).read_spec()
    assert spec["request"] == DESIGN and spec["ask"] == ASK
    assert "designing" in capsys.readouterr().out
