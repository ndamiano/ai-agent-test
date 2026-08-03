"""Auth endpoints. Login, plus invite-code signup — an account is either provisioned by an admin
(`python -m auth.cli`) or self-created against an admin-minted invite code; there is no open
signup."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth import store
from auth.deps import bearer_token, get_current_user
from auth.ratelimit import login_throttle, signup_throttle
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


class SignupRequest(BaseModel):
    handle: str
    password: str
    invite_code: str


@router.post("/signup")
async def signup(body: SignupRequest, request: Request):
    """Create an account against an invite code and sign it in. Throttled per client IP (the
    login throttle's shape — see auth/ratelimit.py), with every failure recorded: the invite-code
    space is what the throttle defends. Redemption is atomic in the store — no code burned on a
    failed signup, no signup on a spent code."""
    key = request.client.host if request.client else "unknown"
    wait = signup_throttle.retry_after(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="too many signup attempts, try again later",
            headers={"Retry-After": str(wait)},
        )
    if not body.handle.strip() or not body.password:
        raise HTTPException(status_code=400, detail="handle and password are required")
    try:
        user = store.signup(body.handle, body.password, body.invite_code)
    except store.InviteCodeError as e:
        signup_throttle.record_failure(key)
        raise HTTPException(status_code=403, detail=str(e))
    except store.HandleTakenError:
        signup_throttle.record_failure(key)
        raise HTTPException(status_code=409, detail="that handle is already taken")
    signup_throttle.clear(key)
    token = store.issue_token(user.id)
    return {
        "token": token,
        "user": {"id": user.id, "handle": user.handle, "role": user.role},
    }


@router.post("/logout")
async def logout(request: Request, user: User = Depends(get_current_user)):
    """Revoke the presented session token server-side, so it can't be reused after logout
    (dropping it client-side alone would leave it live until its TTL). Play grants are not
    touched: they are per-game, path-scoped, and expire on their own short TTL."""
    token = bearer_token(request)
    if token:
        store.revoke_token(token)
    return {"ok": True}


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/password")
async def change_password(body: PasswordChangeRequest, user: User = Depends(get_current_user)):
    """Change the signed-in user's password, gated on the current one — a stolen bearer token
    alone must not be enough to take the account over. Wrong guesses share the login throttle,
    so the current-password check can't be brute-forced faster than the login form."""
    key = user.handle.strip().lower()
    wait = login_throttle.retry_after(key)
    if wait:
        raise HTTPException(status_code=429, detail="too many attempts, try again later",
                            headers={"Retry-After": str(wait)})
    if store.authenticate(user.handle, body.current_password) is None:
        login_throttle.record_failure(key)
        raise HTTPException(status_code=403, detail="current password is wrong")
    login_throttle.clear(key)
    if not body.new_password:
        raise HTTPException(status_code=400, detail="new password is required")
    store.set_password(user.handle, body.new_password)
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
