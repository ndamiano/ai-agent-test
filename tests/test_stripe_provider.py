import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auth.billing import PACKAGES
from auth.credits import StripeProvider, get_provider
import auth.credits as credits


def _provider() -> StripeProvider:
    return StripeProvider("sk_test_key", "whsec_testsecret")


def _signed(payload: bytes, secret: str = "whsec_testsecret", t: int | None = None) -> dict:
    t = int(time.time()) if t is None else t
    sig = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={t},v1={sig}"}


def _event(purchase_id="p1", event_type="checkout.session.completed", status="paid") -> bytes:
    return json.dumps({
        "type": event_type,
        "data": {"object": {"payment_status": status,
                            "metadata": {"purchase_id": purchase_id},
                            "client_reference_id": purchase_id}},
    }).encode()


# ---------------------------------------------------------------- webhook signature

def test_a_correctly_signed_paid_event_verifies():
    ev = _provider().verify(_event("p42"), _signed(_event("p42")))
    assert ev is not None and ev.purchase_id == "p42"


def test_a_tampered_payload_is_refused():
    headers = _signed(_event("p42"))
    assert _provider().verify(_event("p99"), headers) is None


def test_a_wrong_secret_is_refused():
    payload = _event()
    assert _provider().verify(payload, _signed(payload, secret="whsec_other")) is None


def test_a_stale_timestamp_is_refused():
    payload = _event()
    old = int(time.time()) - 3600
    assert _provider().verify(payload, _signed(payload, t=old)) is None


def test_missing_or_malformed_signature_is_refused_not_raised():
    payload = _event()
    assert _provider().verify(payload, {}) is None
    assert _provider().verify(payload, {"stripe-signature": "garbage"}) is None


def test_an_unpaid_or_offtopic_event_is_refused():
    unpaid = _event(status="unpaid")
    assert _provider().verify(unpaid, _signed(unpaid)) is None
    other = _event(event_type="invoice.created")
    assert _provider().verify(other, _signed(other)) is None


# ---------------------------------------------------------------- checkout calls

class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_start_checkout_sends_the_package_as_inline_price(monkeypatch):
    seen = {}

    def fake_post(url, data=None, timeout=None, auth=None):
        seen.update({"url": url, "data": data, "auth": auth})
        return _Resp(200, {"id": "cs_123", "url": "https://checkout.stripe.com/c/cs_123"})

    monkeypatch.setattr(credits.requests, "post", fake_post)
    pkg = PACKAGES[1]
    out = _provider().start_checkout("p7", pkg, "https://app/ok", "https://app/no")

    assert out.ref == "cs_123" and out.url.startswith("https://checkout.stripe.com/")
    assert seen["url"].endswith("/checkout/sessions")
    assert seen["auth"] == ("sk_test_key", "")
    d = seen["data"]
    assert d["metadata[purchase_id]"] == "p7"
    assert d["line_items[0][price_data][unit_amount]"] == str(pkg.usd_cents)
    assert d["success_url"] == "https://app/ok"


def test_a_stripe_error_is_loud(monkeypatch):
    monkeypatch.setattr(credits.requests, "post",
                        lambda *a, **k: _Resp(401, {"error": {"message": "Invalid API Key"}}))
    try:
        _provider().start_checkout("p7", PACKAGES[0], "https://a", "https://b")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "Invalid API Key" in str(e)


def test_confirm_checkout_answers_the_payment_id_iff_paid(monkeypatch):
    monkeypatch.setattr(credits.requests, "get",
                        lambda *a, **k: _Resp(200, {"payment_status": "paid",
                                                    "payment_intent": "pi_9"}))
    assert _provider().confirm_checkout("cs_123") == "pi_9"
    monkeypatch.setattr(credits.requests, "get",
                        lambda *a, **k: _Resp(200, {"payment_status": "unpaid"}))
    assert _provider().confirm_checkout("cs_123") is None


# ---------------------------------------------------------------- provider selection

@pytest.mark.parametrize("payments", [
    {},
    {"stripe_secret_key": "sk_test_x", "stripe_webhook_secret": ""},
    {"stripe_secret_key": "", "stripe_webhook_secret": "whsec_y"},
])
def test_a_missing_key_refuses_to_build_a_provider(monkeypatch, payments):
    """An empty webhook secret is an HMAC key anyone can sign with — a forged completion is
    free credits, so no provider is ever built without it."""
    from config.settings_manager import settings_manager
    monkeypatch.setattr(settings_manager, "get_settings", lambda: {"payments": payments})
    monkeypatch.setattr(credits, "_active", None)
    with pytest.raises(RuntimeError):
        get_provider()


def test_both_keys_select_stripe(monkeypatch):
    from config.settings_manager import settings_manager
    monkeypatch.setattr(settings_manager, "get_settings",
                        lambda: {"payments": {"stripe_secret_key": "sk_test_x",
                                              "stripe_webhook_secret": "whsec_y"}})
    monkeypatch.setattr(credits, "_active", None)
    assert isinstance(get_provider(), StripeProvider)
