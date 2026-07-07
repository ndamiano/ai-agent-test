"""Payment-provider webhook — the credit top-up seam.

Server-to-server: a provider (Stripe/Paddle) POSTs a signed purchase event. There is no user
token here (the provider can't send one), so this path is PUBLIC to the user-auth middleware
(registered in `auth.deps.PUBLIC_PATHS`) and authenticated INSTEAD by the provider's signature,
verified inside the active `CreditProvider`. An event that fails verification credits nothing and
is rejected 400.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from auth import store
from auth.credits import get_provider

router = APIRouter()
logger = logging.getLogger("auth")


@router.post("/webhook")
async def purchase_webhook(request: Request):
    payload = await request.body()
    event = get_provider().verify(payload, request.headers)
    if event is None:
        raise HTTPException(status_code=400, detail="unverified purchase event")
    balance = store.grant(event.user_id, event.credits, "purchase")
    logger.info("purchase credited: user=%s +%d -> %d", event.user_id, event.credits, balance)
    return {"credited": event.credits, "balance": balance}
