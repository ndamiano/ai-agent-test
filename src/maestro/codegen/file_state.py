"""What the model has already read, and whether it has changed since.

Measured 2026-09-08 (run 67a1f6e389f4, 10 compactions): 90 of 95 post-compaction reads were of
files the model had ALREADY read in the same build. The code map re-grounds it on what the project
contains, and it goes back and reads the files anyway — a stub that says where the bytes are does
not say whether reading them would tell the model anything it does not have.

So the compaction note carries the one fact that makes a re-read visibly pointless: this file is
byte for byte what you read at step N. Every harness surveyed tracks the opposite — Cline's
`FileContextTracker` says "these changed, you may need to re-read them" and nobody says the
inverse — and the same failure is open against another harness with the same shape (openai/codex
#16839: three files, 53/24/17 reads, ~90% of all reads in one session).

The block is rendered ONCE per compaction, not per turn. A block rebuilt every turn sits in the
prompt prefix and changes under the KV cache, and a cached token is an order of magnitude cheaper
than an uncached one — the read a per-turn block saves is not worth the prefill it costs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

FILE = "reads.json"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _path(run_dir) -> Path:
    return Path(run_dir) / FILE


def _load(run_dir) -> Dict[str, dict]:
    try:
        return json.loads(_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_read(run_dir, rel: str, data: bytes, *, turn: Optional[int] = None) -> None:
    """Remember the bytes the model was shown for `rel`. A partial read is recorded against the
    WHOLE file's digest: what matters later is whether the file changed under it, and a window is
    the model's own choice of how much of it to look at."""
    reads = _load(run_dir)
    reads[rel] = {"sha": _digest(data), "turn": turn}
    try:
        _path(run_dir).write_text(json.dumps(reads), encoding="utf-8")
    except OSError:
        pass          # the block is an optimisation; a run dir that cannot take it still builds


def render(run_dir, root: Path) -> str:
    """The file-state block, or "" when the model has read nothing yet. One line per file it has
    read: unchanged since, or changed and how big it is now."""
    reads = _load(run_dir)
    if not reads or not root.exists():
        return ""
    unchanged, changed = [], []
    for rel, seen in sorted(reads.items()):
        p = root / rel
        if not p.is_file():
            continue
        try:
            live = _digest(p.read_bytes())
        except OSError:
            continue
        if live == seen.get("sha"):
            unchanged.append(rel)
        else:
            changed.append(f"{rel} ({p.stat().st_size} bytes)")
    if not unchanged and not changed:
        return ""
    out = []
    if unchanged:
        out.append("You have already read these files and they have NOT changed since — reading "
                   "them again returns exactly what you were shown:\n  " + "\n  ".join(unchanged))
    if changed:
        out.append("These have changed since you read them:\n  " + "\n  ".join(changed))
    return "\n\n".join(out)
