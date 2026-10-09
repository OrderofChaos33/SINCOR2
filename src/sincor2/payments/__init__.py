"""WP4: hardened payment package for SINCOR2.

Owner decisions enforced here:
- D3: USDC + AXM ONLY. Fiat/Stripe intents are rejected at construction.
- D4: Off-chain only. No on-chain broadcast paths in this package.
- D5: Financial ops under head orchestration with overlapping supervision.

Modules:
- intent: server-priced PaymentIntent (USDC|AXM only)
- idempotency: atomic claim of tx/event IDs before fulfillment
- killswitch: replay-safe kill-switch evaluation for payment intents
- reconciliation: off-chain assignment -> winner/price/beneficiary mapping
"""

from sincor2.payments.idempotency import (
    IdempotencyStore,
    claim_fulfillment,
    reset_claims,
)
from sincor2.payments.intent import (
    SUPPORTED_ASSETS,
    PaymentIntent,
    PaymentIntentError,
)
from sincor2.payments.killswitch import (
    PaymentKillSwitch,
    payment_kill_switch,
)
from sincor2.payments.reconciliation import (
    SettlementRecord,
    SettlementReconciler,
)

__all__ = [
    "SUPPORTED_ASSETS",
    "PaymentIntent",
    "PaymentIntentError",
    "IdempotencyStore",
    "claim_fulfillment",
    "reset_claims",
    "PaymentKillSwitch",
    "payment_kill_switch",
    "SettlementRecord",
    "SettlementReconciler",
]
