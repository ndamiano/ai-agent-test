"""Grading (docs/game_rubric.md): the owner plays a built game and writes down what he thought.

Two things are load-bearing and tested here — the page is kept BLIND to anything that identifies
the arm, and re-grading a run never destroys the earlier grade.
"""

import pytest

import grading
from auth import store as auth_store
from maestro.codegen.run import create_run, set_prompt


@pytest.fixture
def client(app_client, tmp_runs):
    return app_client


@pytest.fixture(autouse=True)
def grades_dir(tmp_path, monkeypatch):
    d = tmp_path / "grades"
    d.mkdir()
    monkeypatch.setattr(grading, "_dir", lambda: d)
    return d


def _user(handle="alice", role="admin"):
    u = auth_store.create_user(handle, "pw-pass1234", role=role, email=f"{handle}@example.com")
    return u, auth_store.issue_token(u.id)


def _game(user_id, request="a small game about a crab"):
    run_id = create_run(user_id)
    set_prompt(run_id, request)
    return run_id


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _filled():
    return {
        "loads": True, "takes_input": True, "crashed": False, "crash_note": "",
        "first_impression": 5, "first_impression_note": "pretty flat",
        "show_someone": "with caveats", "what_is_it": "a crab game", "biggest_gap": "no depth",
        "dimensions": {"core_loop": {"score": 6, "note": "loops"},
                       "sound": {"score": None, "note": "silent, and never tried for it"}},
        "considered": 6, "considered_note": "the loop is better than it felt",
        "what_moved_it": "noticed the escalation",
        "claims": [{"claim": "shops", "verdict": "absent", "note": "never found one"}],
        "unrequested": "a title screen", "play_ended": "time-box", "play_minutes": 10,
    }


def test_grade_target_hides_everything_that_identifies_the_arm(client):
    """The page is handed the request (it needs it for the reveal step) and nothing else. A grade
    formed while knowing which arm built the game is not evidence about the arm."""
    owner, tok = _user()
    run_id = _game(owner.id)

    r = client.get(f"/api/admin/grades/{run_id}", headers=_auth(tok))
    assert r.status_code == 200
    assert set(r.json()) == {"run_id", "request", "previous"}
    assert r.json()["request"] == "a small game about a crab"
    assert r.json()["previous"] == 0


def test_grading_is_admin_only_and_owner_only(client):
    owner, owner_tok = _user("alice")
    _, plain_tok = _user("bob", role="user")
    run_id = _game(owner.id)

    assert client.get(f"/api/admin/grades/{run_id}").status_code == 401
    assert client.get(f"/api/admin/grades/{run_id}", headers=_auth(plain_tok)).status_code == 403

    # An admin who does not own the run gets nothing — grading is not a way around ownership.
    other, other_tok = _user("carol")
    assert client.get(f"/api/admin/grades/{run_id}", headers=_auth(other_tok)).status_code == 404
    assert client.get(f"/api/admin/grades/{_game(other.id)}",
                      headers=_auth(owner_tok)).status_code == 404


def test_a_grade_round_trips(client, grades_dir):
    owner, tok = _user()
    run_id = _game(owner.id)

    r = client.post(f"/api/admin/grades/{run_id}", json=_filled(), headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["saved"].startswith(run_id)
    assert len(list(grades_dir.glob("*.json"))) == 1

    got = client.get(f"/api/admin/grades/{run_id}/history", headers=_auth(tok)).json()["grades"]
    assert len(got) == 1
    assert got[0]["run_id"] == run_id and got[0]["graded_at"]
    assert got[0]["considered"] == 6
    # `n/a` survives as null rather than collapsing to a zero — it is not a score of any kind.
    assert got[0]["dimensions"]["sound"]["score"] is None
    assert got[0]["claims"][0]["verdict"] == "absent"


def test_regrading_keeps_the_earlier_grade(client, monkeypatch):
    """Re-grading after a fix build is the point of the instrument; an overwrite could never show
    that a change moved a game."""
    owner, tok = _user()
    run_id = _game(owner.id)

    stamps = iter(["20260808T100000Z", "20260809T100000Z"])
    monkeypatch.setattr(grading.time, "strftime", lambda *a, **k: next(stamps))

    first = dict(_filled(), considered=4)
    client.post(f"/api/admin/grades/{run_id}", json=first, headers=_auth(tok))
    client.post(f"/api/admin/grades/{run_id}", json=dict(_filled(), considered=8), headers=_auth(tok))

    got = client.get(f"/api/admin/grades/{run_id}/history", headers=_auth(tok)).json()["grades"]
    assert [g["considered"] for g in got] == [4, 8]

    assert client.get(f"/api/admin/grades/{run_id}", headers=_auth(tok)).json()["previous"] == 2


def test_a_corrupt_grade_file_does_not_hide_the_rest(client, grades_dir):
    owner, tok = _user()
    run_id = _game(owner.id)
    client.post(f"/api/admin/grades/{run_id}", json=_filled(), headers=_auth(tok))
    (grades_dir / f"{run_id}__20260101T000000Z.json").write_text("{not json", encoding="utf-8")

    got = client.get(f"/api/admin/grades/{run_id}/history", headers=_auth(tok)).json()["grades"]
    assert len(got) == 1
