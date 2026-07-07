"""Build pricing — the one swappable cost function.

A build costs a flat 1 credit today. Metered/tiered variants (by spec size, module set, or
the scaleout S3 usage hook) drop in HERE, leaving the ledger and the build gate untouched.
"""

from typing import Dict


def cost(spec: Dict) -> int:
    return 1
