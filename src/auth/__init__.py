"""Authentication + identity — the platform's first persistent user store.

Real auth (not cosmetic): pbkdf2-hashed passwords, opaque bearer session tokens (sha256-hashed
at rest), a request middleware that gates every route, and per-run ownership.

Accounts are provisioned MANUALLY (`python -m auth.cli`). There is deliberately no signup route.
"""

from auth.store import User

__all__ = ["User"]
