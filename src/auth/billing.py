"""Build pricing — the one swappable cost function, and the storefront's package catalog.

A build costs a flat 1 credit today. Metered/tiered variants (by spec size, module set, or
the scaleout S3 usage hook) drop in HERE, leaving the ledger and the build gate untouched.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# One credit buys this much GPU-execution budget (3 hours of 5090-seconds). Owner's pricing call
# 2026-08-03: $5 per credit, and every measured game to date spends far under this grant
# (median 386s, p90 ~2000s on the 2026-07-31 table).
SECONDS_PER_CREDIT = 10_800

USD_CENTS_PER_CREDIT = 500


def cost(spec: Dict) -> int:
    return 1


@dataclass(frozen=True)
class Package:
    id: str
    credits: int
    usd_cents: int


# Flat $5/credit at every size — a bulk discount is not a decided policy.
PACKAGES: Tuple[Package, ...] = tuple(
    Package(id=str(n), credits=n, usd_cents=n * USD_CENTS_PER_CREDIT) for n in (1, 5, 10)
)


def package_by_id(package_id: str) -> Optional[Package]:
    return next((p for p in PACKAGES if p.id == package_id), None)
