"""The design stage: four llm calls write the spec and the build starts on it."""

import pytest

import maestro.codegen.run as run_mod
from maestro.codegen import build_chain, design, staging
from maestro.state import RunState

ASK = "Make me an f1 racing game."
DOCS = {"gameplay": "# gameplay.md\nLaps: 3.", "visual": "# visual.md\nRed cars.",
        "engineering": "# engineering.md\nFiles: sim.js."}
SECTIONS = ["0. SCOPE", "1. CONVENTIONS", "2. CONTRACTS", "3. VISUAL SPEC", "4. GAMEPLAY SPEC",
            "5. CHARACTERS", "6. AUDIO", "7. UX", "8. DEBUG API", "9. TESTS", "10. BUILD ORDER",
            "11. DEFINITION OF DONE", "A. SANITY"]
SPEC = "# BUILD SPEC\n\nEvery number is final.\n\n" + "\n\n".join(f"# {h}\n\nbody of {h}" for h in SECTIONS)
HALF = SPEC[:SPEC.index("# 7. UX")]
REST = SPEC[SPEC.index("# 7. UX"):]


def _reply(content):
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(run_mod, "_emit", lambda et, rid, **p: captured.append((et, rid, p)))
    return captured


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(run_mod.games, "create_game", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.games, "update_prompt_meta", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.games, "charge_game", lambda *a, **k: None)
    monkeypatch.setattr(design.games, "create_build", lambda *a, **k: "dbuild")
    monkeypatch.setattr(design.games, "build_started", lambda *a, **k: None)


@pytest.fixture
def finished(monkeypatch):
    out = []
    monkeypatch.setattr(design.games, "build_finished",
                        lambda bid, status, steps=None: out.append((bid, status)))
    return out


@pytest.fixture
def kicked(monkeypatch):
    started = []
    monkeypatch.setattr(build_chain, "kickoff", lambda rid, **kw: started.append((rid, kw)) or "bid")
    return started


@pytest.fixture
def enqueued(monkeypatch):
    out = []

    def _enqueue(queue, payload, **kw):
        out.append((kw["metadata"]["step"], payload["body"]["messages"][-1]["content"],
                    kw["metadata"], kw.get("build_id")))
        return f"job{len(out)}"
    monkeypatch.setattr(design.jobs, "enqueue_job", _enqueue)
    return out


@pytest.fixture
def connector(monkeypatch):
    class Fake:
        replies = []
        model_name = "m"
        reasoning = "medium"
        seen = []

        def generate_with_tools(self, msgs, tools, max_tokens=None, **kw):
            Fake.seen.append(msgs)
            return _reply(Fake.replies.pop(0))

        def build_llm_job(self, msgs, tools, max_tokens):
            Fake.seen.append(msgs)
            return {"body": {"messages": msgs}}, "m"

    Fake.seen = []
    Fake.replies = []
    monkeypatch.setattr(design, "get_connector", lambda: Fake())
    return Fake


def _propose(run_id):
    return run_mod.propose_prompt(ASK, run_id)


def test_the_ask_is_stored_verbatim_and_gameplay_is_asked_first(tmp_runs, events, connector,
                                                                 enqueued):
    run_id = run_mod.create_run("u1")
    spec = _propose(run_id)

    assert spec["ask"] == ASK and "request" not in spec
    assert RunState(run_id).read_spec() == spec
    assert [e[0] for e in enqueued] == ["gameplay"]
    assert enqueued[0][2] == {"stage": "design", "run_id": run_id, "step": "gameplay"}
    assert ASK in enqueued[0][1] and "{request}" not in enqueued[0][1]
    assert events == []


def test_the_chain_fans_out_after_gameplay_and_joins_at_the_integrator(
        tmp_runs, events, connector, enqueued, kicked, finished):
    run_id = run_mod.create_run("u1")
    _propose(run_id)

    design.on_complete(run_id, "dbuild", _reply(DOCS["gameplay"]), None, "gameplay")
    assert [e[0] for e in enqueued] == ["gameplay", "visual", "engineering"]
    assert all(DOCS["gameplay"] in e[1] for e in enqueued[1:])
    assert all(e[3] == "dbuild" for e in enqueued)

    design.on_complete(run_id, "dbuild", _reply(DOCS["visual"]), None, "visual")
    assert [e[0] for e in enqueued] == ["gameplay", "visual", "engineering"]
    design.on_complete(run_id, "dbuild", _reply(DOCS["engineering"]), None, "engineering")
    assert [e[0] for e in enqueued][-1] == "integrate"
    assert all(doc in enqueued[-1][1] for doc in DOCS.values())
    assert "request" not in RunState(run_id).read_spec() and kicked == []

    design.on_complete(run_id, "dbuild", _reply(SPEC), None, "integrate")

    spec = RunState(run_id).read_spec()
    assert spec["ask"] == ASK and ASK in spec["request"] and "design/design.md" in spec["request"]
    assert staging.spec_path(RunState(run_id).run_dir).read_text() == SPEC
    assert (RunState(run_id).run_dir / "design" / "gameplay.md").read_text() == DOCS["gameplay"]
    assert [e[0] for e in events] == ["prompt_proposed"]
    assert kicked == [(run_id, {"kind": "build"})]
    assert finished == [("dbuild", "succeeded")]


