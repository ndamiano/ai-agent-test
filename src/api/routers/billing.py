"""Billing — the credits storefront and the payment-provider webhook.

The storefront routes are user-authed like any other /api surface: list the package catalog,
open a purchase, complete it, list past purchases. Money moves (or, today, pretends to) inside
the active `CreditProvider`; credits move only through the ledger, via the purchase row's
started→completed flip (`store.complete_purchase`), so completing twice grants once.

The webhook is the one PUBLIC path here (registered in `auth.deps.PUBLIC_PATHS`): a provider
POSTs server-to-server with no user token, authenticated instead by its signature, verified
inside the `CreditProvider`. An event that fails verification credits nothing and is rejected 400.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth import store
from auth.billing import PACKAGES, package_by_id
from auth.credits import get_provider
from auth.deps import get_current_user
from auth.store import User

router = APIRouter()
logger = logging.getLogger("auth")


class PurchaseBody(BaseModel):
    package_id: str


@router.get("/packages")
def list_packages(user: User = Depends(get_current_user)):
    return [{"id": p.id, "credits": p.credits, "usd_cents": p.usd_cents} for p in PACKAGES]


@router.post("/purchase")
def start_purchase(body: PurchaseBody, user: User = Depends(get_current_user)):
    package = package_by_id(body.package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="unknown package")
    purchase = store.create_purchase(user.id, package.id, package.credits, package.usd_cents)
    ref = get_provider().start_checkout(purchase.id, package)
    store.set_purchase_ref(purchase.id, ref)
    return {"purchase_id": purchase.id, "status": "started"}


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
    payload = await request.body()
    event = get_provider().verify(payload, request.headers)
    if event is None:
        raise HTTPException(status_code=400, detail="unverified purchase event")
    balance = store.grant(event.user_id, event.credits, "purchase")
    logger.info("purchase credited: user=%s +%d -> %d", event.user_id, event.credits, balance)
    return {"credited": event.credits, "balance": balance}
