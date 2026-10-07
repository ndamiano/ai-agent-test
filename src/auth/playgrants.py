"""Play auth: how a browser earns the right to load a staged game's files.

The SPA (holding the bearer token) asks for a play session; the server checks ownership — or,
for a listed demo, nothing — and answers with a single-use HANDOFF token (~60s). The game origin
redeems it at /handoff and 302s into the game, setting a GRANT cookie scoped to that one folder's
path — HttpOnly, host-only, so the game origin never holds a credential its JS can read, and game
A can never fetch game B's files. One flow whether games share the app origin or live on their own domain; the only thing
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
# Both tables are unbounded only by issuance rate, and the demo surface issues without auth — the
# cap is a memory floor, far above organic use; per-IP throttling belongs to the proxy.
MAX_LIVE = 10_000

_lock = threading.Lock()
SURFACES = ("games", "demos", "shares")

_handoffs: Dict[str, Tuple[str, float]] = {}  # token -> (target, expires_at)
_grants: Dict[str, Tuple[str, float]] = {}


def target(surface: str, slug: str) -> str:
    """What a token opens: one folder under /play, under one of SURFACES. No two share a grant."""
    if surface not in SURFACES:
        raise ValueError(surface)
    return f"{surface}/{slug}"


def _prune(table: Dict[str, Tuple[str, float]], now: float) -> None:
    for k in [k for k, (_, exp) in table.items() if exp <= now]:
        table.pop(k, None)


def issue_handoff(target: str) -> Optional[str]:
    """A short-lived, single-use token the SPA passes to the game origin. It rides in a URL and
    lands in access logs, which the TTL and single use make acceptable. None ⇒ at the cap."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _prune(_handoffs, now)
        if len(_handoffs) >= MAX_LIVE:
            return None
        _handoffs[token] = (target, now + HANDOFF_TTL_SECONDS)
    return token


def redeem_handoff(token: Optional[str]) -> Optional[str]:
    """The target of a live handoff token, consuming it — a replayed URL gets nothing."""
    if not token:
        return None
    now = time.time()
    with _lock:
        entry = _handoffs.pop(token, None)
    if entry is None or entry[1] <= now:
        return None
    return entry[0]


def issue_grant(target: str) -> Optional[str]:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _prune(_grants, now)
        if len(_grants) >= MAX_LIVE:
            return None
        _grants[token] = (target, now + GRANT_TTL_SECONDS)
    return token


def resolve_grant(token: Optional[str]) -> Optional[str]:
    """The target of a live grant — reusable until it expires; every sub-resource load of a play
    session presents it."""
    if not token:
        return None
    now = time.time()
    with _lock:
        entry = _grants.get(token)
    if entry is None or entry[1] <= now:
        return None
    return entry[0]


def clear() -> None:
    """Test isolation only."""
    with _lock:
        _handoffs.clear()
        _grants.clear()
