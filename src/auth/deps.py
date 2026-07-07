"""Auth wiring for the API: the request-gating middleware + the `get_current_user` dependency.

One app-level middleware gates every HTTP route (a valid bearer token is required for anything
that isn't explicitly public). The WebSocket path lives in a different ASGI scope that HTTP
middleware never sees, so it authenticates itself in the endpoint (see routers/websocket.py).
"""

from typing import Optional

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from auth.store import User, resolve_token

# Reachable without a token: the health probe, the login endpoint, the API docs, and the
# payment webhook (server-to-server — no user token; authed by the provider's signature,
# verified inside the CreditProvider, never by this gate).
PUBLIC_PATHS = {"/", "/auth/login", "/docs", "/redoc", "/openapi.json", "/api/billing/webhook"}


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


def install_auth(app) -> None:
    """Register the auth gate on `app`. Every request except PUBLIC_PATHS (and CORS preflight)
    must carry a valid bearer token; the resolved user is attached to `request.state.user`."""

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        # A browser <img src>/download anchor can't set an Authorization header, so a GET may
        # instead carry its token in a `token` query param (same channel the WebSocket uses).
        token = bearer_token(request) or request.query_params.get("token")
        user = resolve_token(token)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "authentication required"})
        request.state.user = user
        return await call_next(request)
