"""Auth endpoints. Login only — accounts are provisioned by an admin (`python -m auth.cli`),
so there is deliberately NO signup route here."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import store
from auth.deps import get_current_user
from auth.store import User

router = APIRouter()


class LoginRequest(BaseModel):
    handle: str
    password: str


@router.post("/login")
async def login(body: LoginRequest):
    user = store.authenticate(body.handle, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid handle or password")
    token = store.issue_token(user.id)
    return {
        "token": token,
        "user": {"id": user.id, "handle": user.handle, "role": user.role},
    }


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
