"""What a credit buys, and what a build costs."""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

MICROS_PER_CREDIT = 3_000_000


def cost(spec: Dict) -> int:
    return 1


@dataclass(frozen=True)
class Package:
    id: str
    credits: int
    usd_cents: int

    def to_json(self):
        return {"id": self.id, "credits": self.credits, "usd_cents": self.usd_cents}


PACKAGES: Tuple[Package, ...] = (
    Package(id="one-credit", credits=1, usd_cents=500),
    Package(id="five-credits", credits=5, usd_cents=2500),
    Package(id="ten-credits", credits=10, usd_cents=5000),
)


def package_by_id(package_id: str) -> Optional[Package]:
    return next((p for p in PACKAGES if p.id == package_id), None)
