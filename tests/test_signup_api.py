"""The signup endpoint (public, open, per-IP throttled), as it runs on the app."""

import pytest

from auth import store
from auth.ratelimit import signup_throttle
from billing import ledger


@pytest.fixture(autouse=True)
def _clear_throttle():
    # TestClient requests all arrive from the same client address.
    signup_throttle.clear("testclient")


def _signup(client, handle="alice", password="pw-pass1234", email=None):
    return client.post("/auth/signup",
                       json={"handle": handle, "password": password,
                             "email": email or f"{handle.strip() or 'blank'}@example.com"})


def test_signup_is_public_and_returns_a_working_token(app_client):
    r = _signup(app_client)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["handle"] == "alice"
    assert body["user"]["role"] == "user"
    assert store.resolve_token(body["token"]).handle == "alice"
    assert ledger.balance(body["user"]["id"]) == 0


def test_taken_handle_is_409(app_client):
    store.create_user("alice", "pw-pass1234", email="alice@example.com")
    assert _signup(app_client, email="alice2@example.com").status_code == 409
    assert _signup(app_client, handle="bob").status_code == 200


def test_blank_handle_or_password_is_400(app_client):
    assert _signup(app_client, handle="   ").status_code == 400
    assert _signup(app_client, password="").status_code == 400
    assert _signup(app_client, password="short").status_code == 400
    assert _signup(app_client, email="not-an-address").status_code == 400


def test_a_taken_email_is_409_whatever_its_case(app_client):
    store.create_user("alice", "pw-pass1234", email="Someone@Example.com")
    assert _signup(app_client, handle="bob", email="someone@example.com").status_code == 409


def test_a_rejected_form_does_not_spend_the_throttle(app_client):
    """A typo'd address is the person's own mistake — it must not cost them their signup
    attempts."""
    for _ in range(6):
        assert _signup(app_client, email="nope").status_code == 400
    assert _signup(app_client).status_code == 200


def test_signup_throttles_by_ip_after_repeated_failures(app_client):
    store.create_user("alice", "pw-pass1234", email="alice@example.com")
    for _ in range(5):
        assert _signup(app_client, email="alice2@example.com").status_code == 409
    r = _signup(app_client, handle="bob")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_a_successful_signup_clears_the_throttle(app_client):
    store.create_user("alice", "pw-pass1234", email="alice@example.com")
    for _ in range(4):
        _signup(app_client, email="alice2@example.com")
    assert _signup(app_client, handle="bob").status_code == 200
    for _ in range(5):
        assert _signup(app_client, handle="bob").status_code == 409
    assert _signup(app_client, handle="carol").status_code == 429
