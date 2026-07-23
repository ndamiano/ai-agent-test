"""Auth wiring for the API: the request-gating middleware + the `get_current_user` dependency.

One app-level middleware gates every HTTP route (a valid bearer token is required for anything
that isn't explicitly public). The WebSocket path lives in a different ASGI scope that HTTP
middleware never sees, so it authenticates itself in the endpoint (see routers/websocket.py).
"""

import os
from typing import Optional

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from auth.store import User, balance, resolve_token
from db import store as db_store

# Reachable without a token: the health probe, the login endpoint, and the payment webhook
# (server-to-server — no user token; authed by the provider's signature, verified inside the
# CreditProvider, never by this gate). The API docs exist only in dev (see api/app.py), so
# they're public only there.
PUBLIC_PATHS = {"/", "/auth/login", "/api/billing/webhook"}
if os.getenv("MAESTRO_DEV") == "1":
    PUBLIC_PATHS |= {"/docs", "/redoc", "/openapi.json"}

# The /play game harness is static HTML/JS the browser loads with plain <script>/<img>/fetch — no
# way to attach a Bearer header to those sub-resource requests. So /play alone authenticates by a
# cookie (`maestro_play`) the browser sends automatically, scoped to Path=/play so it NEVER rides
# any /api or /auth request: the whole API surface stays strictly header-only, cookie-immune (and
# thus CSRF-immune). This cookie is the single, deliberate deviation from that model — see the
# /play branch in `install_auth`.
PLAY_COOKIE = "maestro_play"


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip() or None
    return None


def get_current_user(request: Request) -> User:
    """FastAPI dependency — returns the user the middleware attached to this request."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_credits(request: Request) -> User:
    """`get_current_user` plus a positive credit balance.

    For inference that is never CHARGED but must not be free to everyone: chat and spec drafting
    run before a game exists to bill, and metering them would make an abandoned conversation cost
    the user real money. Free is not the same as open, though — at a zero balance nothing that
    conversation could produce is buildable, so the GPU time behind it has no path to revenue."""
    user = get_current_user(request)
    remaining = balance(user.id)
    if remaining <= 0:
        raise HTTPException(status_code=402, detail={
            "reason": "insufficient_credits", "balance": remaining, "cost": 0})
    return user


def require_admin(request: Request) -> User:
    """`get_current_user` plus the admin role. Gates the operator-only surfaces (queue stats,
    fleet spend). A signed-in non-admin gets a 403, not a 401 — they ARE authenticated, they just
    can't see this."""
    user = get_current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user


def install_auth(app) -> None:
    """Register the auth gate on `app`. Every request except PUBLIC_PATHS (and CORS preflight)
    must carry a valid bearer token; the resolved user is attached to `request.state.user`."""

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or path in PUBLIC_PATHS:
            return await call_next(request)
        # The static game harness — cookie-gated, ownership-checked (a separate model, see below).
        if path == "/play" or path.startswith("/play/"):
            return await _play_gate(request, call_next)
        # The API lives under /api and /auth; everything else is the static SPA shell + its
        # assets, which the browser must fetch (unauthenticated) before it can even show the
        # login form. So gate only the API surfaces — the frontend is served in the clear.
        if not path.startswith("/api") and not path.startswith("/auth"):
            return await call_next(request)
        # Header-only: a token never rides in the URL, so it can't leak into access logs, browser
        # history, or Referer. Browser <img>/download fetches attach the header via authed fetch +
        # blob. The WebSocket (a separate ASGI scope this middleware never sees) authenticates
        # itself from its own `token` query param — see routers/websocket.py.
        token = bearer_token(request)
        user = resolve_token(token)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "authentication required"})
        request.state.user = user
        return await call_next(request)

    async def _play_gate(request: Request, call_next):
        """Gate a /play request by the `maestro_play` cookie (never the Authorization header — the
        static harness can't set one on its sub-resource fetches). A valid session is required for
        every /play path; a per-game bundle/asset under /play/games/<id>/ ALSO requires ownership,
        so one signed-in user can't open another's game by guessing its run id."""
        user = resolve_token(request.cookies.get(PLAY_COOKIE))
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "authentication required"})
        parts = request.url.path.split("/")  # ["", "play", "games", "<id>", ...]
        if len(parts) >= 4 and parts[2] == "games":
            if db_store.owner_of(parts[3]) != user.id:
                return JSONResponse(status_code=403, content={"detail": "not your game"})
        request.state.user = user
        return await call_next(request)
