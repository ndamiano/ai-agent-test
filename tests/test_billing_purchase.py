"""The credits storefront: the package catalog, start→complete through the provider seam, and the
purchase history. Completion grants through the ledger exactly once — a double-complete is
answered, not re-credited. With no provider configured the store is disabled and refuses."""

import pytest

from auth import store
from auth.billing import PACKAGES
from auth.credits import Checkout, CreditProvider


class _PaidProvider(CreditProvider):
    """A checkout that the provider reports as paid — what a completed Stripe session answers."""

    def start_checkout(self, purchase_id, package, success_url, cancel_url):
        return Checkout(ref=f"cs_{purchase_id}", url=f"https://pay.example/{purchase_id}")

    def confirm_checkout(self, provider_ref):
        return provider_ref.startswith("cs_")

    def verify(self, payload, headers):
        return None


@pytest.fixture(autouse=True)
def paid_provider(monkeypatch):
    # get_provider re-reads settings on every call, so the patch lands on the router's imported
    # names, not on the module-level cache it would overwrite.
    import api.routers.billing as billing_router
    provider = _PaidProvider()
    monkeypatch.setattr(billing_router, "get_provider", lambda: provider)
    monkeypatch.setattr(billing_router, "store_enabled", lambda: True)


def _authed_user(handle="alice"):
    user = store.create_user(handle, "pw")
    token = store.issue_token(user.id)
    return user, {"Authorization": f"Bearer {token}"}


def _purchase_ledger_rows(user_id):
    with store._db() as conn:
        return conn.execute(
            "SELECT delta FROM credit_transactions WHERE user_id = ? AND reason = 'purchase'",
            (user_id,),
        ).fetchall()


def test_storefront_routes_reject_anonymous(app_client):
    assert app_client.get("/api/billing/packages").status_code == 401
    assert app_client.post("/api/billing/purchase", json={"package_id": "1"}).status_code == 401
    assert app_client.get("/api/billing/purchases").status_code == 401


