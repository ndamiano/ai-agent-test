"""Credit top-up seam — provider-agnostic purchase verification.

A payment provider (Stripe, Paddle, ...) POSTs a signed purchase event to the billing webhook.
The webhook does NOT trust the payload: it hands the raw bytes + headers to the active
`CreditProvider`, which verifies the provider's own signature and returns who to credit and how
much. An event that fails verification yields None and credits nothing.

The active provider is `UnconfiguredProvider`, which refuses every event: credits reach the ledger
only through the admin CLI. Wiring a concrete provider is a change to this seam alone — the ledger
and the webhook stay as they are.
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
    """Refuses every event, so no webhook payload can reach the ledger."""

    def verify(self, payload: bytes, headers: Mapping[str, str]) -> Optional[PurchaseEvent]:
        # TODO: implement a concrete provider (Stripe/Paddle) — verify the signature header
        # against the raw payload, parse the event, map its customer/price to (user_id, credits).
        # Refuses by RETURNING None, never by raising: the webhook is public (a provider can't
        # carry a user token), so a raise here is a 500 + traceback any caller can trigger.
        return None


_provider: CreditProvider = UnconfiguredProvider()


def get_provider() -> CreditProvider:
    return _provider
