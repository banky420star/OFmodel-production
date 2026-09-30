"""Simulated billing for the fan slice.

The money never leaves the machine, and the code says so at every level: the
only processor is `fake`, the ledger records what a real one would have
recorded, and the API response says "simulated" next to the balance.

What is *not* simulated is the accounting. `post_transaction` refuses to write
an unbalanced transaction, entitlements are unique per (user, product), and
`POST /wallet/topup` is idempotent on its key — so the machinery that would
handle real money is the machinery that is actually being exercised. Swapping in
a real processor means writing one class against `PaymentProcessor` and changing
`PAYMENT_PROCESSOR`; no call site moves.
"""

from app.billing.processor import ChargeResult, PaymentProcessor, get_processor

__all__ = ["ChargeResult", "PaymentProcessor", "get_processor"]
