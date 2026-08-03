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
    """Put run ids on the demo list without touching the settings file. Append to a tier's list
    to list a game under it."""
    from config.settings_manager import settings_manager

    tiers = {"showcase": [], "oneshot": []}
    real = settings_manager.get_settings

    def patched():
        s = real()
        s["demo_games"] = {k: list(v) for k, v in tiers.items()}
        return s

    monkeypatch.setattr(settings_manager, "get_settings", patched)
    return tiers


def _game(staged=None):
    u = auth_store.create_user(f"owner{auth_store.list_users().__len__()}", "pw")
    run_id = create_run(u.id)
    set_prompt(run_id, "a demo game")
    if staged:
        staged(run_id)
    return run_id


def test_demo_list_is_public_and_carries_the_prompt(client, staged_game, demo_listed):
    run_id = _game(staged_game)
    demo_listed["showcase"].append({"id": run_id})
    unstaged = _game()
    demo_listed["showcase"].append({"id": unstaged})  # listed but not built — skipped, not an error

    r = client.get("/api/demos")  # no Authorization header
    assert r.status_code == 200
    rows = r.json()
    assert [x["run_id"] for x in rows] == [run_id]
    assert rows[0]["prompt"] == "a demo game"


def test_a_staged_demo_shows_the_persons_words_not_stage_one(client, staged_game, demo_listed):
    from maestro.codegen import stages as stages_mod
    from maestro.state import RunState

    run_id = _game(staged_game)
    stages_mod.save(RunState(run_id).run_dir, "make me a fox game",
                    ["Build a complete browser game where…", "Add a hunger system…"])
    demo_listed["oneshot"].append({"id": run_id})

    assert client.get("/api/demos").json()[0]["prompt"] == "make me a fox game"


def test_each_demo_carries_its_tier_showcase_first(client, staged_game, demo_listed):
    one = _game(staged_game)
    show = _game(staged_game)
    demo_listed["oneshot"].append({"id": one})
    demo_listed["showcase"].append({"id": show})

    rows = client.get("/api/demos").json()
    assert [(x["run_id"], x["tier"]) for x in rows] == [(show, "showcase"), (one, "oneshot")]


def test_a_thumb_is_served_for_listed_games_and_stays_inside_the_game(client, staged_game, demo_listed):
    run_id = _game(staged_game)
    gd = api.app._runtime / "games" / run_id
    (gd / "assets").mkdir()
    (gd / "assets" / "fox.webp").write_bytes(b"not-really-webp")
    demo_listed["oneshot"].append({"id": run_id, "thumb": "assets/fox.webp"})
    bare = _game(staged_game)
    demo_listed["oneshot"].append({"id": bare})

    rows = client.get("/api/demos").json()
    assert [x["thumb_url"] for x in rows] == [f"/api/demos/{run_id}/thumb", None]
    assert client.get(f"/api/demos/{run_id}/thumb").content == b"not-really-webp"
    assert client.get(f"/api/demos/{bare}/thumb").status_code == 404
    assert client.get("/api/demos/unlisted/thumb").status_code == 404


def test_a_thumb_path_cannot_escape_the_game_folder(client, staged_game, demo_listed):
    run_id = _game(staged_game)
    demo_listed["oneshot"].append({"id": run_id, "thumb": "../../../../etc/passwd"})
    assert client.get(f"/api/demos/{run_id}/thumb").status_code == 404


def test_demo_play_session_works_without_auth_for_listed_games_only(client, staged_game, demo_listed):
    run_id = _game(staged_game)
    other = _game(staged_game)  # staged but NOT listed
    demo_listed["oneshot"].append({"id": run_id})

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
    demo_listed["showcase"].extend([{"id": run_id}, {"id": other}])

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


def test_a_built_game_is_named_by_its_own_title_tag(client, staged_game, demo_listed):
    run_id = _game(staged_game)
    gd = api.app._runtime / "games" / run_id
    (gd / "index.html").write_text(
        "<head><title>  The Cursed \n Curio  </title></head><body>x</body>", encoding="utf-8")
    demo_listed["showcase"].append({"id": run_id})
    assert client.get("/api/demos").json()[0]["title"] == "The Cursed Curio"
