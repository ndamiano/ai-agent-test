"""Drive a build_chain build to completion in-process, with a fake connector.

The real build is completion-driven: each llm turn is an `llm` job the control-plane's
`/worker/complete` advances. A unit test has no worker, so this harness stands in for that loop —
it captures each enqueued turn's payload, asks the test's fake connector for the turn's result, and
feeds it straight into `build_chain.advance`, exactly as `on_completion` would.

For the duration it monkeypatches: the connector seam (`build_chain.get_connector`), the enqueue
(`db_store.enqueue_job` — captured, never hits a real queue), and `build_chain.stage_for_play` (a
no-op so a green build does not copy into the real runtime/games dir). It also points the platform db
at a temp file under the run dir and ensures a games row exists, since kickoff records the attempt.

A fake connector only needs the old `generate_with_tools(messages, tools=None, **kw)` shape — the
harness wraps it with the `build_llm_job` seam the driver calls. Pass `run_id = str(tmp_path)` so the
run dir is the temp dir AND the id is a db-safe string.
"""

from db import store as db_store
from maestro.codegen import build_chain, build_state
from maestro.state import RunState


class _Adapter:
    """Wraps a test fake (generate_with_tools) with the build_chain enqueue/normalize seam. The
    payload just carries the raw chat messages so the harness can re-drive the fake directly."""

    def __init__(self, fake):
        self.fake = fake

    def build_llm_job(self, messages, schemas=None, max_tokens=None, reasoning=None):
        return {"messages": messages, "tools": schemas, "reasoning": reasoning}, "model"

    def to_chat(self, raw):
        return raw


def run_build_to_completion(run_id, fake, monkeypatch, *, kind="build", note="", max_turns=200):
    """Kick a build off and drive it turn-by-turn until its cursor is done. Returns the final
    BuildCursor (cursor.ok / cursor.step)."""
    rs = RunState(run_id)
    monkeypatch.setattr(db_store, "_db_path", lambda: rs.run_dir / "harness_platform.db")
    monkeypatch.setattr(build_chain, "stage_for_play", lambda *a, **k: None)
    if db_store.game(run_id) is None:
        db_store.create_game(run_id, "u1")

    captured = {}

    def fake_enqueue(queue, payload, **kw):
        captured["payload"] = payload
        captured["metadata"] = kw.get("metadata")
        return "job"

    monkeypatch.setattr(build_chain, "get_connector", lambda: _Adapter(fake))
    monkeypatch.setattr(db_store, "enqueue_job", fake_enqueue)
    # The asset lanes are exercised in their own tests; here they would enqueue image jobs into
    # `captured` (clobbering the llm turn the harness re-drives) and spawn auto-skin threads.
    monkeypatch.setattr(build_chain, "_maybe_early_assets", lambda *a, **k: None)
    monkeypatch.setattr(build_chain, "_auto_skin", lambda *a, **k: None)

    build_chain.kickoff(run_id, kind=kind, note=note)
    for _ in range(max_turns):
        cursor = build_state.load(rs.run_dir)
        if cursor is None or cursor.phase == "done":
            return cursor
        p = captured["payload"]
        result = fake.generate_with_tools(p["messages"], p["tools"], reasoning=p["reasoning"])
        build_chain.advance(run_id, result)
    raise AssertionError(f"build {run_id} did not finish within {max_turns} turns")
