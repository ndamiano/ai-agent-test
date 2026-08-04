"""Account recovery: the mailed reset link, and what changing a password does to sessions.

A reset token is a password-equivalent credential that travels through someone's inbox, so the
properties under test are the ones that keep it from being reusable, guessable-at-leisure, or an
oracle for who has an account.
"""

import asyncio

import pytest
from fastapi import HTTPException

from auth import router as auth_router
from auth import store
from auth.ratelimit import login_throttle, reset_throttle
from tools import mailer

PW = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clear_throttles():
    reset_throttle._failures.clear()
    login_throttle._failures.clear()


@pytest.fixture
def sent(monkeypatch):
    """A configured mailer whose sends land in a list instead of an SMTP server."""
    out = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send",
                        lambda to, subject, body: out.append((to, subject, body)))
    return out


def _user(handle="alice", email="alice@example.com"):
    return store.create_user(handle, PW, email=email)


def _forgot(email):
    return asyncio.run(auth_router.forgot_password(auth_router.ForgotRequest(email=email)))


def _reset(token, new_password):
    return asyncio.run(auth_router.reset_password(
        auth_router.ResetRequest(token=token, new_password=new_password)))


def _token_from(body: str) -> str:
    return body.split("?t=")[1].split()[0]


# ── The mail ─────────────────────────────────────────────────────────────────────────────────


def test_a_known_address_gets_a_link(sent):
    _user()
    _forgot("alice@example.com")
    to, subject, body = sent[0]
    assert to == "alice@example.com"
    assert "?t=" in body and "reset" in subject.lower()


def test_the_address_is_matched_case_insensitively(sent):
    _user(email="Alice@Example.com")
    _forgot("alice@example.com")
    assert len(sent) == 1


def test_an_unknown_address_answers_the_same_and_mails_nothing(sent):
    _user()
    assert _forgot("nobody@example.com") == _forgot("alice@example.com")
    assert [to for to, _, _ in sent] == ["alice@example.com"]


def test_asking_twice_kills_the_first_link(sent):
    """Two links live in one inbox means the older one is a spare key nobody revoked."""
    _user()
    _forgot("alice@example.com")
    _forgot("alice@example.com")
    first, second = (_token_from(b) for _, _, b in sent)
    assert store.consume_reset_token(first) is None
    assert store.consume_reset_token(second) is not None


def test_the_form_cannot_be_used_to_mail_bomb(sent):
    _user()
    for _ in range(10):
        _forgot("alice@example.com")
    assert len(sent) <= 3


def test_no_smtp_configured_still_answers_ok(monkeypatch):
    _user()
    monkeypatch.setattr(mailer, "configured", lambda: False)
    assert _forgot("alice@example.com") == {"ok": True}


# ── Spending the token ───────────────────────────────────────────────────────────────────────


def test_a_reset_sets_the_password_and_signs_in(sent):
    user = _user()
    _forgot("alice@example.com")
    out = _reset(_token_from(sent[0][2]), "brand-new-password")
    assert out["user"]["id"] == user.id
    assert store.resolve_token(out["token"]).id == user.id
    assert store.authenticate("alice", "brand-new-password") is not None
    assert store.authenticate("alice", PW) is None


def test_a_token_works_exactly_once(sent):
    _user()
    _forgot("alice@example.com")
    token = _token_from(sent[0][2])
    _reset(token, "brand-new-password")
    with pytest.raises(HTTPException) as exc:
        _reset(token, "another-new-password")
    assert exc.value.status_code == 400
    assert store.authenticate("alice", "another-new-password") is None


def test_an_expired_token_is_refused(sent, monkeypatch):
    _user()
    _forgot("alice@example.com")
    token = _token_from(sent[0][2])
    real = store.time.time
    monkeypatch.setattr(store.time, "time", lambda: real() + store.RESET_TTL_SECONDS + 1)
    with pytest.raises(HTTPException) as exc:
        _reset(token, "brand-new-password")
    assert exc.value.status_code == 400


def test_a_made_up_token_is_refused():
    _user()
    with pytest.raises(HTTPException) as exc:
        _reset("not-a-real-token", "brand-new-password")
    assert exc.value.status_code == 400


def test_a_reset_must_still_meet_the_length_rule(sent):
    _user()
    _forgot("alice@example.com")
    token = _token_from(sent[0][2])
    with pytest.raises(HTTPException) as exc:
        _reset(token, "short")
    assert exc.value.status_code == 400
    # The token survives a rejected password — the person gets to try again.
    assert store.consume_reset_token(token) is not None


def test_a_reset_ends_every_other_session(sent):
    user = _user()
    stale = store.issue_token(user.id)
    _forgot("alice@example.com")
    _reset(_token_from(sent[0][2]), "brand-new-password")
    assert store.resolve_token(stale) is None


# ── The password rule, and what a change does to sessions ────────────────────────────────────


@pytest.mark.parametrize("password", ["", "short", "123456789"])
def test_passwords_under_ten_characters_are_refused(password):
    with pytest.raises(ValueError):
        store.create_user("alice", password, email="alice@example.com")


def test_ten_characters_is_enough_whatever_it_contains():
    """Length is the rule. No symbol/case/digit requirement — those shrink the search space an
    attacker has to cover."""
    store.create_user("alice", "aaaaaaaaaa", email="alice@example.com")
    assert store.authenticate("alice", "aaaaaaaaaa") is not None


def test_changing_a_password_signs_every_session_out_but_the_caller():
    user = _user()
    other_device = store.issue_token(user.id)
    out = asyncio.run(auth_router.change_password(
        auth_router.PasswordChangeRequest(current_password=PW,
                                          new_password="brand-new-password"), user))
    assert store.resolve_token(other_device) is None
    assert store.resolve_token(out["token"]).id == user.id


# ── Changing the recovery address ────────────────────────────────────────────────────────────


def _change_email(user, password, email):
    return asyncio.run(auth_router.change_email(
        auth_router.EmailChangeRequest(password=password, email=email), user))


def test_changing_the_email_needs_the_password():
    user = _user()
    with pytest.raises(HTTPException) as exc:
        _change_email(user, "wrong-password-here", "new@example.com")
    assert exc.value.status_code == 403
    assert store.get_user_by_handle("alice").email == "alice@example.com"


def test_a_changed_email_is_where_the_next_link_goes(sent):
    user = _user()
    _change_email(user, PW, "moved@example.com")
    _forgot("moved@example.com")
    assert sent[0][0] == "moved@example.com"


def test_an_email_already_in_use_is_refused():
    _user()
    other = store.create_user("bob", PW, email="bob@example.com")
    with pytest.raises(HTTPException) as exc:
        _change_email(other, PW, "alice@example.com")
    assert exc.value.status_code == 409
