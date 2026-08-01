"""The /play game harness is cookie-gated (not header-only like /api): login mints a /play-scoped
`maestro_play` cookie the static harness sends automatically, per-game bundles are ownership-checked,
and shared harness files need only a valid session."""

import shutil

import pytest

import api.app
from auth import store as auth_store
from maestro.codegen.run import create_run


@pytest.fixture
def client(app_client, tmp_runs):
    return app_client


def _user(handle="alice"):
    u = auth_store.create_user(handle, "pw")
    return u, auth_store.issue_token(u.id)


def _make_game(user_id):
    run_id = create_run(user_id)
    return run_id


@pytest.fixture
def game_bundle():
    """Drop a fake per-game bundle into the real /play mount root, then clean it up. The auth gate
    runs before StaticFiles, but a 200 for the owner needs the file to actually exist to serve."""
    created = []

    def _make(run_id):
        gd = api.app._runtime / "games" / run_id
        gd.mkdir(parents=True, exist_ok=True)
        (gd / "main.js").write_text("// fake bundle", encoding="utf-8")
        created.append(gd)
        return f"/play/games/{run_id}/main.js"

    yield _make
    for gd in created:
        shutil.rmtree(gd, ignore_errors=True)


def test_login_sets_play_cookie_and_logout_clears_it(client):
    auth_store.create_user("alice", "pw")

    r = client.post("/auth/login", json={"handle": "alice", "password": "pw"})
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "").lower()
    assert "maestro_play=" in set_cookie
    assert "path=/play" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    # http (test scheme) → not Secure, so the cookie works on localhost dev.
    assert "secure" not in set_cookie
    # Outlives the browser: the bearer token is in localStorage, so a session cookie here would
    # leave the app signed in with every /play request 401ing after a restart.
    assert f"max-age={auth_store.SESSION_TTL_SECONDS}" in set_cookie

    token = r.json()["token"]
    r2 = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    cleared = r2.headers.get("set-cookie", "").lower()
    assert "maestro_play=" in cleared and ("max-age=0" in cleared or "expires=" in cleared)


def test_play_game_file_requires_owning_session(client, game_bundle):
    owner, owner_tok = _user("alice")
    _, other_tok = _user("bob")
    run_id = _make_game(owner.id)
    url = game_bundle(run_id)

    assert client.get(url).status_code == 401
    r = client.get(url, cookies={"maestro_play": other_tok})
    assert r.status_code == 403
    r = client.get(url, cookies={"maestro_play": owner_tok})
    assert r.status_code == 200
    assert r.text == "// fake bundle"


def test_play_responses_carry_the_containment_csp(client):
    """/play runs model-authored JS: every response (including a 401) pins scripted loads + network
    to this origin. /api stays CSP-free — the policy is containment for the game surface only."""
    _, tok = _user("alice")

    csp = client.get("/play/games/nosuchgame/index.html",
                     cookies={"maestro_play": tok}).headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    # blob: (GLB texture object URLs) + data: (the webp support-detection probe image) are required
    # by GLTFLoader — images + fetch only.
    assert "connect-src 'self' blob:" in csp
    assert "img-src 'self' blob: data:" in csp
    assert "object-src 'none'" in csp
    # blob: must never reach script-src — a blob: script would let generated code sidestep 'self'.
    assert "script-src 'self' 'unsafe-inline';" in csp
    # form-action does not fall back to default-src: unset, a form POST leaves the origin.
    assert "form-action 'none'" in csp

    assert "content-security-policy" in client.get("/play/games/g/index.html").headers  # 401 too
    assert "content-security-policy" not in client.get("/api/games").headers
