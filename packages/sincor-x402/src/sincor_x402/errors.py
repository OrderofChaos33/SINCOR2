"""Fail-closed error types. Every payment-path error refuses to proceed
rather than guessing — modelled on the platform's x402_buyer.py safety notes.
"""
from __future__ import annotations


class SincorX402Error(Exception):
    """Base for all sincor_x402 errors."""


class PaymentCapExceeded(SincorX402Error):
    """A 402 demands more than the buyer's per-call cap. No signature produced."""


class UnsupportedPayment(SincorX402Error):
    """Unsupported asset, network, or scheme in a 402 challenge."""


class MalformedChallenge(SincorX402Error):
    """A 402 challenge / payment payload is missing required fields."""


class AmbiguousPayment(SincorX402Error):
    """Retry outcome was neither clear success nor clear rejection.

    Retrying blindly could double-pay, so the buyer stops and the
    idempotency journal keeps the instruction locked to its one payment.
    """


class IdempotencyConflict(SincorX402Error):
    """An instruction id was reused with a *different* request fingerprint."""


class VelocityCapExceeded(SincorX402Error):
    """Per-payee velocity cap (count or amount per window) would be exceeded.

    Raised BEFORE signing, so a capped attempt never becomes a payment.
    """


class AcceptsMismatch(SincorX402Error):
    """Client-claimed accepted terms do not exactly match the server's own
    retained accepts[] entry (scheme/network/amount/asset/payTo/timeout)."""


class VerificationFailed(SincorX402Error):
    """The delivered result failed verification against the committed spec."""


class EscrowTimeout(SincorX402Error):
    """The wrapped work did not finish inside the escrow timeout."""


class AllowanceInsufficient(SincorX402Error):
    """The buyer's ERC-20 allowance to the settlement contract is below the
    seal amount. Raised by the Hub engine's preflight BEFORE any transaction
    is built or signed: the SEAL signature alone moves nothing — the Hub
    pulls funds via transferFrom, so the buyer must approve first."""


class SettlementReverted(SincorX402Error):
    """The settlement contract reverted (or the settlement transaction
    failed on-chain). When the revert carries one of the Hub's custom
    errors, the decoded error name is included in the message."""
