"""/play is grant-gated: POST /api/games/<id>/play-session (bearer, ownership-checked) mints a
single-use handoff token; GET /handoff redeems it into a per-game, path-scoped grant cookie; only
that cookie opens the game's files. The app's session token never reaches the game surface."""

import shutil

import pytest

import api.app
from auth import playgrants
from auth import store as auth_store
from maestro.codegen.run import create_run, set_prompt


def _game(user_id):
    run_id = create_run(user_id)
    set_prompt(run_id, "a small game")  # _require_state 404s a run with no spec on disk
    return run_id


@pytest.fixture(autouse=True)
def clean_grants():
    playgrants.clear()
    yield
    playgrants.clear()


@pytest.fixture
def client(app_client, tmp_runs):
    return app_client


def _user(handle="alice"):
    u = auth_store.create_user(handle, "pw-pass1234", email=f"{handle}@example.com")
    return u, auth_store.issue_token(u.id)


@pytest.fixture
def game_bundle():
    """Drop a fake staged game into the real /play mount root, then clean it up. index.html makes
    the game count as BUILT (is_staged), which play-session requires."""
    created = []

    def _make(run_id):
        gd = api.app._runtime / "games" / run_id
        gd.mkdir(parents=True, exist_ok=True)
        (gd / "index.html").write_text("<head></head><body>hi</body>", encoding="utf-8")
        (gd / "main.js").write_text("// fake bundle", encoding="utf-8")
        created.append(gd)
        return run_id

    yield _make
    for gd in created:
        shutil.rmtree(gd, ignore_errors=True)


def _play_session(client, token, run_id):
    return client.post(f"/api/games/{run_id}/play-session",
                       headers={"Authorization": f"Bearer {token}"})


def _grant_cookie(client, token, run_id):
    """Run the whole handoff: mint a session, redeem it, hand back the grant cookie value."""
    r = _play_session(client, token, run_id)
    assert r.status_code == 200
    r2 = client.get(r.json()["url"], follow_redirects=False)
    assert r2.status_code == 302
    set_cookie = r2.headers["set-cookie"]
    return set_cookie.split("maestro_play=", 1)[1].split(";", 1)[0], set_cookie


def test_login_sets_no_cookie(client):
    """The session token is header-only everywhere; the play surface earns its own grant via
    /handoff. Login has no cookie to set."""
    auth_store.create_user("alice", "pw-pass1234", email="alice@example.com")
    r = client.post("/auth/login", json={"handle": "alice", "password": "pw-pass1234"})
    assert r.status_code == 200
    assert "set-cookie" not in r.headers


def test_play_session_is_owner_only(client, game_bundle):
    owner, owner_tok = _user("alice")
    _, other_tok = _user("bob")
    run_id = game_bundle(_game(owner.id))

    assert _play_session(client, other_tok, run_id).status_code == 403
    assert client.post(f"/api/games/{run_id}/play-session").status_code == 401
    r = _play_session(client, owner_tok, run_id)
    assert r.status_code == 200
    assert r.json()["url"].startswith("/handoff?t=")


def test_play_session_requires_a_built_game(client):
    owner, tok = _user("alice")
    run_id = _game(owner.id)  # never staged
    assert _play_session(client, tok, run_id).status_code == 409


def test_handoff_grants_access_to_that_game_only(client, game_bundle):
    owner, tok = _user("alice")
    run_id = game_bundle(_game(owner.id))
    other_run = game_bundle(_game(owner.id))

    grant, set_cookie = _grant_cookie(client, tok, run_id)
    low = set_cookie.lower()
    # HttpOnly (game JS can't read it), host-only (no Domain= — the game origin must not be able
    # to toss cookies onto the app domain), path-scoped to this one game, cross-site-iframe-able.
    assert "httponly" in low
    assert "domain=" not in low
    assert f"path=/play/games/{run_id}/" in low
    assert "samesite=none" in low and "secure" in low and "partitioned" in low

    assert client.get(f"/play/games/{run_id}/main.js").status_code == 401
    ok = client.get(f"/play/games/{run_id}/main.js", cookies={"maestro_play": grant})
    assert ok.status_code == 200 and ok.text == "// fake bundle"
    # The browser's Path scoping would never send it; the server refuses a hand-crafted try too.
    assert client.get(f"/play/games/{other_run}/main.js",
                      cookies={"maestro_play": grant}).status_code == 403
    assert client.get(f"/play/demos/{run_id}/main.js",
                      cookies={"maestro_play": grant}).status_code == 403
    assert client.get(f"/play/other/{run_id}/main.js",
                      cookies={"maestro_play": grant}).status_code == 404


