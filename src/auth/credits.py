"""Credit top-up seam — provider-agnostic purchases.

Two paths reach the ledger through the active `CreditProvider`:

- The storefront checkout: the billing router opens a purchase (`start_checkout`), the user pays
  on the provider's side, and completion asks the provider (`confirm_checkout`) before the ledger
  is credited — the provider's answer, never the client's word, is what authorizes the grant.
- The webhook: a provider POSTs a signed purchase event. The webhook does NOT trust the payload;
  it hands the raw bytes + headers to `verify`, which checks the provider's own signature and
  returns who to credit and how much. An event that fails verification yields None and credits
  nothing.

The provider is Stripe, and both keys are required: the webhook signature is an HMAC under
`stripe_webhook_secret`, so an empty secret is one anyone can sign with — a forged completion
would be free credits. A box missing either key refuses every billing call instead.

Stripe rides plain HTTPS over the `requests` we already pin (no SDK): checkout is two
form-encoded calls, and the webhook signature is one documented HMAC — the same loud-failure
class as tools/s3.py, where a wrong key or signature is a 4xx, never a silent success.
"""

import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional

import requests

from auth.billing import Package
from config.settings_manager import settings_manager


@dataclass(frozen=True)
class Checkout:
    """An opened checkout: the provider's reference (what `confirm_checkout` takes) and the
    hosted payment page the user is sent to."""
    ref: str
    url: str


@dataclass(frozen=True)
class PurchaseEvent:
    """A verified webhook's claim: this purchase was paid."""
    purchase_id: str
    payment_intent: Optional[str] = None


@dataclass(frozen=True)
class RefundEvent:
    """A verified webhook's claim: this payment was refunded or charged back — the credits it
    bought come back out, negative balance included."""
    payment_intent: str


class CreditProvider(ABC):
    @abstractmethod
    def start_checkout(self, purchase_id: str, package: Package,
                       success_url: str, cancel_url: str) -> Checkout:
        """Open a checkout with the provider for this purchase."""

    @abstractmethod
    def confirm_checkout(self, provider_ref: str) -> Optional[str]:
        """Ask the provider whether this checkout was actually paid. Returns the provider's
        payment id when it was (what a refund event later names), None when it was not."""

    @abstractmethod
    def verify(self, payload: bytes,
               headers: Mapping[str, str]) -> "Optional[PurchaseEvent | RefundEvent]":
        """Verify a raw webhook event against the provider's signature. Return the paid or
        refunded purchase it announces, or None if it doesn't verify (nothing moves)."""


STRIPE_API = "https://api.stripe.com/v1"
WEBHOOK_TOLERANCE_SECONDS = 300


class StripeProvider(CreditProvider):
    """Stripe Checkout, hosted: card data never touches us. Prices ride the session inline
    (price_data), so nothing is configured in the Stripe dashboard but the keys."""

    def __init__(self, secret_key: str, webhook_secret: str) -> None:
        self._key = secret_key
        self._webhook_secret = webhook_secret

    def _post(self, path: str, fields: Mapping[str, str]) -> dict:
        res = requests.post(f"{STRIPE_API}{path}", data=fields, timeout=30,
                            auth=(self._key, ""))
        if res.status_code != 200:
            raise RuntimeError(f"stripe {path}: {res.status_code} "
                               f"{(res.json().get('error') or {}).get('message', '')[:200]}")
        return res.json()

    def start_checkout(self, purchase_id: str, package: Package,
                       success_url: str, cancel_url: str) -> Checkout:
        session = self._post("/checkout/sessions", {
            "mode": "payment",
            "client_reference_id": purchase_id,
            "metadata[purchase_id]": purchase_id,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(package.usd_cents),
            "line_items[0][price_data][product_data][name]":
                f"{package.credits} credit{'s' if package.credits != 1 else ''}",
            # Managed-payments accounts refuse a session without a product tax code. General
            # electronically-supplied services; confirming it is on the counsel checklist.
            "line_items[0][price_data][product_data][tax_code]": "txcd_10000000",
        })
        return Checkout(ref=session["id"], url=session["url"])

    def confirm_checkout(self, provider_ref: str) -> Optional[str]:
        res = requests.get(f"{STRIPE_API}/checkout/sessions/{provider_ref}", timeout=30,
                           auth=(self._key, ""))
        if res.status_code != 200 or res.json().get("payment_status") != "paid":
            return None
        return res.json().get("payment_intent") or provider_ref

    def verify(self, payload: bytes,
               headers: Mapping[str, str]) -> "Optional[PurchaseEvent | RefundEvent]":
        # Stripe-Signature: t=<unix>,v1=<hmac-sha256 of "<t>.<payload>" under the whsec>.
        # Refusal is None, never a raise — the webhook is public and a raise is a 500 anyone
        # can trigger.
        sig = headers.get("stripe-signature") or headers.get("Stripe-Signature") or ""
        parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
        t, v1 = parts.get("t"), parts.get("v1")
        if not t or not v1 or not t.isdigit():
            return None
        if abs(time.time() - int(t)) > WEBHOOK_TOLERANCE_SECONDS:
            return None
        expected = hmac.new(self._webhook_secret.encode(), f"{t}.".encode() + payload,
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, v1):
            return None
        event = json.loads(payload)
        obj = (event.get("data") or {}).get("object") or {}
        kind = event.get("type")
        if kind == "checkout.session.completed":
            if obj.get("payment_status") != "paid":
                return None
            purchase_id = ((obj.get("metadata") or {}).get("purchase_id")
                           or obj.get("client_reference_id"))
            if not purchase_id:
                return None
            return PurchaseEvent(purchase_id=purchase_id,
                                 payment_intent=obj.get("payment_intent"))
        if kind in ("charge.refunded", "charge.dispute.created"):
            pi = obj.get("payment_intent")
            return RefundEvent(payment_intent=pi) if pi else None
        return None


_active: Optional[CreditProvider] = None


def get_provider() -> CreditProvider:
    global _active
    if _active is None:
        cfg = settings_manager.get_settings().get("payments") or {}
        key = cfg.get("stripe_secret_key") or ""
        webhook_secret = cfg.get("stripe_webhook_secret") or ""
        if not key or not webhook_secret:
            raise RuntimeError("payments.stripe_secret_key and payments.stripe_webhook_secret "
                               "must both be set")
        _active = StripeProvider(key, webhook_secret)
    return _active