def test_packages_list_the_catalog_at_flat_pricing(app_client):
    _, hdr = _authed_user()
    r = app_client.get("/api/billing/packages", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert [(p["id"], p["credits"], p["usd_cents"]) for p in body["packages"]] == \
        [(p.id, p.credits, p.usd_cents) for p in PACKAGES]
    for p in body["packages"]:
        assert p["usd_cents"] == p["credits"] * 500


def test_purchase_grants_credits_exactly_once(app_client):
    user, hdr = _authed_user()

    started = app_client.post("/api/billing/purchase", json={"package_id": "5"}, headers=hdr)
    assert started.status_code == 200
    assert started.json()["checkout_url"].startswith("https://pay.example/")
    assert store.balance(user.id) == 0  # starting is not paying

    done = app_client.post(
        f"/api/billing/purchase/{started.json()['purchase_id']}/complete", headers=hdr)
    assert done.status_code == 200
    assert done.json() == {"status": "completed", "credits": 5, "balance": 5}
    assert store.balance(user.id) == 5
    assert [r["delta"] for r in _purchase_ledger_rows(user.id)] == [5]


def test_double_complete_is_idempotent(app_client):
    user, hdr = _authed_user()
    pid = app_client.post("/api/billing/purchase", json={"package_id": "1"},
                          headers=hdr).json()["purchase_id"]

    first = app_client.post(f"/api/billing/purchase/{pid}/complete", headers=hdr)
    second = app_client.post(f"/api/billing/purchase/{pid}/complete", headers=hdr)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == {"status": "completed", "credits": 1, "balance": 1}
    assert store.balance(user.id) == 1
    assert [r["delta"] for r in _purchase_ledger_rows(user.id)] == [1]


def test_unknown_package_is_refused(app_client):
    user, hdr = _authed_user()
    r = app_client.post("/api/billing/purchase", json={"package_id": "100"}, headers=hdr)
    assert r.status_code == 404
    assert store.list_purchases(user.id) == []


def test_completing_another_users_purchase_is_refused(app_client):
    alice, alice_hdr = _authed_user("alice")
    _, mallory_hdr = _authed_user("mallory")
    pid = app_client.post("/api/billing/purchase", json={"package_id": "10"},
                          headers=alice_hdr).json()["purchase_id"]

    r = app_client.post(f"/api/billing/purchase/{pid}/complete", headers=mallory_hdr)
    assert r.status_code == 404
    assert store.balance(alice.id) == 0
    assert store.get_purchase(pid).status == "started"


def test_history_lists_purchases_newest_first(app_client):
    user, hdr = _authed_user()
    first = app_client.post("/api/billing/purchase", json={"package_id": "1"},
                            headers=hdr).json()["purchase_id"]
    app_client.post(f"/api/billing/purchase/{first}/complete", headers=hdr)
    second = app_client.post("/api/billing/purchase", json={"package_id": "10"},
                             headers=hdr).json()["purchase_id"]
    app_client.post(f"/api/billing/purchase/{second}/complete", headers=hdr)

    r = app_client.get("/api/billing/purchases", headers=hdr)
    assert r.status_code == 200
    rows = r.json()
    assert [row["id"] for row in rows] == [second, first]
    assert [(row["credits"], row["usd_cents"], row["status"]) for row in rows] == \
        [(10, 5000, "completed"), (1, 500, "completed")]
    assert all(row["completed_at"] is not None for row in rows)


def test_history_is_scoped_to_the_caller(app_client):
    _, alice_hdr = _authed_user("alice")
    _, bob_hdr = _authed_user("bob")
    pid = app_client.post("/api/billing/purchase", json={"package_id": "1"},
                          headers=alice_hdr).json()["purchase_id"]
    app_client.post(f"/api/billing/purchase/{pid}/complete", headers=alice_hdr)

    assert app_client.get("/api/billing/purchases", headers=bob_hdr).json() == []


def test_disabled_store_reports_and_refuses(app_client, monkeypatch):
    import api.routers.billing as billing_router
    monkeypatch.setattr(billing_router, "store_enabled", lambda: False)
    user, hdr = _authed_user("carol")

    r = app_client.get("/api/billing/packages", headers=hdr)
    assert r.json() == {"enabled": False, "packages": []}
    assert app_client.post("/api/billing/purchase", json={"package_id": "1"},
                           headers=hdr).status_code == 503
    assert store.list_purchases(user.id) == []


def test_unpaid_checkout_never_grants(app_client, monkeypatch):
    """The provider's word, not the client's return visit, is what authorizes the grant."""
    class Unpaid(_PaidProvider):
        def confirm_checkout(self, provider_ref):
            return False

    import api.routers.billing as billing_router
    unpaid = Unpaid()
    monkeypatch.setattr(billing_router, "get_provider", lambda: unpaid)
    user, hdr = _authed_user("dave")
    pid = app_client.post("/api/billing/purchase", json={"package_id": "1"},
                          headers=hdr).json()["purchase_id"]
    r = app_client.post(f"/api/billing/purchase/{pid}/complete", headers=hdr)
    assert r.status_code == 402
    assert store.balance(user.id) == 0


def test_checkout_returns_to_the_callers_origin_in_dev(app_client, monkeypatch):
    """With no app_origin configured, the return URL is the SPA's origin from the request —
    landing on the API port is a different origin whose localStorage holds no session."""
    import api.routers.billing as billing_router
    seen = {}

    class Capturing(_PaidProvider):
        def start_checkout(self, purchase_id, package, success_url, cancel_url):
            seen["success"] = success_url
            return super().start_checkout(purchase_id, package, success_url, cancel_url)

    capturing = Capturing()
    monkeypatch.setattr(billing_router, "get_provider", lambda: capturing)
    _, hdr = _authed_user("erin")
    app_client.post("/api/billing/purchase", json={"package_id": "1"},
                    headers={**hdr, "Origin": "http://localhost:5173"})
    assert seen["success"].startswith("http://localhost:5173/credits?purchase=")
