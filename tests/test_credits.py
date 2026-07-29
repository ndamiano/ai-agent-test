"""Credit ledger behaviour — grant/deduct/refund, atomic non-negative deduct, reconciliation."""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auth import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "auth.db")


def _ledger_sum(user_id):
    with store._db() as conn:
        rows = conn.execute("SELECT delta FROM credit_transactions WHERE user_id = ?",
                            (user_id,)).fetchall()
    return sum(r["delta"] for r in rows)


def test_create_user_starts_broke_with_an_empty_ledger():
    user = store.create_user("alice", "pw")
    assert store.balance(user.id) == 0
    assert _ledger_sum(user.id) == 0   # no grant-on-create — credits arrive only via grant()


def test_deduct_reduces_balance_and_logs_a_negative_entry():
    user = store.create_user("alice", "pw")
    store.grant(user.id, 10, "admin_grant")
    assert store.deduct(user.id, 1, "build", run_id="r1") is True
    assert store.balance(user.id) == 9
    assert _ledger_sum(user.id) == store.balance(user.id)


def test_deduct_refuses_to_go_negative():
    user = store.create_user("alice", "pw")
    store.grant(user.id, 5, "admin_grant")
    assert store.deduct(user.id, 5, "build") is True
    assert store.balance(user.id) == 0
    assert store.deduct(user.id, 1, "build") is False
    assert store.balance(user.id) == 0
    assert _ledger_sum(user.id) == 0


def test_refund_restores_the_balance_and_reconciles():
    user = store.create_user("alice", "pw")
    store.grant(user.id, 10, "admin_grant")
    store.deduct(user.id, 3, "build", run_id="r1")
    store.refund(user.id, 3, "build_failed", run_id="r1")
    assert store.balance(user.id) == 10
    assert _ledger_sum(user.id) == store.balance(user.id)


def test_grant_adds_and_returns_new_balance():
    user = store.create_user("alice", "pw")
    new_balance = store.grant(user.id, 5, "admin_topup")
    assert new_balance == 5
    assert store.balance(user.id) == new_balance


def test_balance_of_unknown_user_is_zero():
    assert store.balance("nobody") == 0


def test_concurrent_deducts_of_the_last_credit_only_one_wins():
    user = store.create_user("alice", "pw")
    store.grant(user.id, 1, "admin_grant")
    assert store.balance(user.id) == 1

    barrier = threading.Barrier(2)
    results = []

    def race():
        barrier.wait()
        results.append(store.deduct(user.id, 1, "build"))

    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]
    assert store.balance(user.id) == 0
    assert _ledger_sum(user.id) == 0          # the +1 grant and the one winning −1 deduct
