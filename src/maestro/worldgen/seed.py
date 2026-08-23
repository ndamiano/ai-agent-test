"""One stable seed for a name.

Not `hash()`: string hashing is salted per process, so the same world would
reconstruct differently on every run and a failure would not reproduce.
"""
from __future__ import annotations

import zlib


def seed_for(*parts: object) -> int:
    return zlib.crc32("/".join(str(p) for p in parts).encode())


__all__ = ["seed_for"]
