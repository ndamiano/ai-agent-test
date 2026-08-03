"""Build pricing — the one swappable cost function.

A build costs a flat 1 credit today. Metered/tiered variants (by spec size, module set, or
the scaleout S3 usage hook) drop in HERE, leaving the ledger and the build gate untouched.
"""

from typing import Dict

# One credit buys this much GPU-execution budget (3 hours of 5090-seconds). Owner's pricing call
# 2026-08-03: $5 per credit, and every measured game to date spends far under this grant
# (median 386s, p90 ~2000s on the 2026-07-31 table).
SECONDS_PER_CREDIT = 10_800


def cost(spec: Dict) -> int:
    return 1
