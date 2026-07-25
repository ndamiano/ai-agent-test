"""build_chain — the completion-driven build driver.

The end-to-end drive (plan → data → author → gates → fix, to green) is exercised in test_codegen via
the harness. These tests pin the driver MECHANICS that replace the old in-memory build queue: a turn
is a stage-tagged `llm` job, pause halts the chain, a refused budget ends the build, and the reaper's
stuck-build query finds a run whose driver died mid-turn.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import store as db_store
from maestro.codegen import build_chain, build_state
from maestro.run_control import get_or_create, remove
from maestro.state import RunState

_SPEC = {"frozen": True, "mode": "2d", "design": {"control": {"scheme": "top-down"}}}


class FakeConn:
    """Builds a job payload carrying the raw messages; every turn's reply is unparseable, which is
    enough to exercise the driver's plumbing (plan falls back, etc.)."""

    def build_llm_job(self, messages, schemas=None, max_tokens=None, reasoning=None):
        return {"messages": messages, "tools": schemas, "reasoning": reasoning}, "model"

    def to_chat(self, raw):
        return raw

    def generate_with_tools(self, messages, tools=None, **kw):
        return {"choices": [{"message": {"content": "garbage"}}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(build_chain, "stage_for_play", lambda *a, **k: None)
    monkeypatch.setattr(build_chain, "get_connector", lambda: FakeConn())
    run_id = str(tmp_path)
    db_store.create_game(run_id, "u1")
    RunState(run_id).write_spec(_SPEC)
    yield run_id, tmp_path
    remove(run_id)


def test_kickoff_enqueues_a_stage_tagged_build_turn(env, monkeypatch):
    run_id, _ = env
    captured = []
    monkeypatch.setattr(db_store, "enqueue_job",
                        lambda queue, payload, **kw: captured.append((queue, kw.get("metadata"))) or "j")

    build_chain.kickoff(run_id, kind="build")

    assert len(captured) == 1                          # exactly one turn in flight
    queue, meta = captured[0]
    assert queue == "llm"
    assert meta["stage"] == "build" and meta["run_id"] == run_id and meta["build_id"]
    assert db_store.game(run_id)["status"] == "building"
    assert build_chain.is_active(run_id) is True


def test_pause_halts_the_chain_without_enqueuing(env, monkeypatch):
    run_id, run_dir = env
    captured = []
    monkeypatch.setattr(db_store, "enqueue_job",
                        lambda queue, payload, **kw: captured.append(1) or "j")
    build_chain.kickoff(run_id, kind="build")          # turn 1 enqueued
    assert len(captured) == 1

    get_or_create(run_id).request_pause()
    events = []
    monkeypatch.setattr(build_chain, "_emit", lambda et, rid, **f: events.append(et))
    build_chain.advance(run_id, {"choices": [{"message": {"content": "garbage"}}]})

    assert "build_paused" in events
    assert len(captured) == 1                           # no new turn while paused
    assert build_state.load(run_dir).phase != "done"   # the build is suspended, not finished


def test_a_refused_budget_ends_the_build(env, monkeypatch):
    run_id, run_dir = env

    def refuse(queue, payload, **kw):
        raise db_store.InsufficientCompute(run_id, 0.0, 10.0)

    monkeypatch.setattr(db_store, "enqueue_job", refuse)
    build_chain.kickoff(run_id, kind="build")

    assert db_store.game(run_id)["status"] == "failed"
    cursor = build_state.load(run_dir)
    assert cursor.phase == "done" and cursor.ok is False


def test_stuck_builds_flags_a_run_with_no_inflight_turn(env):
    run_id, _ = env
    db_store.charge_game(run_id, 1, 10_000)
    jid = db_store.enqueue_job("llm", {"path": "x"}, game_id=run_id, build_id="b",
                               metadata={"stage": "build", "run_id": run_id, "build_id": "b"})
    db_store.claim_job("llm", "w", 60)
    db_store.complete_job(jid, "w", {"ok": True}, None, 1.0)   # the turn is terminal
    db_store.set_status(run_id, "building")

    # No pending/claimed build turn + a building status => stuck (grace<0 so the just-finished job
    # counts as old).
    assert run_id in db_store.stuck_builds(-1)

    # A pending build turn means it is NOT stuck — the chain is alive.
    db_store.enqueue_job("llm", {"path": "y"}, game_id=run_id, build_id="b",
                         metadata={"stage": "build", "run_id": run_id, "build_id": "b"})
    assert run_id not in db_store.stuck_builds(-1)


def test_reaper_readvance_of_a_mid_fix_build_does_not_crash(env, monkeypatch):
    """The reaper re-drives a stuck build via `advance(run_id)` with NO result — the completion that
    would have carried one was lost. A build suspended mid-fix must survive that: the lost turn
    becomes an empty turn the fix shape retries, not a NoneType crash that aborts the reaper tick."""
    run_id, run_dir = env
    db_store.charge_game(run_id, 1, 10_000)
    captured = []
    monkeypatch.setattr(db_store, "enqueue_job",
                        lambda queue, payload, **kw: captured.append(1) or "j")

    build_chain.kickoff(run_id, kind="build")            # turn 1 enqueued; cursor now mid-fix
    assert build_state.load(run_dir).phase == "fix"
    assert len(captured) == 1

    # Reaper path: no result to apply. Must not raise.
    build_chain.advance(run_id)

    cursor = build_state.load(run_dir)
    assert cursor.phase != "done"                        # re-driven, not wedged


def test_deterministic_pass_spends_no_step_and_fires_once_per_identity(env, monkeypatch):
    """Steps meter the MODEL's budget: a fix resolved by a deterministic pass must not consume one.
    The pathological-loop bound is by identity instead — one free pass per error identity; if the
    same identity survives its pass, _start_fix hands it to the LLM."""
    from maestro.codegen.build_chain import _start_fix
    from maestro.codegen.fix_classes import FixClass
    from maestro.modules.module import Error, ErrorType, idkey

    run_id, tmp_path = env
    rs = RunState(run_id)
    cursor = build_state.BuildCursor(build_id="b1")
    err = Error(type=ErrorType.FIX, code="typechecks", component="game",
                message="x", path="game.ts")

    calls = []
    fake_cls = FixClass(id="fake", matches=lambda e: True,
                        deterministic=lambda rd, e: (calls.append(1) or
                                                     {"changes": [("z", "game.ts")], "count": 1}))
    monkeypatch.setattr(build_chain, "classify", lambda e: fake_cls)
    monkeypatch.setattr(build_chain, "_emit_step", lambda *a, **k: None)

    # First encounter: pass runs, fix resolved with no llm turn, step untouched, identity recorded.
    assert _start_fix(run_id, rs, cursor, err, stalled=False) is False
    assert cursor.step == 0
    assert calls == [1]
    assert idkey(err) in cursor.det_tried

    # Same identity again: the free pass is spent — straight to the LLM fix, pass not re-run.
    assert _start_fix(run_id, rs, cursor, err, stalled=False) is True
    assert calls == [1]
    assert cursor.phase == "fix"


def test_a_stalled_code_fix_gets_one_contract_ruling(env, monkeypatch):
    """A file authored faithfully to a wrong declaration is the file the gate blames, and the
    read→edit subloop can only ever edit that file. `amend` is the one shape allowed to rule the
    CONTRACT wrong, so a stalled fix is routed through it once per error identity."""
    from maestro.codegen import interfaces
    from maestro.codegen.build_chain import _start_fix
    from maestro.modules.module import Error, ErrorType, idkey

    run_id, tmp_path = env
    rs = RunState(run_id)
    interfaces.save(tmp_path, {"state": [], "invariants": [], "functions": [
        {"name": "f", "file": "game.ts", "signature": "f(): void", "purpose": "p",
         "reads": [], "writes": [], "calls": [], "invariants": []}]})
    cursor = build_state.BuildCursor(build_id="b1")
    err = Error(type=ErrorType.FIX, code="typechecks", component="game", message="x",
                path="game.ts")

    # Not stalled: the ordinary read→edit subloop.
    assert _start_fix(run_id, rs, cursor, err, stalled=False) is True
    assert cursor.fix_cursor().shape == "read_write"

    # Stalled on the same to-do: rule on the contract first.
    assert _start_fix(run_id, rs, cursor, err, stalled=True) is True
    assert cursor.fix_cursor().shape == "amend"
    assert idkey(err) in cursor.amend_tried

    # The ruling is spent — a still-stalled fix goes back to editing code, never loops on amend.
    assert _start_fix(run_id, rs, cursor, err, stalled=True) is True
    assert cursor.fix_cursor().shape == "read_write"


def test_two_errors_that_oscillate_still_reach_the_contract_ruling(env):
    """Each fix trades one error for the other, so no two consecutive to-do snapshots match and
    `stalled` never trips — yet oscillation is exactly what a wrong contract looks like."""
    from maestro.codegen import interfaces
    from maestro.codegen.build_chain import _start_fix
    from maestro.modules.module import Error, ErrorType, idkey

    run_id, tmp_path = env
    interfaces.save(tmp_path, {"state": [], "invariants": [], "functions": [
        {"name": "f", "file": "game.ts", "signature": "f(): void", "purpose": "p",
         "reads": [], "writes": [], "calls": [], "invariants": []}]})
    a = Error(type=ErrorType.FIX, code="typechecks", component="game", message="x", path="game.ts")
    b = Error(type=ErrorType.FIX, code="runs", component="game", message="y", path="main.ts")
    cursor = build_state.BuildCursor(build_id="b1")
    rs = RunState(run_id)
    # The sweeps alternate, so `stalled` is False every time.
    for err in (a, b, a, b):
        assert _start_fix(run_id, rs, cursor, err, stalled=False) is True
        assert cursor.fix_cursor().shape == "read_write"
    assert _start_fix(run_id, rs, cursor, a, stalled=False) is True   # third attempt on `a`
    assert cursor.fix_cursor().shape == "amend"
    assert idkey(a) in cursor.amend_tried


def test_an_error_waiting_its_turn_never_earns_a_contract_ruling(env):
    """A sweep's to-do lists every failing error. Counting recurrence there would hand a ruling to an
    error that sat behind higher-priority ones without a single line of it being edited."""
    from maestro.codegen import interfaces
    from maestro.codegen.build_chain import _start_fix
    from maestro.modules.module import Error, ErrorType, idkey

    run_id, tmp_path = env
    interfaces.save(tmp_path, {"state": [], "invariants": [], "functions": [
        {"name": "f", "file": "game.ts", "signature": "f(): void", "purpose": "p",
         "reads": [], "writes": [], "calls": [], "invariants": []}]})
    waiting = Error(type=ErrorType.FIX, code="typechecks", component="game", message="w",
                    path="storm.ts")
    cursor = build_state.BuildCursor(build_id="b1")
    # It has been in the to-do for many sweeps, but has never been the error a fix was entered for.
    cursor.recent = [["other", idkey(waiting)] for _ in range(6)]
    assert _start_fix(run_id, RunState(run_id), cursor, waiting, stalled=False) is True
    assert cursor.fix_cursor().shape == "read_write"


def test_an_error_seen_once_or_twice_is_not_yet_a_contract_problem(env):
    """Two encounters is ordinary iteration — spending the ruling there wastes it."""
    from maestro.codegen import interfaces
    from maestro.codegen.build_chain import _start_fix
    from maestro.modules.module import Error, ErrorType, idkey

    run_id, tmp_path = env
    interfaces.save(tmp_path, {"state": [], "invariants": [], "functions": [
        {"name": "f", "file": "game.ts", "signature": "f(): void", "purpose": "p",
         "reads": [], "writes": [], "calls": [], "invariants": []}]})
    err = Error(type=ErrorType.FIX, code="typechecks", component="game", message="x",
                path="game.ts")
    cursor = build_state.BuildCursor(build_id="b1")
    cursor.recent = [[idkey(err)], ["other"], [idkey(err)]]
    assert _start_fix(run_id, RunState(run_id), cursor, err, stalled=False) is True
    assert cursor.fix_cursor().shape == "read_write"


def test_no_architecture_means_no_contract_ruling(env):
    """With nothing declared there is no contract to be wrong — amend would have nothing to patch."""
    from maestro.codegen.build_chain import _start_fix
    from maestro.modules.module import Error, ErrorType

    run_id, _ = env
    cursor = build_state.BuildCursor(build_id="b1")
    err = Error(type=ErrorType.FIX, code="typechecks", component="game", message="x",
                path="game.ts")
    assert _start_fix(run_id, RunState(run_id), cursor, err, stalled=True) is True
    assert cursor.fix_cursor().shape == "read_write"


# ── the early asset lane + build-vs-assets budget priority ───────────────────
def test_outer_sweep_starts_the_early_asset_lane_once(env, monkeypatch):
    """The lane fires from the outer sweep and its outcome sticks on the cursor: a real batch id
    is never re-attempted, and None (no plan yet) retries next sweep."""
    from maestro.codegen import reskin

    run_id, run_dir = env
    monkeypatch.setattr(db_store, "enqueue_job", lambda queue, payload, **kw: "j")
    calls = []

    def fake_early(rid, rdir, spec):
        calls.append(rid)
        return None if len(calls) == 1 else "batch123"

    monkeypatch.setattr(reskin, "start_assets_early", fake_early)

    build_chain.kickoff(run_id, kind="build")          # sweep 1: no plan yet
    assert build_state.load(run_dir).asset_batch is None
    build_chain.advance(run_id, {"choices": [{"message": {"content": "garbage"}}]})  # sweep 2
    assert build_state.load(run_dir).asset_batch == "batch123"

    n = len(calls)
    build_chain.advance(run_id, {"choices": [{"message": {"content": "garbage"}}]})
    assert len(calls) == n                             # settled — never re-attempted


def test_a_fix_build_never_starts_the_early_lane(env, monkeypatch):
    from maestro.codegen import reskin

    run_id, run_dir = env
    monkeypatch.setattr(db_store, "enqueue_job", lambda queue, payload, **kw: "j")
    monkeypatch.setattr(reskin, "start_assets_early",
                        lambda *a: pytest.fail("a note-scoped fix must not skin"))
    build_chain.kickoff(run_id, kind="fix", note="the player is stuck")


def test_a_refused_turn_preempts_queued_asset_renders(env, monkeypatch):
    """Budget priority: gameplay beats skin. A build turn refused for headroom abandons the run's
    still-pending asset jobs (releasing their reservations) and retries — the build proceeds and
    the batch finishes short, which is the asset stage's soft-degrade."""
    from maestro.codegen import reskin

    run_id, run_dir = env
    monkeypatch.setattr(reskin, "start_assets_early", lambda *a: "")
    # grant fits ONE image reservation (45s) but not image + the llm turn (30s)
    db_store.charge_game(run_id, 1, 60.0)
    asset_job = db_store.enqueue_job("image", {"kind": "comfy_image"}, game_id=run_id,
                                     batch_id="early", metadata={"asset_id": "goblin"})

    build_chain.kickoff(run_id, kind="build")

    assert db_store.get_job(asset_job)["status"] == "failed"          # preempted
    assert db_store.game(run_id)["status"] == "building"              # the build got its turn
    jobs = [j for j in db_store.batch_jobs("early")]
    assert all(j["status"] == "failed" for j in jobs)


def test_a_refused_turn_with_nothing_to_preempt_still_fails(env, monkeypatch):
    from maestro.codegen import reskin

    run_id, run_dir = env
    monkeypatch.setattr(reskin, "start_assets_early", lambda *a: "")
    db_store.charge_game(run_id, 1, 1.0)               # can't afford any turn, nothing queued

    build_chain.kickoff(run_id, kind="build")

    assert db_store.game(run_id)["status"] == "failed"
    assert build_state.load(run_dir).ok is False


def test_every_build_turn_disables_thinking():
    """A build turn that reaches the connector with reasoning=None skips the enable_thinking
    switch, and the model reasons instead of calling its tool — measured at 16000 tokens and no
    `write`. The floor belongs to Infer's default, so no call site can forget it."""
    from llm_clients.connector import LLMConnector
    from maestro.codegen.build_steps import Infer

    conn = LLMConnector(model="m", reasoning="none")
    inf = Infer([{"role": "user", "content": "author it"}], [], 100)
    payload, _ = conn.build_llm_job(inf.messages, inf.schemas, inf.max_tokens, inf.reasoning)
    assert payload["body"]["chat_template_kwargs"] == {"enable_thinking": False}
