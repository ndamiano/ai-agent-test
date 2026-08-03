"""The signup endpoint (public, invite-gated, per-IP throttled) and the admin invite surface
(role-gated mint/list/disable), as they run on the app."""

import pytest

from auth import store
from auth.ratelimit import signup_throttle


@pytest.fixture(autouse=True)
def _clear_throttle():
    # TestClient requests all arrive from the same client address.
    signup_throttle.clear("testclient")


def _signup(client, handle="alice", password="pw", code="gs-aaaa-aaaa"):
    return client.post("/auth/signup",
                       json={"handle": handle, "password": password, "invite_code": code})


def test_signup_is_public_and_returns_a_working_token(app_client):
    code = store.create_invite("root")
    r = _signup(app_client, code=code)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["handle"] == "alice"
    assert body["user"]["role"] == "user"
    assert store.resolve_token(body["token"]).handle == "alice"
    assert store.balance(body["user"]["id"]) == 0


def test_invalid_code_is_403(app_client):
    assert _signup(app_client).status_code == 403


def test_spent_code_is_403(app_client):
    code = store.create_invite("root", max_uses=1)
    assert _signup(app_client, handle="alice", code=code).status_code == 200
    assert _signup(app_client, handle="bob", code=code).status_code == 403


def test_taken_handle_is_409_and_keeps_the_code(app_client):
    store.create_user("alice", "pw")
    code = store.create_invite("root")
    assert _signup(app_client, code=code).status_code == 409
    assert _signup(app_client, handle="bob", code=code).status_code == 200


def test_blank_handle_or_password_is_400(app_client):
    code = store.create_invite("root")
    assert _signup(app_client, handle="   ", code=code).status_code == 400
    assert _signup(app_client, password="", code=code).status_code == 400


def test_signup_throttles_by_ip_after_repeated_failures(app_client):
    for _ in range(5):
        assert _signup(app_client).status_code == 403
    # Blocked now — even a valid code is refused while the window stands.
    code = store.create_invite("root")
    r = _signup(app_client, code=code)
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_a_successful_signup_clears_the_throttle(app_client):
    for _ in range(4):
        _signup(app_client)
    code = store.create_invite("root")
    assert _signup(app_client, code=code).status_code == 200
    assert _signup(app_client, handle="bob").status_code == 403


# ── Admin invite surface ─────────────────────────────────────────────────────────────────────


def _token(handle, role):
    store.create_user(handle, "pw", role=role)
    return store.issue_token(store.get_user_by_handle(handle).id)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_invites_are_admin_only(app_client):
    assert app_client.get("/api/admin/invites").status_code == 401
    token = _token("alice", "user")
    assert app_client.get("/api/admin/invites", headers=_auth(token)).status_code == 403
    assert app_client.post("/api/admin/invites", json={"count": 1},
                           headers=_auth(token)).status_code == 403


def test_admin_mints_a_batch_and_lists_usage(app_client):
    token = _token("root", "admin")
    r = app_client.post("/api/admin/invites", json={"count": 3, "max_uses": 2},
                        headers=_auth(token))
    assert r.status_code == 200
    codes = r.json()["codes"]
    assert len(codes) == 3

    store.signup("alice", "pw", codes[0])
    invites = app_client.get("/api/admin/invites", headers=_auth(token)).json()["invites"]
    by_code = {i["code"]: i for i in invites}
    assert by_code[codes[0]]["uses"] == 1
    assert by_code[codes[0]]["max_uses"] == 2
    assert by_code[codes[0]]["created_by"] == "root"
    assert by_code[codes[1]]["uses"] == 0


def test_admin_disables_a_code(app_client):
    token = _token("root", "admin")
    code = app_client.post("/api/admin/invites", json={},
                           headers=_auth(token)).json()["codes"][0]
    assert app_client.post(f"/api/admin/invites/{code}/disable",
                           headers=_auth(token)).status_code == 200
    assert _signup(app_client, code=code).status_code == 403
    assert app_client.post("/api/admin/invites/gs-none-none/disable",
                           headers=_auth(token)).status_code == 404


def test_batch_bounds_are_enforced(app_client):
    token = _token("root", "admin")
    assert app_client.post("/api/admin/invites", json={"count": 0},
                           headers=_auth(token)).status_code == 400
    assert app_client.post("/api/admin/invites", json={"count": 101},
                           headers=_auth(token)).status_code == 400
    assert app_client.post("/api/admin/invites", json={"max_uses": 0},
                           headers=_auth(token)).status_code == 400
