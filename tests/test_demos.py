"""The public demo surface: owner-curated run ids in settings, playable by anyone, and nothing
else reachable without a token."""

import shutil

import pytest

import api.app
from auth import playgrants
from auth import store as auth_store
from maestro.codegen.run import create_run, set_prompt


@pytest.fixture(autouse=True)
def clean_grants():
    playgrants.clear()
    yield
    playgrants.clear()


@pytest.fixture
def client(app_client, tmp_runs):
    return app_client


@pytest.fixture
def staged_game():
    created = []

    def _make(run_id):
        gd = api.app._runtime / "games" / run_id
        gd.mkdir(parents=True, exist_ok=True)
        (gd / "index.html").write_text("<head></head><body>demo</body>", encoding="utf-8")
        created.append(gd)
        return run_id

    yield _make
    for gd in created:
        shutil.rmtree(gd, ignore_errors=True)


@pytest.fixture
def demo_listed(monkeypatch):
    """Put run ids on the demo list without touching the settings file."""
    from config.settings_manager import settings_manager

    ids = []
    real = settings_manager.get_settings

    def patched():
        s = real()
        s["demo_games"] = list(ids)
        return s

    monkeypatch.setattr(settings_manager, "get_settings", patched)
    return ids


def _game(staged=None):
    u = auth_store.create_user(f"owner{auth_store.list_users().__len__()}", "pw")
    run_id = create_run(u.id)
    set_prompt(run_id, "a demo game")
    if staged:
        staged(run_id)
    return run_id


def test_demo_list_is_public_and_carries_the_prompt(client, staged_game, demo_listed):
    run_id = _game(staged_game)
    demo_listed.append(run_id)
    unstaged = _game()
    demo_listed.append(unstaged)  # listed but not built — skipped, not an error

    r = client.get("/api/demos")  # no Authorization header
    assert r.status_code == 200
    rows = r.json()
    assert [x["run_id"] for x in rows] == [run_id]
    assert rows[0]["prompt"] == "a demo game"


def test_demo_play_session_works_without_auth_for_listed_games_only(client, staged_game, demo_listed):
    run_id = _game(staged_game)
    other = _game(staged_game)  # staged but NOT listed
    demo_listed.append(run_id)

    assert client.post(f"/api/demos/{other}/play-session").status_code == 404
    r = client.post(f"/api/demos/{run_id}/play-session")
    assert r.status_code == 200
    # The minted session runs the same handoff → grant → files flow as an owner's.
    r2 = client.get(r.json()["url"], follow_redirects=False)
    assert r2.status_code == 302
    grant = r2.headers["set-cookie"].split("maestro_play=", 1)[1].split(";", 1)[0]
    assert client.get(f"/play/games/{run_id}/index.html",
                      cookies={"maestro_play": grant}).status_code == 200


def test_demo_surface_grants_nothing_else(client, staged_game, demo_listed):
    """The public prefix must not open the rest of /api, and a demo grant opens one game only."""
    run_id = _game(staged_game)
    other = _game(staged_game)
    demo_listed.extend([run_id, other])

    assert client.get("/api/games").status_code == 401
    r = client.post(f"/api/demos/{run_id}/play-session")
    r2 = client.get(r.json()["url"], follow_redirects=False)
    grant = r2.headers["set-cookie"].split("maestro_play=", 1)[1].split(";", 1)[0]
    assert client.get(f"/play/games/{other}/index.html",
                      cookies={"maestro_play": grant}).status_code == 403


def test_empty_demo_list_means_no_surface(client):
    assert client.get("/api/demos").json() == []
    assert client.post("/api/demos/anything/play-session").status_code == 404


def test_grant_tables_cap_instead_of_growing_forever():
    for i in range(playgrants.MAX_LIVE):
        assert playgrants.issue_handoff("u", f"r{i}") is not None
    assert playgrants.issue_handoff("u", "one-more") is None
