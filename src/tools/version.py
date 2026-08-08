"""Which Maestro built this.

A game is evidence about the pipeline that made it, and the pipeline changes several times a week —
so a build that does not record its own commit is a measurement with no independent variable. There
is no version number to use instead: nothing here is released, so the commit IS the version.

The DIRTY flag is half the value. A build from an edited tree is not reproducible from any commit,
and most local experiments run exactly that way; a grade traced to `abc1234-dirty` is a grade whose
pipeline no longer exists anywhere, which is worth knowing before drawing a line through it.

Resolved once per process — the tree does not change under a running control plane, and a build
should never pay a subprocess for this.
"""

import logging
import subprocess
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def maestro_rev() -> str:
    """`<short sha>` or `<short sha>-dirty`, and `unknown` where git cannot answer — a deploy from a
    tarball still has to build games."""
    try:
        sha = subprocess.run(["git", "-C", str(_REPO), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        if sha.returncode != 0:
            return "unknown"
        dirty = subprocess.run(["git", "-C", str(_REPO), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5)
        suffix = "-dirty" if dirty.returncode == 0 and dirty.stdout.strip() else ""
        return sha.stdout.strip() + suffix
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("could not resolve the maestro revision: %s", exc)
        return "unknown"
