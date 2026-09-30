"""A payment processor that charges nothing.

Deterministic on purpose: the same input always produces the same outcome, so a
test that unlocks a product twice is testing the idempotency logic rather than
the weather. Declines are available on demand (`force_decline`) so the failure
path is exercisable without waiting for a real one.
"""

from __future__ import annotations

import uuid

from app.billing.processor import ChargeResult, PaymentProcessor


class FakePaymentProcessor(PaymentProcessor):
    name = "fake"

    def __init__(self, force_decline: bool = False):
        self.force_decline = force_decline

    async def charge(
        self,
        *,
        amount_minor: int,
        currency: str,
        user_id: str,
        kind: str,
        reference: str = "",
    ) -> ChargeResult:
        if amount_minor <= 0:
            return ChargeResult(
                ok=False,
                failure_reason="amount must be positive",
                detail={"amount_minor": amount_minor},
            )
        if self.force_decline:
            return ChargeResult(ok=False, failure_reason="declined (forced)")

        return ChargeResult(
            ok=True,
            processor_ref=f"fake_{uuid.uuid4().hex[:16]}",
            detail={
                "simulated": True,
                "amount_minor": amount_minor,
                "currency": currency,
                "kind": kind,
                "reference": reference,
            },
        )

    async def refund(
        self, *, processor_ref: str, amount_minor: int, currency: str = "USD"
    ) -> ChargeResult:
        if amount_minor <= 0:
            return ChargeResult(ok=False, failure_reason="amount must be positive")
        return ChargeResult(
            ok=True,
            processor_ref=f"fakerefund_{uuid.uuid4().hex[:16]}",
            detail={"simulated": True, "refunded": processor_ref},
        )

    async def health_check(self) -> tuple[bool, str]:
        return True, "Simulated processor — no real money moves, by design"
