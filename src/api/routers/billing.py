"""Billing — the credits storefront and the payment-provider webhook."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.deps import get_current_user
from auth.store import User
from billing import ledger
from billing.packages import PACKAGES, package_by_id
from billing.stripe import RefundEvent, get_provider
from config.settings_manager import settings_manager

router = APIRouter()
logger = logging.getLogger("auth")


class PurchaseBody(BaseModel):
    package_id: str


@router.get("/packages")
def list_packages():
    return {
        "packages": [
            p.to_json() for p in PACKAGES
        ]
    }


@router.post("/purchase")
def start_purchase(body: PurchaseBody, user: User = Depends(get_current_user)):
    package = package_by_id(body.package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="unknown package")
    purchase = ledger.create_purchase(user.id, package.id, package.credits, package.usd_cents)
    origin = (settings_manager.get_settings().get("play") or {}).get("app_origin", "").rstrip("/")
    checkout = get_provider().start_checkout(
        purchase.id, package,
        success_url=f"{origin}/credits?purchase={purchase.id}&result=success",
        cancel_url=f"{origin}/credits?purchase={purchase.id}&result=cancelled")
    ledger.set_purchase_ref(purchase.id, checkout.ref)
    return {"purchase_id": purchase.id, "status": "started", "checkout_url": checkout.url}


@router.post("/purchase/{purchase_id}/complete")
def complete_purchase(purchase_id: str, user: User = Depends(get_current_user)):
    purchase = ledger.get_purchase(purchase_id)
    if purchase is None or purchase.user_id != user.id:
        raise HTTPException(status_code=404, detail="no such purchase")
    if purchase.status == "started":
        payment_intent = get_provider().confirm_checkout(purchase.provider_ref or "")
        if not payment_intent:
            raise HTTPException(status_code=402, detail="the provider has not confirmed payment")
        ledger.set_payment_intent(purchase.id, payment_intent)
        balance = ledger.complete_purchase(purchase.id)
        if balance is not None:
            logger.info("purchase completed: user=%s +%d -> %d",
                        user.id, purchase.credits, balance)
    return {"status": "completed", "credits": purchase.credits, "balance": ledger.balance(user.id)}


@router.get("/purchases")
def purchase_history(user: User = Depends(get_current_user)):
    """Returns completed / refunded purchases only."""
    return [{"id": p.id, "package_id": p.package_id, "credits": p.credits,
             "usd_cents": p.usd_cents, "status": p.status, "created_at": p.created_at,
             "completed_at": p.completed_at}
            for p in ledger.list_purchases(user.id) if p.status != "started"]


@router.post("/webhook")
async def purchase_webhook(request: Request):
    """Stripe webhook handler"""
    payload = await request.body()
    event = get_provider().verify(payload, request.headers)
    if event is None:
        raise HTTPException(status_code=400, detail="unverified purchase event")

    if isinstance(event, RefundEvent):
        purchase = ledger.purchase_by_payment_intent(event.payment_intent)
        if purchase is None:
            raise HTTPException(status_code=400, detail="unknown payment")
        balance = ledger.refund_purchase(purchase.id)
        if balance is not None:
            logger.info("purchase refunded: user=%s -%d -> %d",
                        purchase.user_id, purchase.credits, balance)
        return {"status": "refunded"}

    purchase = ledger.get_purchase(event.purchase_id)
    if purchase is None:
        raise HTTPException(status_code=400, detail="unknown purchase")
    if event.payment_intent:
        ledger.set_payment_intent(purchase.id, event.payment_intent)
    balance = ledger.complete_purchase(purchase.id)
    if balance is not None:
        logger.info("purchase completed via webhook: user=%s +%d -> %d",
                    purchase.user_id, purchase.credits, balance)
    return {"status": "completed"}
