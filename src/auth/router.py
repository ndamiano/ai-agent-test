"""Auth endpoints. Login only — accounts are provisioned by an admin (`python -m auth.cli`),
so there is deliberately NO signup route here."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from auth import store
from auth.deps import PLAY_COOKIE, bearer_token, get_current_user
from auth.ratelimit import login_throttle
from auth.store import User

router = APIRouter()


class LoginRequest(BaseModel):
    handle: str
    password: str


def _set_play_cookie(request: Request, response: Response, token: str) -> None:
    """Mirror the session token into the /play-scoped cookie. Path=/play keeps it off every /api and
    /auth request (the API stays header-only); HttpOnly hides it from scripts; SameSite=Strict blocks
    cross-site sends. Secure is gated on https so the cookie still works over plain http on localhost
    dev — behind a TLS-terminating proxy, forward the scheme (X-Forwarded-Proto) or it won't set.

    max_age matches the token's own TTL. Without it this is a SESSION cookie, and since the bearer
    token lives in localStorage, a browser restart leaves the app signed in while every /play
    request 401s."""
    response.set_cookie(
        key=PLAY_COOKIE,
        value=token,
        path="/play",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=store.SESSION_TTL_SECONDS,
    )


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
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
    _set_play_cookie(request, response, token)
    return {
        "token": token,
        "user": {"id": user.id, "handle": user.handle, "role": user.role},
    }


@router.post("/logout")
async def logout(request: Request, response: Response, user: User = Depends(get_current_user)):
    """Revoke the presented session token server-side, so it can't be reused after logout
    (dropping it client-side alone would leave it live until its TTL)."""
    token = bearer_token(request)
    if token:
        store.revoke_token(token)
    response.delete_cookie(PLAY_COOKIE, path="/play")
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
