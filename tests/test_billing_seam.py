"""T4 buy-credits seam: the admin grant CLI and the provider webhook.

CLI grant credits a real handle (balance up, txn logged) and fails cleanly on an unknown handle.
The webhook credits the ledger on a (stubbed) verified event, rejects an unverified one, and is
NOT blocked by the user-auth middleware — a provider posts server-to-server with no user token.
"""

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.app import app
from auth import cli, credits, store
from auth.credits import CreditProvider, PurchaseEvent
from db import store as db_store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "auth.db")


def _ledger_rows(user_id, reason):
    with store._db() as conn:
        return conn.execute(
            "SELECT delta FROM credit_transactions WHERE user_id = ? AND reason = ?",
            (user_id, reason),
        ).fetchall()


# ── admin grant CLI ───────────────────────────────────────────────────────────
def test_cli_grant_increments_balance_and_logs_a_transaction(capsys):
    user = store.create_user("alice", "pw")
    assert cli.main(["grant", "alice", "50"]) == 0
    assert store.balance(user.id) == store.INITIAL_CREDITS + 50
    rows = _ledger_rows(user.id, "admin_grant")
    assert [r["delta"] for r in rows] == [50]
    assert "balance=" in capsys.readouterr().out


def test_cli_grant_on_unknown_handle_fails_cleanly():
    with pytest.raises(SystemExit) as exc:
        cli.main(["grant", "ghost", "50"])
    assert exc.value.code  # non-zero exit, clean message (not a traceback)
    assert "ghost" in str(exc.value.code)


# ── provider webhook ──────────────────────────────────────────────────────────
class _StubProvider(CreditProvider):
    """Verifies iff the payload carries a matching secret — stands in for signature checking."""

    def __init__(self, user_id, credits_n, secret=b"ok"):
        self._user_id, self._credits, self._secret = user_id, credits_n, secret

    def verify(self, payload, headers):
        if payload != self._secret:
            return None
        return PurchaseEvent(user_id=self._user_id, credits=self._credits)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    return TestClient(app)


def test_webhook_credits_the_ledger_on_a_verified_event(app_client, monkeypatch):
    user = store.create_user("alice", "pw")
    monkeypatch.setattr(credits, "_provider", _StubProvider(user.id, 25))

    r = app_client.post("/api/billing/webhook", content=b"ok")
    assert r.status_code == 200
    assert r.json() == {"credited": 25, "balance": store.INITIAL_CREDITS + 25}
    assert store.balance(user.id) == store.INITIAL_CREDITS + 25
    assert [row["delta"] for row in _ledger_rows(user.id, "purchase")] == [25]


def test_webhook_rejects_an_unverified_event_and_credits_nothing(app_client, monkeypatch):
    user = store.create_user("alice", "pw")
    monkeypatch.setattr(credits, "_provider", _StubProvider(user.id, 25))

    r = app_client.post("/api/billing/webhook", content=b"tampered")
    assert r.status_code == 400
    assert store.balance(user.id) == store.INITIAL_CREDITS
    assert _ledger_rows(user.id, "purchase") == []


def test_webhook_is_not_blocked_by_the_user_auth_gate(app_client, monkeypatch):
    # No Authorization header — a gated route 401s, but the webhook reaches its handler (here a
    # clean 400 from the provider refusing) rather than being turned away at the gate.
    monkeypatch.setattr(credits, "_provider", _StubProvider("nobody", 1, secret=b"never"))
    assert app_client.get("/api/games").status_code == 401
    r = app_client.post("/api/billing/webhook", content=b"anything")
    assert r.status_code == 400
