"""Billing — the credits storefront and the payment-provider webhook.

The storefront routes are user-authed like any other /api surface: list the package catalog,
open a purchase (answering with the provider's hosted checkout URL), complete it, list past
purchases. With no provider configured the store is disabled: packages report it, purchase
refuses 503. Money moves inside the active `CreditProvider`; credits move only through the
ledger, via the purchase row's started→completed flip (`store.complete_purchase`), so
completing twice grants once.

The webhook is the one PUBLIC path here (registered in `auth.deps.PUBLIC_PATHS`): a provider
POSTs server-to-server with no user token, authenticated instead by its signature, verified
inside the `CreditProvider`. An event that fails verification credits nothing and is rejected 400.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth import store
from auth.billing import PACKAGES, package_by_id
from auth.credits import get_provider, store_enabled
from auth.deps import get_current_user
from auth.store import User
from config.settings_manager import settings_manager

router = APIRouter()
logger = logging.getLogger("auth")


class PurchaseBody(BaseModel):
    package_id: str


@router.get("/packages")
def list_packages(user: User = Depends(get_current_user)):
    if not store_enabled():
        return {"enabled": False, "packages": []}
    return {"enabled": True,
            "packages": [{"id": p.id, "credits": p.credits, "usd_cents": p.usd_cents}
                         for p in PACKAGES]}


@router.post("/purchase")
def start_purchase(body: PurchaseBody, request: Request,
                   user: User = Depends(get_current_user)):
    if not store_enabled():
        raise HTTPException(status_code=503, detail="purchases are not available")
    package = package_by_id(body.package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="unknown package")
    purchase = store.create_purchase(user.id, package.id, package.credits, package.usd_cents)
    # Where Stripe sends the payer back: the configured app origin in prod; in dev, the SPA's
    # own origin from the request (the vite server, not the API it proxies to) — landing on the
    # API port is a different origin whose localStorage holds no session.
    origin = ((settings_manager.get_settings().get("play") or {}).get("app_origin", "").rstrip("/")
              or (request.headers.get("origin") or "").rstrip("/")
              or str(request.base_url).rstrip("/"))
    checkout = get_provider().start_checkout(
        purchase.id, package,
        success_url=f"{origin}/credits?purchase={purchase.id}&result=success",
        cancel_url=f"{origin}/credits?purchase={purchase.id}&result=cancelled")
    store.set_purchase_ref(purchase.id, checkout.ref)
    return {"purchase_id": purchase.id, "status": "started", "checkout_url": checkout.url}


@router.post("/purchase/{purchase_id}/complete")
def complete_purchase(purchase_id: str, user: User = Depends(get_current_user)):
    purchase = store.get_purchase(purchase_id)
    if purchase is None or purchase.user_id != user.id:
        raise HTTPException(status_code=404, detail="no such purchase")
    if purchase.status == "started":
        if not get_provider().confirm_checkout(purchase.provider_ref or ""):
            raise HTTPException(status_code=402, detail="the provider has not confirmed payment")
        balance = store.complete_purchase(purchase.id)
        if balance is not None:
            logger.info("purchase completed: user=%s +%d -> %d",
                        user.id, purchase.credits, balance)
    return {"status": "completed", "credits": purchase.credits, "balance": store.balance(user.id)}


@router.get("/purchases")
def purchase_history(user: User = Depends(get_current_user)):
    return [{"id": p.id, "package_id": p.package_id, "credits": p.credits,
             "usd_cents": p.usd_cents, "status": p.status, "created_at": p.created_at,
             "completed_at": p.completed_at}
            for p in store.list_purchases(user.id)]


@router.post("/webhook")
async def purchase_webhook(request: Request):
    """Stripe's completion push. The redirect-return also completes; whichever lands first wins
    and the other is the idempotent no-op — `complete_purchase`'s started→completed flip is the
    exactly-once gate either way."""
    payload = await request.body()
    event = get_provider().verify(payload, request.headers)
    if event is None:
        raise HTTPException(status_code=400, detail="unverified purchase event")
    purchase = store.get_purchase(event.purchase_id)
    if purchase is None:
        raise HTTPException(status_code=400, detail="unknown purchase")
    balance = store.complete_purchase(purchase.id)
    if balance is not None:
        logger.info("purchase completed via webhook: user=%s +%d -> %d",
                    purchase.user_id, purchase.credits, balance)
    return {"status": "completed"}
