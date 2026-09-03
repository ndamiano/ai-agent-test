"""Authentication + identity — the platform's first persistent user store.

Real auth (not cosmetic): pbkdf2-hashed passwords, opaque bearer session tokens (sha256-hashed
at rest), a request middleware that gates every route, and per-run ownership.

Signup is open; accounts can also be provisioned by hand (`python -m auth.cli`).
"""

from auth.store import User

__all__ = ["User"]
