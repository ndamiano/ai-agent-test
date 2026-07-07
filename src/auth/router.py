"""Auth endpoints. Login only — accounts are provisioned by an admin (`python -m auth.cli`),
so there is deliberately NO signup route here."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import store

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
