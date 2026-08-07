"""The TURN LOG: a build's conversation lives in its run dir, one copy of each message.

Every build turn is an llm job whose request is the WHOLE transcript so far, so a jobs row per turn
stored the same conversation once per turn (measured 2026-07-31: 951 MB of jobs.payload, 26 MB for
one 117-turn build). The driver now appends each landed turn to runs/<id>/turns.jsonl and clears
the row — append first, so there is never a moment with neither copy.
"""

import json

import pytest

import maestro.state
from api.routers import prompts
from auth import store as auth_store
from db import store as db_store
from maestro.codegen import build_chain, build_state, build_steps, turn_log
from maestro.codegen.run import create_run
from maestro.state import RunState


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)
    # A window small enough that a couple of file writes crosses it, so a test can drive a real
    # compaction instead of describing one.
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 4000)
    user = auth_store.create_user("alice", "pw-pass1234", role="admin", email="alice@example.com")
    run_id = create_run(user.id)
    RunState(run_id).write_spec({"request": "make a game", "title": "Moon Miner"})
    db_store.charge_game(run_id, 1, 3600)
    return run_id


@pytest.fixture
def headers():
    def _issue(handle="admin"):
        u = auth_store.create_user(handle, "pw-pass1234", role="admin", email=f"{handle}@example.com")
        return {"Authorization": f"Bearer {auth_store.issue_token(u.id)}"}
    return _issue


@pytest.fixture
def cleared(monkeypatch):
    """Each job row exactly as it stood the moment before its body was cleared — the row the prompt
    log used to read, kept so a test can compare it against what the archive reads back."""
    seen = {}
    real = db_store.clear_job_body

    def spy(job_id):
        seen[job_id] = db_store.get_job(job_id)
        real(job_id)

    monkeypatch.setattr(db_store, "clear_job_body", spy)
    return seen


def _reply(calls=None, content="", prompt_tokens=10):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": f"c{i}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": message}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 20}}


def _pending():
    """The turn waiting on the queue, carrying the body that was enqueued for it."""
    job = db_store.claim_job("llm", "w1", 60)
    assert job is not None, "the build enqueued no turn"
    return job


def _land(run_id, build_id, reply):
    """One turn, exactly as a worker completes it: claim, complete, drive the next."""
    job = _pending()
    db_store.complete_job(job["id"], "w1", reply, None, 2.0)
    build_chain.on_completion(run_id, build_id, reply, None, job["id"], 2.0)
    return job


def _write(name, size=40):
    return ("write_file", {"path": name, "content": "x" * size})


def _log(run_id):
    return [json.loads(line) for line in
            turn_log.path(RunState(run_id).run_dir).read_text(encoding="utf-8").splitlines()]


def test_a_landed_turn_is_appended_and_its_row_emptied(run):
    build_id = build_chain.kickoff(run)
    job = _land(run, build_id, _reply([_write("index.html")]))

    kinds = [r["kind"] for r in _log(run)]
    assert kinds == ["meta", "turn"]
    record = _log(run)[1]
    assert record["job_id"] == job["id"]
    assert record["added"] == [{"role": "user", "content": "make a game"}]
    assert record["response"]["tool_calls"][0]["function"]["name"] == "write_file"
    assert (record["exec_seconds"], record["error"]) == (2.0, None)

    row = db_store.get_job(job["id"])
    assert row["payload"] == {} and row["result"] is None


def test_the_archive_reads_back_exactly_as_the_row_did(run, cleared, headers, app_client):
    """The prompt log's view of a turn must not change because its body moved to disk — the shape
    is the same reconstruction, off the record rather than off the row."""
    build_id = build_chain.kickoff(run)
    job = _land(run, build_id, _reply([_write("index.html")], content="writing the page"))

    view = app_client.get(f"/api/admin/prompts/turns/{job['id']}", headers=headers()).json()

    assert view == prompts._turn_view(cleared[job["id"]])
    assert view["system"] and view["messages"][0]["text"] == "make a game"
    assert [t["name"] for t in view["tools"]] == [
        "list_files", "read_file", "write_file", "edit_file", "generate_media", "compose_scene",
        "done"]
    assert view["response"]["tool_calls"][0]["name"] == "write_file"


