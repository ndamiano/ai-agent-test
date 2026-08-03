"""Invite-code lifecycle in the auth store: minting, redemption to exhaustion, disabling, and
the atomicity contracts — no code burned on a failed signup, no signup on a spent code, and a
race on the last use admits exactly one."""

import re
import threading

import pytest

from auth import store


def _invite(code):
    return next(i for i in store.list_invites() if i["code"] == code)


def test_create_invite_mints_a_readable_unguessable_code():
    code = store.create_invite("root")
    assert re.fullmatch(r"gs-[a-z2-9]{4}-[a-z2-9]{4}", code)
    row = _invite(code)
    assert row["created_by"] == "root"
    assert (row["max_uses"], row["uses"], row["disabled"]) == (1, 0, 0)


def test_create_invite_rejects_a_useless_max():
    with pytest.raises(ValueError):
        store.create_invite("root", max_uses=0)


def test_signup_creates_a_user_at_zero_credits_and_counts_the_use():
    code = store.create_invite("root", max_uses=2)
    user = store.signup("alice", "pw", code)
    assert user.role == "user"
    assert store.balance(user.id) == 0
    assert store.authenticate("alice", "pw") is not None
    assert _invite(code)["uses"] == 1


def test_signup_normalizes_the_code():
    code = store.create_invite("root")
    store.signup("alice", "pw", f"  {code.upper()}  ")
    assert _invite(code)["uses"] == 1


def test_code_is_spent_at_max_uses():
    code = store.create_invite("root", max_uses=2)
    store.signup("alice", "pw", code)
    store.signup("bob", "pw", code)
    with pytest.raises(store.InviteCodeError):
        store.signup("carol", "pw", code)
    assert _invite(code)["uses"] == 2


def test_unknown_and_disabled_codes_are_rejected():
    with pytest.raises(store.InviteCodeError):
        store.signup("alice", "pw", "gs-aaaa-aaaa")
    code = store.create_invite("root")
    store.disable_invite(code)
    with pytest.raises(store.InviteCodeError):
        store.signup("alice", "pw", code)
    assert _invite(code)["uses"] == 0


def test_disable_unknown_code_raises():
    with pytest.raises(store.InviteCodeError):
        store.disable_invite("gs-zzzz-zzzz")


def test_failed_signup_does_not_burn_the_code():
    store.create_user("alice", "pw")
    code = store.create_invite("root")
    with pytest.raises(store.HandleTakenError):
        store.signup("alice", "pw", code)
    assert _invite(code)["uses"] == 0
    # The rolled-back use is still available.
    store.signup("bob", "pw", code)
    assert _invite(code)["uses"] == 1


def test_race_on_the_last_use_admits_exactly_one():
    code = store.create_invite("root", max_uses=1)
    barrier = threading.Barrier(2)
    outcomes = {}

    def racer(handle):
        barrier.wait()
        try:
            store.signup(handle, "pw", code)
            outcomes[handle] = "ok"
        except store.InviteCodeError:
            outcomes[handle] = "spent"

    threads = [threading.Thread(target=racer, args=(h,)) for h in ("alice", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes.values()) == ["ok", "spent"]
    assert _invite(code)["uses"] == 1
    assert len(store.list_users()) == 1


def test_cli_create_and_signup_share_the_handle_rules():
    for bad in ("", "   "):
        code = store.create_invite("root")
        with pytest.raises(ValueError):
            store.signup(bad, "pw", code)
        assert _invite(code)["uses"] == 0
    code = store.create_invite("root")
    with pytest.raises(ValueError):
        store.signup("alice", "", code)
    assert _invite(code)["uses"] == 0
    # The same rejections the CLI path gives.
    with pytest.raises(ValueError):
        store.create_user("  ", "pw")
    with pytest.raises(ValueError):
        store.create_user("alice", "")