def test_handoff_token_is_single_use_and_validated(client, game_bundle):
    owner, tok = _user("alice")
    run_id = game_bundle(_game(owner.id))

    r = _play_session(client, tok, run_id)
    url = r.json()["url"]
    assert client.get(url, follow_redirects=False).status_code == 302
    assert client.get(url, follow_redirects=False).status_code == 403  # replay
    assert client.get("/handoff?t=nonsense").status_code == 403
    assert client.get("/handoff").status_code == 403


def test_grant_cookie_is_storable_over_plain_http_on_localhost(client, game_bundle):
    """A browser stores no `Secure` cookie over http://localhost, so hardcoding the cross-site
    attributes made every local play 401 at the grant check — the handoff succeeded and the cookie
    never survived the redirect. Localhost serves the game same-site, where Lax is enough."""
    owner, tok = _user("alice")
    run_id = game_bundle(_game(owner.id))

    r = _play_session(client, tok, run_id)
    assert r.status_code == 200
    r2 = client.get(r.json()["url"], follow_redirects=False, headers={"host": "localhost:8000"})
    assert r2.status_code == 302
    low = r2.headers["set-cookie"].lower()
    assert "secure" not in low and "partitioned" not in low
    assert "samesite=lax" in low
    assert "httponly" in low and f"path=/play/games/{run_id}/" in low

    grant = r2.headers["set-cookie"].split("maestro_play=", 1)[1].split(";", 1)[0]
    assert client.get(f"/play/games/{run_id}/main.js",
                      cookies={"maestro_play": grant}).status_code == 200


def test_served_index_html_carries_the_console_reporter(client, game_bundle):
    owner, tok = _user("alice")
    run_id = game_bundle(_game(owner.id))
    grant, _ = _grant_cookie(client, tok, run_id)

    r = client.get(f"/play/games/{run_id}/index.html", cookies={"maestro_play": grant})
    assert r.status_code == 200
    # Injected on the way out; the staged file itself stays pristine.
    assert "maestro-report" in r.text
    assert r.text.index("maestro-report") < r.text.index("<body>")
    assert "maestro-report" not in (api.app._runtime / "games" / run_id / "index.html").read_text()


def test_play_responses_carry_the_containment_csp(client, game_bundle):
    """/play runs model-authored JS: every response (including a 401) pins scripted loads + network
    to this origin. /api stays CSP-free — the policy is containment for the game surface only."""
    owner, tok = _user("alice")
    run_id = game_bundle(_game(owner.id))
    grant, _ = _grant_cookie(client, tok, run_id)

    csp = client.get(f"/play/games/{run_id}/index.html",
                     cookies={"maestro_play": grant}).headers.get("content-security-policy", "")
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
    # The SPA's game page frames the game; no other site may.
    assert "frame-ancestors 'self'" in csp

    assert "content-security-policy" in client.get("/play/games/g/index.html").headers  # 401 too
    assert "content-security-policy" not in client.get("/api/games").headers


@pytest.fixture
def split_origins(monkeypatch):
    """Games on their own domain: play.origin/app_origin set, everything else untouched."""
    from config.settings_manager import settings_manager

    real = settings_manager.get_settings

    def patched():
        s = real()
        s["play"] = {"origin": "https://games.example", "app_origin": "https://app.example"}
        return s

    monkeypatch.setattr(settings_manager, "get_settings", patched)


def test_split_hosts_serve_only_their_own_surface(client, game_bundle, split_origins):
    """One process, two hostnames — isolation only holds if the game host serves NO api (a game's
    fetch('/api/…') resolves there) and the app host serves NO game."""
    owner, tok = _user("alice")
    run_id = game_bundle(_game(owner.id))
    game_host = {"host": "games.example"}

    r = _play_session(client, tok, run_id)  # app host (testserver): the API lives here
    assert r.status_code == 200
    assert r.json()["url"].startswith("https://games.example/handoff?t=")
    assert r.json()["origin"] == "https://games.example"

    # The app host serves no game surface…
    assert client.get(f"/play/games/{run_id}/index.html").status_code == 404
    assert client.get("/handoff?t=x").status_code == 404
    # …and the game host serves nothing else.
    assert client.post("/auth/login", headers=game_host,
                       json={"handle": "alice", "password": "pw-pass1234"}).status_code == 404
    assert client.get("/api/games", headers=game_host).status_code == 404

    # The handoff lives on the game host and still works end to end there.
    r2 = client.get(r.json()["url"], headers=game_host, follow_redirects=False)
    assert r2.status_code == 302
    grant = r2.headers["set-cookie"].split("maestro_play=", 1)[1].split(";", 1)[0]
    ok = client.get(f"/play/games/{run_id}/index.html", headers=game_host,
                    cookies={"maestro_play": grant})
    assert ok.status_code == 200
    # Framed by the app origin alone — 'self' would be the wrong origin on a split domain.
    assert "frame-ancestors https://app.example" in ok.headers["content-security-policy"]
