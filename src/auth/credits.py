"""Credit top-up seam — provider-agnostic purchases.

Two paths reach the ledger through the active `CreditProvider`:

- The storefront checkout: the billing router opens a purchase (`start_checkout`), the user pays
  on the provider's side, and completion asks the provider (`confirm_checkout`) before the ledger
  is credited — the provider's answer, never the client's word, is what authorizes the grant.
- The webhook: a provider POSTs a signed purchase event. The webhook does NOT trust the payload;
  it hands the raw bytes + headers to `verify`, which checks the provider's own signature and
  returns who to credit and how much. An event that fails verification yields None and credits
  nothing.

The active provider is `FakeInstantProvider` — swapping in a real processor (Stripe, Paddle, ...)
is implementing this interface here; the ledger, the router and the webhook stay as they are.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional

from auth.billing import Package


@dataclass(frozen=True)
class PurchaseEvent:
    """The credit grant a verified purchase authorizes."""
    user_id: str
    credits: int


class CreditProvider(ABC):
    @abstractmethod
    def start_checkout(self, purchase_id: str, package: Package) -> str:
        """Open a checkout with the provider for this purchase. Returns the provider's own
        reference for it (a Stripe checkout-session id, ...), which `confirm_checkout` takes."""

    @abstractmethod
    def confirm_checkout(self, provider_ref: str) -> bool:
        """Ask the provider whether this checkout was actually paid."""

    @abstractmethod
    def verify(self, payload: bytes, headers: Mapping[str, str]) -> Optional[PurchaseEvent]:
        """Verify a raw webhook event against the provider's signature. Return the authorized
        grant, or None if it doesn't verify (nothing is credited)."""


class FakeInstantProvider(CreditProvider):
    """FAKE — no payment processor, no money moves. Every checkout it opens confirms as paid.
    A real provider replaces this class: start_checkout creates the provider's session,
    confirm_checkout checks its paid status, verify authenticates its webhook."""

    def start_checkout(self, purchase_id: str, package: Package) -> str:
        return f"fake_{purchase_id}"

    def confirm_checkout(self, provider_ref: str) -> bool:
        return provider_ref.startswith("fake_")

    def verify(self, payload: bytes, headers: Mapping[str, str]) -> Optional[PurchaseEvent]:
        # No signature to check — refuses by RETURNING None, never by raising: the webhook is
        # public (a provider can't carry a user token), so a raise here is a 500 + traceback any
        # caller can trigger.
        return None


_provider: CreditProvider = FakeInstantProvider()


def get_provider() -> CreditProvider:
    return _provider
