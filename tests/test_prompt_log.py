"""The prompt log (admin): every llm turn ANY game spent is reconstructable from the jobs rows —
bucket list, per-bucket index (cheap, no bodies), one turn's full system/messages/tools/reply."""

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import maestro.state
from api.app import app
from auth import store as auth_store
from db import store as db_store
from maestro.codegen.run import create_run
from maestro.state import RunState


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)
    return TestClient(app)


def _user(handle="alice", role="admin"):
    u = auth_store.create_user(handle, "pw", role=role)
    auth_store.grant(u.id, 10, "admin_grant")
    return u, {"Authorization": f"Bearer {auth_store.issue_token(u.id)}"}


def _game(user_id, title="Moon Miner"):
    run_id = create_run(user_id)
    RunState(run_id).write_spec({"title": title, "mode": "2d", "frozen": True})
    db_store.update_spec_meta(run_id, title, "2d", True)
    db_store.charge_game(run_id, 1, 3600)   # a budget, so enqueue admits the turns
    return run_id


_PAYLOAD = {"path": "/v1/responses", "body": {
    "model": "qwen", "instructions": "You author ONE file.\nSecond line.",
    "reasoning": {"effort": "none"}, "max_output_tokens": 4096,
    "input": [
        {"type": "message", "role": "user", "content": "# TASK\nwrite game.ts"},
        {"type": "function_call", "call_id": "c1", "name": "read_file",
         "arguments": '{"file":"state.ts"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "export interface GameState {}"},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "ok"}]},
    ],
    "tools": [{"type": "function", "name": "write", "description": "create a file",
               "parameters": {"type": "object"}}]}}

_RESULT = {"id": "resp_1", "output": [
    {"type": "message", "role": "assistant",
     "content": [{"type": "output_text", "text": "authoring now"}]},
    {"type": "function_call", "call_id": "c2", "name": "write",
     "arguments": '{"file":"game.ts","code":"export const x = 1"}'},
], "usage": {"input_tokens": 900, "output_tokens": 120, "total_tokens": 1020}}


def _turn(run_id=None, build_id=None, payload=_PAYLOAD, result=_RESULT):
    job_id = db_store.enqueue_job("llm", payload, game_id=run_id, build_id=build_id,
                                  metadata={"stage": "build"} if run_id else {})
    if result is not None:
        assert db_store.claim_job("llm", "w1", 60)["id"] == job_id
        db_store.complete_job(job_id, "w1", result, None, 3.5)
    return job_id


def test_buckets_cover_every_game_plus_the_platforms_own_turns(client):
    admin, headers = _user()
    alice = auth_store.create_user("alice2", "pw")
    game_a, game_b = _game(admin.id, "Moon Miner"), _game(alice.id, "Cave Diver")
    _turn(game_a, db_store.create_build(game_a))
    _turn(game_a, db_store.create_build(game_a))
    _turn(game_b, db_store.create_build(game_b))
    _turn()   # chat / spec draft — no game to own it

    buckets = client.get("/api/admin/prompts/games", headers=headers).json()

    by_game = {b["game_id"]: b for b in buckets}
    assert by_game[game_a]["turns"] == 2
    assert by_game[game_a]["title"] == "Moon Miner"
    # Another user's game is in the log too — this surface is operator-wide, not per-owner.
    assert by_game[game_b]["title"] == "Cave Diver"
    assert by_game[None]["turns"] == 1


def test_scopes_select_all_one_game_or_the_platform(client):
    admin, headers = _user()
    run_id = _game(admin.id)
    owned = _turn(run_id, db_store.create_build(run_id))
    unowned = _turn()

    def ids(query):
        return [t["id"] for t in client.get(f"/api/admin/prompts/turns?{query}", headers=headers).json()]

    assert ids("scope=all") == [owned, unowned]
    assert ids(f"scope=game&game_id={run_id}") == [owned]
    assert ids("scope=platform") == [unowned]
    assert client.get("/api/admin/prompts/turns?scope=game", headers=headers).status_code == 400


def test_the_index_carries_no_bodies_and_skips_other_queues(client):
    admin, headers = _user()
    run_id = _game(admin.id)
    job_id = _turn(run_id, db_store.create_build(run_id))
    db_store.enqueue_job("image", {"prompt": "a goblin"}, game_id=run_id)

    (row,) = client.get(f"/api/admin/prompts/turns?scope=game&game_id={run_id}", headers=headers).json()

    assert row["id"] == job_id
    assert row["system_head"] == "You author ONE file.\nSecond line."
    assert (row["n_messages"], row["exec_seconds"]) == (4, 3.5)
    assert row["payload_chars"] > 0
    assert "payload" not in row and "result" not in row


def test_turns_sharing_a_system_prompt_share_a_hash(client):
    """A build's turns collapse onto a handful of system prompts (measured: 661 turns, 8 prompts),
    and that hash is the only handle on which PROMPT produced a turn — the file name is never
    recorded. Same text ⇒ same hash, whatever else differs about the turn."""
    admin, headers = _user()
    run_id = _game(admin.id)
    build_id = db_store.create_build(run_id)
    other_body = dict(_PAYLOAD["body"], instructions="You audit ONE claim.")
    same_prompt_new_messages = {"path": "/v1/responses",
                                "body": dict(_PAYLOAD["body"], input=[
                                    {"type": "message", "role": "user", "content": "different"}])}

    _turn(run_id, build_id)
    _turn(run_id, build_id, payload=same_prompt_new_messages)
    _turn(run_id, build_id, payload={"path": "/v1/responses", "body": other_body})

    rows = client.get(f"/api/admin/prompts/turns?scope=game&game_id={run_id}", headers=headers).json()

    first, second, third = (r["system_hash"] for r in rows)
    assert first == second != third
    assert rows[0]["system_chars"] == len(_PAYLOAD["body"]["instructions"])


def test_one_turn_reconstructs_system_messages_tools_and_reply(client):
    admin, headers = _user()
    run_id = _game(admin.id)
    job_id = _turn(run_id, db_store.create_build(run_id))

    turn = client.get(f"/api/admin/prompts/turns/{job_id}", headers=headers).json()

    assert turn["system"] == "You author ONE file.\nSecond line."
    assert (turn["reasoning"], turn["stage"], turn["game_id"]) == ("none", "build", run_id)
    assert [(m["role"], m["kind"]) for m in turn["messages"]] == [
        ("user", "text"), ("assistant", "tool_call"), ("tool", "tool_result"), ("assistant", "text")]
    assert turn["messages"][0]["text"] == "# TASK\nwrite game.ts"
    assert turn["messages"][1]["text"] == '{"file":"state.ts"}'
    assert turn["messages"][2]["text"] == "export interface GameState {}"
    assert turn["messages"][3]["text"] == "ok"
    assert [t["name"] for t in turn["tools"]] == ["write"]
    assert turn["response"]["text"] == "authoring now"
    assert turn["response"]["tool_calls"] == [
        {"name": "write", "arguments": '{"file":"game.ts","code":"export const x = 1"}'}]
    assert turn["response"]["usage"]["prompt_tokens"] == 900


def test_a_turn_with_no_reply_still_reads(client):
    """A failed or still-pending turn is exactly the one worth reading — its prompt must survive
    the missing result rather than 500 the viewer."""
    admin, headers = _user()
    job_id = _turn(result=None)

    turn = client.get(f"/api/admin/prompts/turns/{job_id}", headers=headers).json()

    assert turn["response"] is None
    assert turn["status"] == "pending"
    assert turn["system"].startswith("You author ONE file.")


def test_the_cap_drops_the_oldest_turns_not_the_newest(client):
    admin, _headers = _user()
    run_id = _game(admin.id)
    build_id = db_store.create_build(run_id)
    ids = [_turn(run_id, build_id) for _ in range(3)]

    assert [r["id"] for r in db_store.llm_turns_for_game(run_id, limit=2)] == ids[1:]


def test_the_whole_surface_is_admin_only(client):
    admin, admin_headers = _user()
    _plain, plain_headers = _user("bob", role="user")
    run_id = _game(admin.id)
    job_id = _turn(run_id, db_store.create_build(run_id))

    for path in ("games", "turns?scope=all", f"turns/{job_id}"):
        assert client.get(f"/api/admin/prompts/{path}", headers=plain_headers).status_code == 403
        assert client.get(f"/api/admin/prompts/{path}").status_code == 401

    assert client.get("/api/admin/prompts/turns/nope", headers=admin_headers).status_code == 404


def test_a_chat_wire_format_turn_reads_the_same(client):
    """`llm.api` can change between a build and someone reading its log, and a db that has served
    both wire formats holds both — the shape is read off the record, never asked of the connector."""
    _user_row, headers = _user()
    job_id = db_store.enqueue_job("llm", {"path": "/v1/chat/completions", "body": {
        "model": "m", "max_tokens": 900,
        "messages": [
            {"role": "system", "content": "you are X"},
            {"role": "user", "content": "make a game"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "write", "arguments": '{"file":"index.html"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
        ],
        "tools": [{"type": "function", "function": {
            "name": "write", "description": "d", "parameters": {"type": "object"}}}],
    }}, model="m")
    db_store.claim_job("llm", "w1", 60)
    db_store.complete_job(job_id, "w1", {
        "choices": [{"message": {"role": "assistant", "content": "done",
                                 "tool_calls": [{"id": "c2", "type": "function",
                                                 "function": {"name": "done",
                                                              "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3}}, None, 1.0)

    turn = client.get(f"/api/admin/prompts/turns/{job_id}", headers=headers).json()
    assert turn["system"] == "you are X"
    assert turn["max_output_tokens"] == 900
    assert [m["kind"] for m in turn["messages"]] == ["text", "tool_call", "tool_result"]
    assert turn["messages"][1]["name"] == "write"
    assert turn["messages"][2]["text"] == '{"ok": true}'
    assert turn["tools"] == [{"name": "write", "description": "d",
                              "parameters": {"type": "object"}}]
    assert turn["response"]["text"] == "done"
    assert turn["response"]["tool_calls"] == [{"name": "done", "arguments": "{}"}]
    assert turn["response"]["usage"]["completion_tokens"] == 3
