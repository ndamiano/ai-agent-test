"""Play auth: how a browser earns the right to load a staged game's files.

The SPA (holding the bearer token) asks for a play session; the server checks ownership and
answers with a single-use HANDOFF token (~60s). The game origin redeems it at /handoff and 302s
into the game, setting a GRANT cookie scoped to that one game's path — HttpOnly, host-only, so
the game origin never holds a credential its JS can read, and game A can never fetch game B's
files. One flow whether games share the app origin or live on their own domain; the only thing
`play.origin` changes is which host the handoff URL points at.

In-memory on purpose: the control plane is one process (docs/architecture.md), and a restart
costs an open play session one more click of Play.
"""

import secrets
import threading
import time
from typing import Dict, Optional, Tuple

HANDOFF_TTL_SECONDS = 60
GRANT_TTL_SECONDS = 4 * 3600

_lock = threading.Lock()
_handoffs: Dict[str, Tuple[str, str, float]] = {}  # token -> (user_id, run_id, expires_at)
_grants: Dict[str, Tuple[str, str, float]] = {}


def _prune(table: Dict[str, Tuple[str, str, float]], now: float) -> None:
    for k in [k for k, (_, _, exp) in table.items() if exp <= now]:
        table.pop(k, None)


def issue_handoff(user_id: str, run_id: str) -> str:
    """A short-lived, single-use token the SPA passes to the game origin. It rides in a URL and
    lands in access logs, which the TTL and single use make acceptable."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _prune(_handoffs, now)
        _handoffs[token] = (user_id, run_id, now + HANDOFF_TTL_SECONDS)
    return token


def redeem_handoff(token: Optional[str]) -> Optional[Tuple[str, str]]:
    """(user_id, run_id) for a live handoff token, consuming it — a replayed URL gets nothing."""
    if not token:
        return None
    now = time.time()
    with _lock:
        entry = _handoffs.pop(token, None)
    if entry is None or entry[2] <= now:
        return None
    return entry[0], entry[1]


def issue_grant(user_id: str, run_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _prune(_grants, now)
        _grants[token] = (user_id, run_id, now + GRANT_TTL_SECONDS)
    return token


def resolve_grant(token: Optional[str]) -> Optional[Tuple[str, str]]:
    """(user_id, run_id) for a live grant — reusable until it expires; every sub-resource load of
    a play session presents it."""
    if not token:
        return None
    now = time.time()
    with _lock:
        entry = _grants.get(token)
    if entry is None or entry[2] <= now:
        return None
    return entry[0], entry[1]


def clear() -> None:
    """Test isolation only."""
    with _lock:
        _handoffs.clear()
        _grants.clear()