def test_every_request_the_build_sent_replays_from_the_log(run, cleared):
    """The whole point of storing only what each turn ADDED: turn k is the system prompt, the tool
    schemas and every added slice up to k. Including across a compaction, which drops rounds from
    the live transcript that the log still has to account for."""
    build_id = build_chain.kickoff(run)
    jobs = [_land(run, build_id, _reply([_write("index.html", 3000)])),
            _land(run, build_id, _reply([_write("game.js", 3000)], prompt_tokens=3000)),
            _land(run, build_id, _reply([_write("style.css")]))]

    assert [r["kind"] for r in _log(run)] == ["meta", "turn", "turn", "compact", "turn"]
    for job in jobs:
        archived = turn_log.read_turn(RunState(run).run_dir, job["id"])
        assert job["payload"]["body"]["messages"] == (
            [{"role": "system", "content": archived["meta"]["system"]}] + archived["messages"])
        assert job["payload"]["body"]["tools"] == archived["meta"]["tools"]


def test_a_compaction_records_the_rounds_it_dropped_and_its_note(run):
    build_id = build_chain.kickoff(run)
    _land(run, build_id, _reply([_write("index.html", 3000)]))
    _land(run, build_id, _reply([_write("game.js", 3000)], prompt_tokens=3000))

    (event,) = [r for r in _log(run) if r["kind"] == "compact"]
    assert event["dropped"] >= 1
    assert "index.html" in event["note"]


def test_the_meta_record_is_written_once_per_build(run):
    build_id = build_chain.kickoff(run)
    for _ in range(3):
        _land(run, build_id, _reply([_write("index.html")]))

    assert [r["kind"] for r in _log(run)].count("meta") == 1
    assert build_state.load(RunState(run).run_dir).meta_logged is True


def test_a_fix_appends_its_own_build_and_keeps_the_first(run):
    """A fix re-enters the same turn machine on the same run dir. Its transcript starts over, so it
    gets its own meta — and truncating the file would throw away the build being fixed."""
    build_id = build_chain.kickoff(run)
    _land(run, build_id, _reply([_write("index.html")]))
    fix_id = build_chain.kickoff(run, kind="fix", note="the ship never moves")
    fix_job = _land(run, fix_id, _reply([_write("game.js")]))

    metas = [r for r in _log(run) if r["kind"] == "meta"]
    assert [m["build_id"] for m in metas] == [build_id, fix_id]
    # The fix's first request is the note, not a replay of the build it is fixing.
    archived = turn_log.read_turn(RunState(run).run_dir, fix_job["id"])
    assert len(archived["messages"]) == 1
    assert "the ship never moves" in archived["messages"][0]["content"]


def test_an_append_that_fails_leaves_the_row_holding_the_body(run, monkeypatch):
    """The append and the sqlite write cannot be one transaction, so the order is the guarantee:
    there is never a moment where neither copy exists."""
    build_id = build_chain.kickoff(run)
    job = _pending()
    reply = _reply([_write("index.html")])
    db_store.complete_job(job["id"], "w1", reply, None, 2.0)

    def full(*a, **kw):
        raise OSError("No space left on device")

    monkeypatch.setattr(turn_log, "append_turn", full)
    with pytest.raises(OSError):
        build_chain.on_completion(run, build_id, reply, None, job["id"], 2.0)

    row = db_store.get_job(job["id"])
    assert row["payload"]["body"]["messages"]
    assert row["result"]["choices"]


def test_the_turn_index_measures_archived_turns_from_the_log(run, headers, app_client):
    """The index reads sizes and the system prompt's head off the row — which is empty once the
    body moves. It fills those in from the log rather than showing an unnamed turn of zero KB."""
    build_id = build_chain.kickoff(run)
    _land(run, build_id, _reply([_write("index.html", 3000)]))
    _land(run, build_id, _reply([_write("game.js")]))

    rows = app_client.get(
        f"/api/admin/prompts/turns?scope=game&game_id={run}", headers=headers()).json()

    # Two landed turns, read out of the log, plus the one still on the queue with its own body.
    assert [r["status"] for r in rows] == ["done", "done", "pending"]
    assert all(r["system_head"] for r in rows)
    assert len({r["system_hash"] for r in rows}) == 1
    assert [r["n_messages"] for r in rows] == [1, 3, 5]
    assert rows[0]["payload_chars"] < rows[1]["payload_chars"] < rows[2]["payload_chars"]

