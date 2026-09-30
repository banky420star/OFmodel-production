"""The payment processor boundary.

This ABC is the seam that keeps a real, PCI-scoped processor swappable later
without touching a single call site. Everything above it — the ledger, the
entitlements, the routes — deals in `ChargeResult`, never in a vendor SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChargeResult:
    ok: bool
    processor_ref: str = ""
    failure_reason: str = ""
    detail: dict = field(default_factory=dict)


class PaymentProcessor(ABC):
    """Move (simulated) money on behalf of a user.

    Implementations must be idempotent on `reference` where the upstream allows
    it, and must never raise for an ordinary decline — a declined card is a
    `ChargeResult(ok=False)`, not an exception. Exceptions are reserved for the
    processor being unreachable, which the caller treats differently.
    """

    name: str = "abstract"

    @abstractmethod
    async def charge(
        self,
        *,
        amount_minor: int,
        currency: str,
        user_id: str,
        kind: str,
        reference: str = "",
    ) -> ChargeResult:
        ...

    @abstractmethod
    async def refund(
        self, *, processor_ref: str, amount_minor: int, currency: str = "USD"
    ) -> ChargeResult:
        ...

    @abstractmethod
    async def health_check(self) -> tuple[bool, str]:
        """(ok, human-readable detail) — surfaced on /system/providers."""
        ...


def get_processor() -> PaymentProcessor:
    """The configured processor.

    There is exactly one implementation, and naming an unknown value is a hard
    error rather than a quiet fallback to the fake — a production deployment
    that asked for a real processor must never silently get simulated money.
    """
    from app.config import get_settings

    configured = (get_settings().PAYMENT_PROCESSOR or "fake").lower()
    if configured == "fake":
        from app.billing.fake_processor import FakePaymentProcessor
        return FakePaymentProcessor()
    raise ValueError(
        f"Unknown PAYMENT_PROCESSOR '{configured}'. Only 'fake' exists — "
        "implement PaymentProcessor for a real one rather than degrading to it."
    )