def test_an_integrator_that_stops_early_is_asked_to_continue(tmp_runs, events, connector,
                                                             enqueued, kicked):
    run_id = run_mod.create_run("u1")
    _propose(run_id)
    for step in ("gameplay", "visual", "engineering"):
        design.on_complete(run_id, "dbuild", _reply(DOCS[step]), None, step)

    design.on_complete(run_id, "dbuild", _reply(HALF), None, "integrate")

    assert enqueued[-1][0] == "integrate" and HALF in enqueued[-1][1]
    assert "Continue it from that point" in enqueued[-1][1]
    assert kicked == []

    design.on_complete(run_id, "dbuild", _reply(REST), None, "integrate")

    assert staging.spec_path(RunState(run_id).run_dir).read_text() == HALF + REST
    assert kicked == [(run_id, {"kind": "build"})]


def test_an_empty_reply_is_asked_for_again_before_the_ask_is_the_prompt(
        tmp_runs, events, connector, enqueued, kicked, finished):
    run_id = run_mod.create_run("u1")
    _propose(run_id)
    design.on_complete(run_id, "dbuild", _reply(DOCS["gameplay"]), None, "gameplay")
    asked = len(enqueued)

    design.on_complete(run_id, "dbuild", _reply(""), None, "engineering")

    assert [e[0] for e in enqueued[asked:]] == ["engineering"]
    assert enqueued[-1][1] == enqueued[-2][1]
    assert RunState(run_id).read_spec().get("request") is None
    assert kicked == [] and finished == []


@pytest.mark.parametrize("step, result, error, replies", [
    ("gameplay", None, "the worker died", 1),
    ("visual", _reply(""), None, 2),
    ("integrate", {}, None, 2),
])
def test_a_step_that_never_lands_leaves_the_ask_as_the_prompt(
        tmp_runs, events, connector, enqueued, kicked, finished, step, result, error, replies):
    run_id = run_mod.create_run("u1")
    _propose(run_id)

    for _ in range(replies):
        design.on_complete(run_id, "dbuild", result, error, step)

    assert RunState(run_id).read_spec()["request"] == ASK
    assert [e[0] for e in events] == ["prompt_proposed"]
    assert [r for r, _ in kicked] == [run_id]
    assert finished == [("dbuild", "failed")]


def test_an_enqueue_that_is_refused_lands_the_ask_immediately(tmp_runs, events, connector,
                                                              monkeypatch):
    monkeypatch.setattr(design.jobs, "enqueue_job",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no compute")))
    run_id = run_mod.create_run("u1")

    _propose(run_id)

    assert RunState(run_id).read_spec()["request"] == ASK
    assert [e[0] for e in events] == ["prompt_proposed"]


def test_the_human_edit_moves_only_the_request(tmp_runs, events, connector, enqueued, kicked):
    run_id = run_mod.create_run("u1")
    _propose(run_id)
    for step in ("gameplay", "visual", "engineering"):
        design.on_complete(run_id, "dbuild", _reply(DOCS[step]), None, step)
    design.on_complete(run_id, "dbuild", _reply(SPEC), None, "integrate")
    title = RunState(run_id).read_spec()["title"]

    run_mod.set_prompt(run_id, "A user asked for a game. Read design/ and build it.")

    spec = RunState(run_id).read_spec()
    assert spec["ask"] == ASK and spec["title"] == title
    assert spec["request"].endswith("build it.")
    assert events[-1][0] == "prompt_updated"


def test_the_cli_runs_the_whole_chain_before_it_builds(tmp_runs, events, connector, monkeypatch,
                                                       finished, capsys):
    monkeypatch.setattr(run_mod.store, "list_users", lambda: [type("U", (), {"id": "u1"})()])
    connector.replies = [DOCS["gameplay"], DOCS["visual"], "", DOCS["engineering"], HALF, REST]

    run_id = run_mod._new_run(ASK)

    spec = RunState(run_id).read_spec()
    assert ASK in spec["request"] and spec["ask"] == ASK
    assert staging.spec_path(RunState(run_id).run_dir).read_text() == HALF + REST
    assert len(connector.seen) == 6 and connector.seen[2] == connector.seen[3]
    assert finished == [("dbuild", "succeeded")]
    assert "designing" in capsys.readouterr().out
