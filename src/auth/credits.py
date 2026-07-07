"""Credit top-up seam — provider-agnostic purchase verification.

A payment provider (Stripe, Paddle, ...) POSTs a signed purchase event to the billing webhook.
The webhook does NOT trust the payload: it hands the raw bytes + headers to the active
`CreditProvider`, which verifies the provider's own signature and returns who to credit and how
much. An event that fails verification yields None and credits nothing.

The concrete provider verify is deliberately unbuilt (owner's call) — it's a TODO wired to this
seam, so the ledger and webhook never change when a real provider drops in. Until one is set,
`UnconfiguredProvider` refuses every event, so there is no unsigned path to credits.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class PurchaseEvent:
    """The credit grant a verified purchase authorizes."""
    user_id: str
    credits: int


class CreditProvider(ABC):
    @abstractmethod
    def verify(self, payload: bytes, headers: Mapping[str, str]) -> Optional[PurchaseEvent]:
        """Verify a raw webhook event against the provider's signature. Return the authorized
        grant, or None if it doesn't verify (nothing is credited)."""


class UnconfiguredProvider(CreditProvider):
    """Default until a real provider is wired — verifying anything is a TODO, so no event can
    reach the ledger."""

    def verify(self, payload: bytes, headers: Mapping[str, str]) -> Optional[PurchaseEvent]:
        # TODO: implement a concrete provider (Stripe/Paddle) — verify the signature header
        # against the raw payload, parse the event, map its customer/price to (user_id, credits).
        raise NotImplementedError(
            "no payment provider configured — wire a concrete CreditProvider (Stripe/Paddle)"
        )


_provider: CreditProvider = UnconfiguredProvider()


def get_provider() -> CreditProvider:
    return _provider


def set_provider(provider: CreditProvider) -> None:
    global _provider
    _provider = provider
