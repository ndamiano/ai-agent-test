"""Auth endpoints. Login only — accounts are provisioned by an admin (`python -m auth.cli`),
so there is deliberately NO signup route here."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth import store
from auth.deps import bearer_token, get_current_user
from auth.ratelimit import login_throttle
from auth.store import User

router = APIRouter()


class LoginRequest(BaseModel):
    handle: str
    password: str


@router.post("/login")
async def login(body: LoginRequest):
    key = body.handle.strip().lower()
    wait = login_throttle.retry_after(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="too many login attempts, try again later",
            headers={"Retry-After": str(wait)},
        )
    user = store.authenticate(body.handle, body.password)
    if user is None:
        login_throttle.record_failure(key)
        raise HTTPException(status_code=401, detail="invalid handle or password")
    login_throttle.clear(key)
    token = store.issue_token(user.id)
    return {
        "token": token,
        "user": {"id": user.id, "handle": user.handle, "role": user.role},
    }


@router.post("/logout")
async def logout(request: Request, user: User = Depends(get_current_user)):
    """Revoke the presented session token server-side, so it can't be reused after logout
    (dropping it client-side alone would leave it live until its TTL)."""
    token = bearer_token(request)
    if token:
        store.revoke_token(token)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    """The authed user + their live credit balance — the header's balance source. Gated: no token
    (or a bad one) never reaches here, the middleware 401s first."""
    return {
        "id": user.id,
        "handle": user.handle,
        "role": user.role,
        "balance": store.balance(user.id),
    }
