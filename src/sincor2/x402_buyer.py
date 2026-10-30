"""x402 BUYER client: pay other x402 services (we are the buyer).

This is the buyer-side complement to x402_payments.py (seller side).
Standard x402 v2 flow:
  1. GET/POST resource without payment -> 402 PAYMENT-REQUIRED
  2. Parse payment details from 402 response
  3. Sign EIP-712 transferWithAuthorization for USDC via injected signer
  4. Retry with X-PAYMENT (PAYMENT-SIGNATURE) header
  5. Server verifies, returns data

SAFETY (fail-closed):
- signer_fn is INJECTED. This module NEVER sees, stores, or derives
  private keys. Key custody stays with the caller (founder-controlled).
- max_payment_usd cap per call (default $0.10). Any 402 demanding more
  raises PaymentCapExceeded and NO signature is produced.
- No auto-retry on ambiguous payments: if the retry response is not a
  clear 200 or 402, raise AmbiguousPayment instead of retrying blindly.
  A caller-driven retry of the same logical payment reuses the in-flight
  signed authorization (W-75 journal) — never a fresh nonce — so it cannot
  double-pay.
- Paid-leg transport errors (timeouts, dropped responses) AFTER the
  signed payment has been sent — or at any point where delivery cannot
  be disproven — raise AmbiguousPayment, never a retryable error: the
  payment MAY have settled server-side. Callers must reconcile (check
  payment status / reuse the settled authorization) before any retry.
  Only a transport failure that PROVES no bytes left this machine
  (DNS failure, connection refused, malformed request) raises
  PaymentTransportError, which is safe to retry.
- validBefore must be in the future and within 1 hour of now; otherwise
  refuse to sign (stale or absurd expiry).
- Only USDC on Base (eip155:8453) is supported. Any other asset/network
  in the 402 raises UnsupportedPayment.

Tested against MACH Base (https://api.mach.gallery) x402 v2 flow.
No real network calls in tests — everything is mocked.

DESIGN CONSTRAINT (dev-watch item 263 / x402 #3866, OPEN):
Never run wrapFetchWithPayment — or any x402 buyer client — with defaults.
The official client signs EIP-3009 for ANY server-declared accepts[].amount
(a x10 overcharge was captured 3/3: no price-expectation check anywhere in
the loop). Every paid call through THIS client must wire the pre-payment
policy hook: ``X402Buyer(..., expected_price_usd=<quoted price>,
price_tolerance_bps=<tolerance>)``. An adversarial 402 declaring an amount
outside expected*(1+tolerance) is refused with PricePolicyViolation BEFORE
any signature is produced. The absolute ``max_payment_usd`` cap is NOT a
substitute — a x10-inflated quote can still fit under the cap.

UPSTREAM DEPENDENCY PIN (dev-watch item 261 / x402 PR #3795):
Any integration using the official JS packages MUST pin @x402/core and
@x402/express to >= the first release containing PR #3795 (merged
2026-10-22; the changeset marked both packages for a patch release —
check the x402 changelog for the exact patch version, published after
2026-10-22). Before #3795 the 402 JSON body was a literal {} and the
rejection reason travelled ONLY in the base64 PAYMENT-REQUIRED header;
after #3795 the body is {"error": <reason>} and the @x402/express
settlement catch-all answers {"error": "settlement_failed"}. This client
reads the refusal reason from the BODY (the guaranteed channel) and
surfaces it as a typed PaymentRefusedError.

FACILITATOR ERROR SHAPES (dev-watch items 309 + 310 / x402 Go #3937,
#3778): some facilitators answer verification failures with a bare HTTP
500, dropping the typed error (#3937), and answer settlement_pending
with a 500, dropping the broadcast hash (#3778). This client therefore
classifies EVERY paid-leg failure by the error REASON parsed from the
response BODY — never by HTTP status alone
(:func:`classify_paid_leg_failure`): each failure class maps to exactly
one client action (:class:`FailureAction`) — deterministically-invalid
payments (bad signature, insufficient balance) ABORT and are never
retried as if transient; an expired authorization is CORRECT_AND_RETRY
(discard the stale bytes, re-sign a fresh validity window over the same
nonce, resend exactly once); settlement failures, pending settlements,
and anything unrecognized HOLD for reconciliation. A bare 500 is
UNKNOWN, never "invalid". A non-2xx body is never discarded: when it
carries a settlement_pending reason with a broadcast hash, the hash is
recovered and reconciled onchain via the injected
``settlement_reconciler`` BEFORE any retry decision, and no second
payment is ever submitted for a pending settlement.
"""

from __future__ import annotations

import enum
import json
import secrets
import socket
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

# Base mainnet USDC (EIP-3009 transferWithAuthorization)
USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6
BASE_CHAIN_ID = 8453
BASE_CHAIN_REF = "eip155:8453"

# EIP-3009 domain for USDC on Base
_USDC_DOMAIN = {
    "name": "USD Coin",
    "version": "2",
    "chainId": BASE_CHAIN_ID,
    "verifyingContract": USDC_BASE_ADDRESS,
}

_TRANSFER_WITH_AUTH_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}


# ---------------------------------------------------------------------------
# Errors (all fail-closed)
# ---------------------------------------------------------------------------


class X402BuyerError(Exception):
    """Base for all buyer errors."""


class PaymentCapExceeded(X402BuyerError):
    """402 demands more than the per-call cap. No signature produced."""


class UnsupportedPayment(X402BuyerError):
    """402 asks for an unsupported asset, network, or scheme."""


class MalformedChallenge(X402BuyerError):
    """402 response is missing required payment fields."""


class AmbiguousPayment(X402BuyerError):
    """Payment state is UNKNOWN — retrying blindly could double-pay.

    Raised when the paid retry's response is neither a clear success nor
    a clear 402, AND when the paid leg suffers a transport error (timeout,
    dropped response, connection reset) at or after the point where the
    signed payment bytes may have left this machine. The payment MAY have
    settled server-side. The caller must reconcile (check payment status,
    reuse the settled authorization) before any retry. Human must
    investigate.
    """


class PaymentTransportError(X402BuyerError):
    """Transport failed BEFORE any payment bytes left this machine.

    Safe to retry: either no signature had been produced yet (unpaid leg),
    or the transport proved the connection was never established
    (DNS failure, connection refused, malformed request). No payment
    could have been made or settled.
    """


class StaleChallenge(X402BuyerError):
    """validBefore is in the past or unreasonably far in the future."""


class PricePolicyViolation(X402BuyerError):
    """402 demands a price outside the declared expectation.

    Raised by the pre-payment price policy (expected price + tolerance)
    BEFORE any signature is produced. Distinct from PaymentCapExceeded:
    the cap is an ABSOLUTE per-call ceiling, while the price policy is a
    RELATIVE expectation — a x10-inflated quote that still fits under the
    cap is refused here (x402 #3866 / scenario S-BS-06).
    """


class PaymentRefusedError(AmbiguousPayment):
    """Server refused AFTER the signed payment was sent (402 on the paid leg).

    A subclass of AmbiguousPayment: the payment MAY have settled
    server-side, so the no-blind-retry contract is preserved. The server's
    rejection reason — carried in the 402 JSON BODY since x402 PR #3795
    (``{"error": ...}``), not only in the base64 PAYMENT-REQUIRED header —
    is exposed as ``.reason`` so callers can distinguish an unfunded wallet
    from a bad signature from a settlement failure instead of treating
    every refusal as identical.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class SigningValidationError(X402BuyerError):
    """Typed data about to be signed does not match the checked requirements.

    Dev-watch item 301 (Cyfrin 1inch clear-signing audit): descriptors are
    untrusted display metadata. Before any signature is produced, the exact
    typed-data payload handed to the signer is validated field-by-field
    against the checked :class:`PaymentRequirements`, and any summary shown
    to a human/agent must be generated from that exact payload. A mismatch
    (recipient, expiry/validBefore, spend value, nonce, domain), an
    unexpected/extra field, or a summary that differs from the payload is
    refused here — no signature is produced.
    """


class InsufficientFundsError(PaymentRefusedError):
    """Paid-leg 402 with body error ``insufficient_funds`` (unfunded wallet)."""


class InvalidSignatureError(PaymentRefusedError):
    """Paid-leg 402 with body error ``invalid_signature`` (bad signature)."""


class SettlementFailedError(PaymentRefusedError):
    """Paid-leg 402 with body error ``settlement_failed`` (settle catch-all)."""


class ExpiredAuthorizationError(PaymentRefusedError):
    """Facilitator rejected the authorization as EXPIRED (items 309/310).

    The one deterministically-invalid class whose correction is sound:
    the token contract refuses transferWithAuthorization after
    validBefore, so the rejected bytes can never settle. The client
    discards them, re-signs a FRESH validity window over the SAME nonce /
    amount / recipient, and resends exactly once (CORRECT_AND_RETRY).
    """


class SettlementPendingError(PaymentRefusedError):
    """Facilitator reports the settlement PENDING, with its broadcast hash.

    Raised for a ``settlement_pending`` reason on ANY paid-leg status
    (the x402 Go facilitator answers it with a 500 — item 310). Carries
    the recovered broadcast ``tx_hash`` and the result of the injected
    onchain ``settlement_reconciler`` (None when no reconciler is wired
    or no hash was present). The client NEVER submits a second payment
    for a pending settlement: the action is HOLD.
    """

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        tx_hash: Optional[str] = None,
        reconciliation: Any = None,
    ) -> None:
        super().__init__(reason, message)
        self.tx_hash = tx_hash
        self.reconciliation = reconciliation


class UnknownFacilitatorFailure(AmbiguousPayment):
    """Paid-leg failure with no parseable reason (e.g. a bare 500).

    Item 309: an unknown failure is UNKNOWN — it is never classified as
    deterministically invalid (it is NOT a PaymentRefusedError subclass
    instance for a known reason) and never retried as if transient. The
    payment may have settled; the action is HOLD for reconciliation.
    """


# URLError reasons that prove the TCP connection was never established,
# hence no request bytes (and no payment) could have left this machine.
# Anything else (timeouts, resets, TLS errors, mid-stream failures) is
# treated as possibly-post-send: ambiguous on the paid leg.
_PROVABLY_PRE_SEND_REASONS = (ConnectionRefusedError, socket.gaierror)


# ---------------------------------------------------------------------------
# Pre-payment price policy (dev-watch item 263 / scenario S-BS-06)
# ---------------------------------------------------------------------------

#: Default price tolerance: 5% over the expected price.
DEFAULT_PRICE_TOLERANCE_BPS = 500


def check_price_expectation(
    expected_price_usd: float,
    req: "PaymentRequirements",
    *,
    tolerance_bps: int = DEFAULT_PRICE_TOLERANCE_BPS,
) -> None:
    """Pure pre-payment price policy: expected price + tolerance -> accept/reject.

    Raises :class:`PricePolicyViolation` when ``req.amount_usd`` exceeds
    ``expected_price_usd * (1 + tolerance_bps/10000)``. Accepts when the
    402 demand is at or under the expected price or within tolerance.
    Raises ValueError on a non-positive expected price or negative
    tolerance. Pure: no I/O, no signing — wire it (or construct
    ``X402Buyer(expected_price_usd=...)``) anywhere a client is about to
    sign for a server-declared amount.
    """
    if expected_price_usd is None or expected_price_usd <= 0:
        raise ValueError("expected_price_usd must be positive")
    if tolerance_bps < 0:
        raise ValueError("tolerance_bps must be non-negative")
    ceiling = expected_price_usd * (1 + tolerance_bps / 10000)
    if req.amount_usd > ceiling:
        raise PricePolicyViolation(
            f"402 demands ${req.amount_usd:.4f} > expected "
            f"${expected_price_usd:.4f} + {tolerance_bps}bps tolerance "
            f"(ceiling ${ceiling:.4f}); no signature produced"
        )


# ---------------------------------------------------------------------------
# Paid-leg 402 refusal classification (dev-watch item 261 / x402 PR #3795)
# ---------------------------------------------------------------------------

#: Body-error strings (normalized: lowercased, spaces/hyphens -> _) that
#: map to typed refusal errors. Anything else non-empty becomes a plain
#: PaymentRefusedError with the raw reason preserved; an empty/absent
#: error (pre-#3795 servers) stays a plain AmbiguousPayment.
_REFUSAL_REASON_MAP: dict[str, type[PaymentRefusedError]] = {
    "insufficient_funds": InsufficientFundsError,
    "insufficient_amount": InsufficientFundsError,
    "insufficient_balance": InsufficientFundsError,
    "invalid_signature": InvalidSignatureError,
    "signature_invalid": InvalidSignatureError,
    "bad_signature": InvalidSignatureError,
    "settlement_failed": SettlementFailedError,
    "settle_failed": SettlementFailedError,
}


def _normalize_refusal_reason(raw: object) -> str:
    return (
        str(raw or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def classify_retry_refusal(raw_body: bytes) -> AmbiguousPayment:
    """Build the typed error for a 402 received AFTER the signed payment.

    Parses the 402 JSON BODY (the guaranteed channel since x402 PR #3795)
    for ``error`` and returns a typed :class:`PaymentRefusedError`
    (which IS an AmbiguousPayment — the payment may have settled, so the
    no-blind-retry contract holds) when a reason is present, else a plain
    AmbiguousPayment for pre-patch servers that still answer ``{}``.
    """
    reason = ""
    try:
        parsed = json.loads(raw_body or b"{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = {}
    if isinstance(parsed, dict):
        reason = _normalize_refusal_reason(parsed.get("error"))
    message = (
        "server returned 402 after signed payment; refusing to retry "
        "(could double-pay). Investigate manually."
    )
    if not reason:
        return AmbiguousPayment(message)
    cls = _REFUSAL_REASON_MAP.get(reason, PaymentRefusedError)
    return cls(
        reason,
        message.replace(
            "after signed payment;",
            f"after signed payment (reason: {reason});",
        ),
    )


# ---------------------------------------------------------------------------
# Paid-leg failure classification (dev-watch items 309 + 310)
# ---------------------------------------------------------------------------


class FailureAction(str, enum.Enum):
    """The exactly-one client action a paid-leg failure class maps to."""

    CORRECT_AND_RETRY = "correct_and_retry"
    ABORT = "abort"
    HOLD = "hold"


#: Failure classes (FacilitatorFailure.klass values).
FAILURE_INVALID_SIGNATURE = "invalid_signature"
FAILURE_INSUFFICIENT_FUNDS = "insufficient_funds"
FAILURE_EXPIRED_AUTHORIZATION = "expired_authorization"
FAILURE_SETTLEMENT_FAILED = "settlement_failed"
FAILURE_SETTLEMENT_PENDING = "settlement_pending"
FAILURE_UNKNOWN = "unknown"

_EXPIRED_REASONS = frozenset(
    {
        "authorization_expired",
        "expired_authorization",
        "expired",
        "payment_expired",
        "valid_before_expired",
        "authorization_expiry",
    }
)
_SETTLEMENT_PENDING_REASONS = frozenset(
    {
        "settlement_pending",
        "settle_pending",
        "settlement_in_progress",
        "settlement_queued",
    }
)

#: Body keys under which a facilitator may carry the broadcast hash of a
#: pending settlement (item 310: the hash must be recovered, never dropped).
_TX_HASH_KEYS = (
    "txHash",
    "tx_hash",
    "transactionHash",
    "transaction_hash",
    "settlementTxHash",
    "hash",
)


def _extract_tx_hash(parsed: dict[str, Any]) -> Optional[str]:
    """Recover a 0x-prefixed 32-byte broadcast hash from a response body.

    Checks the top level and a nested ``settlement`` object. Returns the
    lowercased hash, or None when no candidate is a well-formed hash —
    a malformed value is never trusted as a reconciliation target.
    """
    candidates: list[Any] = [parsed.get(k) for k in _TX_HASH_KEYS]
    nested = parsed.get("settlement")
    if isinstance(nested, dict):
        candidates.extend(nested.get(k) for k in _TX_HASH_KEYS)
    for cand in candidates:
        if isinstance(cand, str):
            h = cand.strip().lower()
            if (
                len(h) == 66
                and h.startswith("0x")
                and all(c in "0123456789abcdef" for c in h[2:])
            ):
                return h
    return None


@dataclass(frozen=True)
class FacilitatorFailure:
    """One classified paid-leg failure: class + exactly one action."""

    status: int
    reason: str  # normalized body reason ("" when absent/unparseable)
    klass: str  # one of the FAILURE_* constants
    action: FailureAction
    tx_hash: Optional[str] = None  # recovered broadcast hash, if carried


def classify_paid_leg_failure(status: int, raw_body: bytes) -> FacilitatorFailure:
    """Classify a paid-leg failure by its BODY REASON, never status alone.

    Item 309 (x402 Go #3937): a facilitator may answer a verification
    failure with a bare HTTP 500, dropping the typed error into the body
    or out of it entirely. The reason is therefore parsed from the body
    regardless of status: a 500 carrying ``invalid_signature`` IS an
    invalid signature (ABORT), and a bare 500 with no parseable reason is
    UNKNOWN (HOLD) — never "invalid", never transient.

    Item 310 (x402 Go #3778): the body is never discarded — a
    ``settlement_pending`` reason and its broadcast hash are recovered
    from ANY non-2xx body so the caller can reconcile the hash onchain
    before any retry.
    """
    parsed: dict[str, Any] = {}
    try:
        candidate = json.loads(raw_body or b"{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        candidate = {}
    if isinstance(candidate, dict):
        parsed = candidate
    reason = ""
    err_field: Any = parsed.get("error")
    if isinstance(err_field, dict):
        reason = _normalize_refusal_reason(
            err_field.get("code") or err_field.get("reason") or err_field.get("message")
        )
    elif err_field:
        reason = _normalize_refusal_reason(err_field)
    if not reason:
        reason = _normalize_refusal_reason(parsed.get("reason") or parsed.get("code"))
    tx_hash = _extract_tx_hash(parsed)

    if reason in _REFUSAL_REASON_MAP and _REFUSAL_REASON_MAP[reason] is InvalidSignatureError:
        klass, action = FAILURE_INVALID_SIGNATURE, FailureAction.ABORT
    elif reason in _REFUSAL_REASON_MAP and _REFUSAL_REASON_MAP[reason] is InsufficientFundsError:
        klass, action = FAILURE_INSUFFICIENT_FUNDS, FailureAction.ABORT
    elif reason in _EXPIRED_REASONS:
        klass, action = FAILURE_EXPIRED_AUTHORIZATION, FailureAction.CORRECT_AND_RETRY
    elif reason in _REFUSAL_REASON_MAP and _REFUSAL_REASON_MAP[reason] is SettlementFailedError:
        klass, action = FAILURE_SETTLEMENT_FAILED, FailureAction.HOLD
    elif reason in _SETTLEMENT_PENDING_REASONS:
        klass, action = FAILURE_SETTLEMENT_PENDING, FailureAction.HOLD
    else:
        klass, action = FAILURE_UNKNOWN, FailureAction.HOLD
    return FacilitatorFailure(
        status=status, reason=reason, klass=klass, action=action, tx_hash=tx_hash
    )


def failure_action_for_error(exc: BaseException) -> FailureAction:
    """Map a raised buyer error to the matrix action for its class.

    Deterministically-invalid refusals (bad signature, insufficient
    funds) ABORT — retrying them as if transient is the item-309 bug.
    Expired authorizations CORRECT_AND_RETRY. Everything else ambiguous
    (settlement failed/pending, unrecognized refusals, plain
    AmbiguousPayment) HOLDs for reconciliation.
    """
    if isinstance(exc, (InvalidSignatureError, InsufficientFundsError)):
        return FailureAction.ABORT
    if isinstance(exc, ExpiredAuthorizationError):
        return FailureAction.CORRECT_AND_RETRY
    if isinstance(exc, AmbiguousPayment):
        return FailureAction.HOLD
    raise TypeError(f"not a payment failure error: {type(exc).__name__}")


def _error_for_failure(failure: FacilitatorFailure) -> AmbiguousPayment:
    """Build the typed error for a classified non-402 paid-leg failure."""
    detail = (
        f"paid-leg facilitator failure: HTTP {failure.status}, "
        f"reason {failure.reason or 'unknown'} (class {failure.klass}, "
        f"action {failure.action.value})"
    )
    if failure.klass == FAILURE_INVALID_SIGNATURE:
        return InvalidSignatureError(failure.reason, detail)
    if failure.klass == FAILURE_INSUFFICIENT_FUNDS:
        return InsufficientFundsError(failure.reason, detail)
    if failure.klass == FAILURE_SETTLEMENT_FAILED:
        return SettlementFailedError(failure.reason, detail)
    if failure.klass == FAILURE_EXPIRED_AUTHORIZATION:
        return ExpiredAuthorizationError(failure.reason, detail)
    return UnknownFacilitatorFailure(
        f"ambiguous retry status {failure.status}; {detail}; refusing to "
        "retry blindly — the failure reason is unknown, which is never "
        "treated as invalid; reconcile payment status first"
    )


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentRequirements:
    """Parsed from a 402 PAYMENT-REQUIRED response."""

    amount_atomic: int
    amount_usd: float
    asset: str  # token contract address
    network: str  # e.g. "eip155:8453"
    pay_to: str
    nonce: str  # hex bytes32
    valid_after: int  # unix timestamp
    valid_before: int  # unix timestamp
    resource: str = ""
    description: str = ""


@dataclass(frozen=True)
class SignedPayment:
    """EIP-712 signed transferWithAuthorization, ready for X-PAYMENT header."""

    signature: str  # 0x-prefixed hex
    authorization: dict[str, Any]  # the signed message fields


# Type: takes EIP-712 typed data dict, returns 0x-prefixed signature hex.
# The caller owns the key; this module never sees it.
SignerFn = Callable[[dict[str, Any]], str]


# ---------------------------------------------------------------------------
# Validate-before-signing (dev-watch item 301)
# ---------------------------------------------------------------------------

#: The exact TransferWithAuthorization message fields that may be signed.
#: Anything else in the message is a hidden field and is refused.
_EXPECTED_MESSAGE_FIELDS = frozenset(
    {"from", "to", "value", "validAfter", "validBefore", "nonce"}
)
_EXPECTED_TYPED_DATA_FIELDS = frozenset(
    {"types", "domain", "primaryType", "message"}
)


def signing_summary(typed_data: dict[str, Any]) -> dict[str, Any]:
    """Canonical summary generated FROM the exact typed_data being signed.

    This is the only summary that may be shown to a signer/human/agent for
    a TransferWithAuthorization. It is copied field-by-field from
    ``typed_data`` itself — never from a separate descriptor, challenge
    body, or third-party attestation (item 301: descriptors are untrusted
    display metadata, never verification).
    """
    if not isinstance(typed_data, dict):
        raise SigningValidationError("typed_data must be a dict")
    message = typed_data.get("message")
    domain = typed_data.get("domain")
    if not isinstance(message, dict) or not isinstance(domain, dict):
        raise SigningValidationError(
            "typed_data must carry dict 'message' and 'domain'"
        )
    return {
        "primaryType": typed_data.get("primaryType"),
        "domain": dict(domain),
        "message": dict(message),
    }


def validate_typed_data_before_signing(
    typed_data: dict[str, Any],
    req: "PaymentRequirements",
    *,
    wallet_address: str,
    expected_valid_before: int | None = None,
    summary: dict[str, Any] | None = None,
    descriptor: Any = None,
) -> dict[str, Any]:
    """Validate the exact typed_data about to be signed against ``req``.

    Checks, fail-closed (raises :class:`SigningValidationError`):

    - top-level shape is exactly {types, domain, primaryType, message};
    - primaryType is TransferWithAuthorization and ``types``/``domain``
      are exactly the expected USDC-on-Base EIP-712 definitions (no
      hidden domain field, no substituted verifying contract/chain);
    - the message carries exactly the six expected fields — any extra
      (hidden) field or any missing field is refused;
    - ``from`` == the buyer wallet, ``to`` == ``req.pay_to`` (recipient),
      ``value`` == ``req.amount_atomic`` (spend authorization),
      ``validAfter`` == the requirement/default actually used,
      ``validBefore`` == ``expected_valid_before`` when given (else
      ``req.valid_before`` when the challenge set one) (expiry), and
      ``nonce`` == ``req.nonce``;
    - ``validAfter``/``validBefore`` are ints by type (bool/float/str
      refused, including equal-valued floats), and when no
      ``expected_valid_before`` is supplied the validator still checks
      expiry sanity itself: validBefore positive, greater than
      validAfter, and in the future (round-2 hardening, finding B3);
    - when ``summary`` is supplied (the summary shown to the signer), it
      must equal the canonical summary generated from this exact
      typed_data — a summary whose expiry or recipient differs from the
      payload is refused, not signed.

    ``descriptor`` is accepted only to make the trust rule explicit: it is
    third-party display metadata and is NEVER consulted for verification;
    mutating it cannot change what is validated or signed.

    Returns the canonical summary (from :func:`signing_summary`).
    """
    del descriptor  # untrusted display metadata: never verification input
    if not isinstance(typed_data, dict):
        raise SigningValidationError("typed_data must be a dict")
    extra_top = set(typed_data) - _EXPECTED_TYPED_DATA_FIELDS
    missing_top = _EXPECTED_TYPED_DATA_FIELDS - set(typed_data)
    if extra_top or missing_top:
        raise SigningValidationError(
            "typed_data shape mismatch: "
            f"extra={sorted(extra_top)} missing={sorted(missing_top)}"
        )
    if typed_data.get("primaryType") != "TransferWithAuthorization":
        raise SigningValidationError(
            f"unexpected primaryType: {typed_data.get('primaryType')!r}"
        )
    if typed_data.get("domain") != _USDC_DOMAIN:
        raise SigningValidationError(
            "typed_data domain does not match the expected USDC-on-Base "
            f"domain: {typed_data.get('domain')!r}"
        )
    if typed_data.get("types") != _TRANSFER_WITH_AUTH_TYPES:
        raise SigningValidationError(
            "typed_data types do not match the expected "
            "TransferWithAuthorization type definition"
        )
    message = typed_data.get("message")
    if not isinstance(message, dict):
        raise SigningValidationError("typed_data message must be a dict")
    extra = set(message) - _EXPECTED_MESSAGE_FIELDS
    missing = _EXPECTED_MESSAGE_FIELDS - set(message)
    if extra or missing:
        raise SigningValidationError(
            "typed_data message field mismatch (hidden/missing fields "
            f"refused): extra={sorted(extra)} missing={sorted(missing)}"
        )

    def _addr(value: Any) -> str:
        return str(value).lower()

    if _addr(message["from"]) != _addr(wallet_address):
        raise SigningValidationError(
            f"message 'from' {message['from']!r} != wallet {wallet_address!r}"
        )
    if _addr(message["to"]) != _addr(req.pay_to):
        raise SigningValidationError(
            f"message 'to' (recipient) {message['to']!r} != checked "
            f"pay_to {req.pay_to!r}"
        )
    value = message["value"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SigningValidationError(
            f"message 'value' must be an int, got {value!r}"
        )
    if value != req.amount_atomic:
        raise SigningValidationError(
            f"message 'value' (spend) {value!r} != checked "
            f"amount_atomic {req.amount_atomic!r}"
        )
    # Round-2 hardening (item 301, finding B3): the expiry window fields
    # must be ints by TYPE, not just by equality — in Python ``False == 0``,
    # ``0.0 == 0`` and ``float(validBefore) == validBefore``, so an
    # equality-only check lets equal-valued bools/floats (and strings are
    # refused here too) through the exported validator.
    for _field in ("validAfter", "validBefore"):
        _value = message[_field]
        if isinstance(_value, bool) or not isinstance(_value, int):
            raise SigningValidationError(
                f"message '{_field}' must be an int, got {_value!r}"
            )
    expected_valid_after = req.valid_after or 0
    if message["validAfter"] != expected_valid_after:
        raise SigningValidationError(
            f"message 'validAfter' {message['validAfter']!r} != checked "
            f"{expected_valid_after!r}"
        )
    if expected_valid_before is not None:
        expected_expiry: int | None = expected_valid_before
    elif req.valid_before:
        expected_expiry = req.valid_before
    else:
        expected_expiry = None
    if expected_expiry is not None and message["validBefore"] != expected_expiry:
        raise SigningValidationError(
            f"message 'validBefore' (expiry) {message['validBefore']!r} "
            f"!= checked {expected_expiry!r}"
        )
    if expected_valid_before is None:
        # B3: with no caller-supplied expectation the validator must still
        # check expiry sanity itself instead of skipping the check:
        # validBefore is a positive int (type-checked above), strictly
        # after validAfter, and in the future.
        valid_before = message["validBefore"]
        valid_after = message["validAfter"]
        if (
            valid_before <= 0
            or valid_before <= valid_after
            or valid_before <= int(time.time())
        ):
            raise SigningValidationError(
                f"message 'validBefore' (expiry) {valid_before!r} fails "
                "expiry sanity with no expected_valid_before supplied: "
                "must be positive, greater than validAfter "
                f"({valid_after!r}), and in the future"
            )
    if message["nonce"] != req.nonce:
        raise SigningValidationError(
            f"message 'nonce' {message['nonce']!r} != checked {req.nonce!r}"
        )
    canonical = signing_summary(typed_data)
    if summary is not None and summary != canonical:
        raise SigningValidationError(
            "summary shown to the signer differs from the exact typed_data "
            "being signed (recipient/expiry/spend mismatch refused): "
            f"summary={summary!r} payload_summary={canonical!r}"
        )
    return canonical


# ---------------------------------------------------------------------------
# Buyer
# ---------------------------------------------------------------------------


class X402Buyer:
    """Pays x402 services. Keys never enter this class."""

    def __init__(
        self,
        wallet_address: str,
        signer_fn: SignerFn,
        *,
        max_payment_usd: float = 0.10,
        timeout_seconds: int = 30,
        expected_price_usd: float | None = None,
        price_tolerance_bps: int = DEFAULT_PRICE_TOLERANCE_BPS,
        settlement_reconciler: Optional[Callable[[str], Any]] = None,
    ) -> None:
        if not wallet_address or not wallet_address.startswith("0x"):
            raise ValueError("wallet_address must be a 0x address")
        if max_payment_usd <= 0:
            raise ValueError("max_payment_usd must be positive")
        if expected_price_usd is not None and expected_price_usd <= 0:
            raise ValueError("expected_price_usd must be positive")
        if price_tolerance_bps < 0:
            raise ValueError("price_tolerance_bps must be non-negative")
        self.wallet_address = wallet_address
        self._signer_fn = signer_fn
        self.max_payment_usd = max_payment_usd
        self.timeout_seconds = timeout_seconds
        # Pre-payment price policy (item 263 / S-BS-06): the expected price
        # for this call site plus tolerance. When set, any 402 declaring a
        # higher amount is refused BEFORE signing. None = policy disabled
        # (cap only) — integrators must always wire this; see the module
        # docstring design constraint.
        self.expected_price_usd = expected_price_usd
        self.price_tolerance_bps = price_tolerance_bps
        # Item 310: onchain reconciler for a pending settlement's
        # broadcast hash — takes the tx hash, returns its chain state.
        # Injected (never constructed here) so tests and callers own the
        # RPC surface. Consulted BEFORE any retry decision; this client
        # never submits a second payment for a pending settlement.
        self._settlement_reconciler = settlement_reconciler
        # W-75: payment-attempt journal.  Maps a resource URL to the
        # (requirements, signed authorization) of the last attempt whose
        # outcome was ambiguous (or transport-failed after signing).  A
        # retry of the SAME logical payment reuses the in-flight signed
        # bytes instead of minting a fresh nonce — the onchain nonce can
        # only be spent once, so reuse can never double-pay, while a
        # fresh signature would create a second spendable authorization.
        self._inflight: dict[str, tuple[PaymentRequirements, SignedPayment]] = {}
        self._journal_lock = threading.Lock()

    # -- 402 parsing ----------------------------------------------------

    @staticmethod
    def parse_402(body: dict[str, Any]) -> PaymentRequirements:
        """Parse x402 v2 402 response body into PaymentRequirements.

        Accepts both our seller format (nested "payment" dict) and the
        generic x402 v2 flat format. Raises MalformedChallenge on missing
        fields, UnsupportedPayment on wrong asset/network.
        """
        # Unwrap: our seller nests under "payment"; MACH/x402v2 may be flat
        # or under "accepts"[0].
        p = body
        if isinstance(body.get("payment"), dict):
            p = body["payment"]
        elif isinstance(body.get("accepts"), list) and body["accepts"]:
            p = body["accepts"][0]

        def _get(*names: str) -> Any:
            for n in names:
                if p.get(n) is not None:
                    return p[n]
            return None

        amount_atomic = _get("maxAmountRequired", "amount_atomic", "amount")
        asset = _get("asset", "assetAddress")
        network = _get("network", "chainId")
        pay_to = _get("payTo", "pay_to", "recipient")
        # nonce may be absent -> we generate one (EIP-3009 allows any unique)
        nonce = _get("nonce") or ("0x" + secrets.token_hex(32))
        valid_after = _get("validAfter", "valid_after") or 0
        valid_before = _get("validBefore", "valid_before") or 0

        missing = [
            n
            for n, v in [
                ("amount", amount_atomic),
                ("asset", asset),
                ("network", network),
                ("pay_to", pay_to),
            ]
            if v is None
        ]
        if missing:
            raise MalformedChallenge(f"402 missing fields: {', '.join(missing)}")

        # Normalize network to eip155:NNNN
        net_str = str(network)
        if net_str.isdigit():
            net_str = f"eip155:{net_str}"
        if net_str != BASE_CHAIN_REF:
            raise UnsupportedPayment(f"unsupported network: {network!r}")

        # Only USDC on Base
        if str(asset).lower() != USDC_BASE_ADDRESS.lower():
            raise UnsupportedPayment(f"unsupported asset: {asset!r}")

        try:
            amount_atomic_int = int(amount_atomic)
        except (TypeError, ValueError) as exc:
            raise MalformedChallenge(f"bad amount: {amount_atomic!r}") from exc

        amount_usd = amount_atomic_int / (10**USDC_DECIMALS)

        return PaymentRequirements(
            amount_atomic=amount_atomic_int,
            amount_usd=amount_usd,
            asset=str(asset),
            network=net_str,
            pay_to=str(pay_to),
            nonce=str(nonce),
            valid_after=int(valid_after or 0),
            valid_before=int(valid_before or 0),
            resource=str(_get("resource") or ""),
            description=str(_get("description") or ""),
        )

    # -- safety gates ---------------------------------------------------

    def _check_requirements(self, req: PaymentRequirements) -> None:
        """Fail-closed gates before signing. Raises on any violation."""
        if req.amount_usd > self.max_payment_usd:
            raise PaymentCapExceeded(
                f"402 demands ${req.amount_usd:.4f} > cap ${self.max_payment_usd:.2f}; "
                "no signature produced"
            )
        # Item 263 / S-BS-06: the price policy is RELATIVE (expected price +
        # tolerance), so a x10-inflated quote that still fits under the
        # absolute cap is refused here. Enforced at the signing gate itself
        # so every signing path (fetch, direct sign_authorization) is covered.
        if self.expected_price_usd is not None:
            check_price_expectation(
                self.expected_price_usd, req, tolerance_bps=self.price_tolerance_bps
            )
        if req.amount_atomic <= 0:
            raise MalformedChallenge("non-positive payment amount")
        now = int(time.time())
        # valid_before must be future and within 1h (absurd expiries rejected)
        if req.valid_before and req.valid_before <= now:
            raise StaleChallenge("valid_before is in the past")
        if req.valid_before and req.valid_before > now + 3600:
            raise StaleChallenge("valid_before more than 1h out; refusing")

    # -- EIP-712 signing ------------------------------------------------

    # -- validate-before-signing helpers (item 301) ----------------------

    @staticmethod
    def signing_summary(typed_data: dict[str, Any]) -> dict[str, Any]:
        """Canonical summary from the exact typed_data (see module fn)."""
        return signing_summary(typed_data)

    def validate_before_signing(
        self,
        typed_data: dict[str, Any],
        req: PaymentRequirements,
        *,
        expected_valid_before: int | None = None,
        summary: dict[str, Any] | None = None,
        descriptor: Any = None,
    ) -> dict[str, Any]:
        """Validate typed_data against the checked req (see module fn)."""
        return validate_typed_data_before_signing(
            typed_data,
            req,
            wallet_address=self.wallet_address,
            expected_valid_before=expected_valid_before,
            summary=summary,
            descriptor=descriptor,
        )

    def sign_authorization(
        self,
        req: PaymentRequirements,
        *,
        summary: dict[str, Any] | None = None,
        descriptor: Any = None,
    ) -> SignedPayment:
        """Build and sign EIP-712 transferWithAuthorization via injected signer.

        Item 301 gate: the constructed typed_data is validated
        field-by-field against the checked requirements (recipient,
        spend value, expiry, nonce, domain; no hidden/extra fields)
        BEFORE the signer is called. When ``summary`` — the summary
        shown to the signer — is supplied, it must equal the canonical
        summary generated from this exact typed_data or signing is
        refused. ``descriptor`` is untrusted display metadata and is
        never used as verification. The honest-path typed_data (fields
        and values) is unchanged by this gate.
        """
        self._check_requirements(req)

        now = int(time.time())
        valid_before = req.valid_before or (now + 600)  # default 10 min

        message = {
            "from": self.wallet_address,
            "to": req.pay_to,
            "value": req.amount_atomic,
            "validAfter": req.valid_after or 0,
            "validBefore": valid_before,
            "nonce": req.nonce,
        }
        typed_data = {
            "types": _TRANSFER_WITH_AUTH_TYPES,
            "domain": _USDC_DOMAIN,
            "primaryType": "TransferWithAuthorization",
            "message": message,
        }
        validate_typed_data_before_signing(
            typed_data,
            req,
            wallet_address=self.wallet_address,
            expected_valid_before=valid_before,
            summary=summary,
            descriptor=descriptor,
        )
        signature = self._signer_fn(typed_data)
        if not signature or not str(signature).startswith("0x"):
            raise X402BuyerError("signer_fn did not return 0x signature")
        return SignedPayment(signature=str(signature), authorization=message)

    # -- payment-attempt journal (W-75) -------------------------------------

    @staticmethod
    def _same_logical_payment(
        req: PaymentRequirements, journaled: PaymentRequirements
    ) -> bool:
        """True when two challenges describe the same logical payment.

        Same recipient, amount, asset, and network.  A changed price or
        recipient is a NEW payment and must be signed fresh.
        """
        return (
            req.pay_to == journaled.pay_to
            and req.amount_atomic == journaled.amount_atomic
            and req.asset == journaled.asset
            and req.network == journaled.network
        )

    def _reuse_inflight(
        self, url: str, req: PaymentRequirements
    ) -> Optional[SignedPayment]:
        """Return the in-flight signed authorization for a retry, if any.

        Returns None when there is no journaled attempt for this URL, when
        the new challenge describes a different logical payment, or when
        the journaled authorization has expired (validBefore in the past) —
        in all of which the caller must sign fresh.
        """
        with self._journal_lock:
            entry = self._inflight.get(url)
        if entry is None:
            return None
        journaled_req, signed = entry
        if not self._same_logical_payment(req, journaled_req):
            return None
        valid_before = int(signed.authorization.get("validBefore") or 0)
        if valid_before and valid_before <= int(time.time()):
            return None
        return signed

    def _record_inflight(
        self, url: str, req: PaymentRequirements, signed: SignedPayment
    ) -> None:
        with self._journal_lock:
            self._inflight[url] = (req, signed)

    def _clear_inflight(self, url: str) -> None:
        with self._journal_lock:
            self._inflight.pop(url, None)

    # -- pending-settlement reconciliation (item 310) ----------------------

    def _pending_error(self, failure: FacilitatorFailure) -> SettlementPendingError:
        """Build the HOLD error for a pending settlement, reconciling first.

        When the failure body carried a broadcast hash and a reconciler
        is wired, the hash is checked onchain NOW — before the caller
        makes any retry decision. A reconciler failure is recorded on
        the error, never converted into a retry: the settlement stays
        pending and no second payment is submitted either way.
        """
        reconciliation: Any = None
        if failure.tx_hash is not None and self._settlement_reconciler is not None:
            try:
                reconciliation = self._settlement_reconciler(failure.tx_hash)
            except Exception as exc:  # reconciler down: still HOLD
                reconciliation = {"error": f"{type(exc).__name__}: {exc}"}
        return SettlementPendingError(
            failure.reason or "settlement_pending",
            "facilitator reports the settlement PENDING "
            f"(broadcast tx: {failure.tx_hash or 'not provided'}); "
            "refusing to submit a second payment — reconcile the "
            "broadcast hash onchain before any retry",
            tx_hash=failure.tx_hash,
            reconciliation=reconciliation,
        )

    # -- HTTP ---------------------------------------------------------

    def _do_request(
        self,
        url: str,
        method: str,
        body: Optional[dict],
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        """Send one HTTP request; return (status, headers, body).

        Transport-failure contract (subclass overrides must honor it):
        - raise :class:`PaymentTransportError` ONLY when the transport can
          prove no request bytes left this machine (DNS failure, connection
          refused, malformed request). The caller treats this as safe to
          retry, even on the paid leg.
        - let any other transport error (``urllib.error.URLError``,
          ``OSError``, timeouts, resets) propagate unchanged: the caller
          maps those to :class:`AmbiguousPayment` on the paid leg, because
          the signed payment may already have been delivered.
        """
        data = json.dumps(body).encode() if body is not None else None
        try:
            req = urllib.request.Request(
                url, data=data, method=method.upper(), headers=headers
            )
        except Exception as exc:
            # Request could not even be constructed: nothing was sent.
            raise PaymentTransportError(
                f"could not build request (nothing sent; safe to retry): {exc}"
            ) from exc
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), exc.read()
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, _PROVABLY_PRE_SEND_REASONS):
                # TCP connect never completed: no bytes left this machine.
                raise PaymentTransportError(
                    "connection never established "
                    f"(nothing sent; safe to retry): {exc.reason}"
                ) from exc
            # Timeout / reset / TLS failure at an unknown point in the send
            # sequence: the payment may have been delivered. Propagate raw;
            # fetch() maps this to AmbiguousPayment on the paid leg.
            raise

    def fetch(
        self,
        url: str,
        method: str = "GET",
        body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Fetch a paid x402 resource: 402 -> sign -> retry -> data.

        Returns the parsed JSON response body on success.
        Raises PaymentCapExceeded / UnsupportedPayment / MalformedChallenge /
        AmbiguousPayment (incl. typed PaymentRefusedError subclasses carrying
        the server's 402 body reason, x402 PR #3795) / PricePolicyViolation /
        StaleChallenge / PaymentTransportError (all fail-closed, no payment
        made unless stated).

        Retry contract:
        - :class:`PaymentTransportError`: safe to retry — no payment was
          made or could have been made.
        - :class:`AmbiguousPayment`: payment state UNKNOWN — do NOT retry
          blindly; reconcile payment status first.  If you must retry the
          same logical payment, this method reuses the in-flight signed
          authorization (W-75) instead of minting a fresh nonce, so the
          retry can never create a second spendable authorization.
        """
        # --- unpaid leg: no signature exists yet, so ANY transport failure
        # is safe to retry.
        try:
            status, headers, raw = self._do_request(url, method, body, {})
        except PaymentTransportError:
            raise
        except Exception as exc:
            raise PaymentTransportError(
                "unpaid request transport error "
                f"(no payment was made; safe to retry): {exc}"
            ) from exc

        if status == 200:
            return {"ok": True, "paid": False, "data": json.loads(raw or b"{}")}

        if status != 402:
            raise X402BuyerError(f"unexpected status {status} on unpaid request")

        # Parse 402: body JSON or PAYMENT-REQUIRED header
        challenge_body: dict[str, Any] = {}
        try:
            challenge_body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            pass
        # Header fallback (x402 v2 sends payment details in header too)
        for h_name in ("PAYMENT-REQUIRED", "X-Payment-Required", "Payment-Required"):
            if headers.get(h_name):
                try:
                    challenge_body = json.loads(headers[h_name])
                    break
                except json.JSONDecodeError:
                    continue

        req = self.parse_402(challenge_body)
        # W-75: on retry of the same logical payment, reuse the in-flight
        # signed authorization instead of minting a fresh nonce.  The
        # onchain nonce can only be spent once, so reuse is double-pay
        # safe; a fresh signature would be a second spendable authorization.
        signed = self._reuse_inflight(url, req)
        if signed is None:
            signed = self.sign_authorization(req)
            self._record_inflight(url, req, signed)

        # --- paid leg: the signed authorization now exists. A transport
        # error here is AMBIGUOUS unless the transport proves nothing was
        # sent: the payment may have settled server-side before the error.
        # A caller-driven retry reuses the in-flight authorization (W-75),
        # never a fresh nonce. Never auto-retry; the caller must reconcile
        # first. The single exception is item 309's CORRECT_AND_RETRY for
        # an EXPIRED authorization (see the loop below).
        corrected = False
        while True:
            payment_header = json.dumps(
                {
                    "x402Version": 1,
                    "scheme": "exact",
                    "network": req.network,
                    "payload": {
                        "signature": signed.signature,
                        "authorization": signed.authorization,
                    },
                }
            )
            try:
                status2, _, raw2 = self._do_request(
                    url,
                    method,
                    body,
                    {"X-PAYMENT": payment_header, "PAYMENT-SIGNATURE": payment_header},
                )
            except PaymentTransportError:
                # Transport proved no bytes left this machine: safe to retry.
                raise
            except Exception as exc:
                raise AmbiguousPayment(
                    "paid request transport error after the signed payment was "
                    "sent (or at an unknown point in the send sequence); the "
                    "payment MAY have settled server-side. DO NOT retry blindly "
                    "— reconcile payment status before any retry "
                    f"({type(exc).__name__}: {exc})"
                ) from exc

            if status2 == 200:
                # Paid leg settled: the logical payment is complete — drop the
                # journal entry so a later, genuinely new payment signs fresh.
                self._clear_inflight(url)
                return {
                    "ok": True,
                    "paid": True,
                    "amount_usd": req.amount_usd,
                    "data": json.loads(raw2 or b"{}"),
                }

            # Items 309 + 310: classify by the BODY REASON, never by the
            # status alone, and never discard the body — a 500 may carry
            # the typed error (#3937) or a pending settlement's broadcast
            # hash (#3778).
            failure = classify_paid_leg_failure(status2, raw2)

            if (
                failure.action is FailureAction.CORRECT_AND_RETRY
                and not corrected
            ):
                # Expired authorization: the rejected bytes can NEVER
                # settle (the token contract refuses them after
                # validBefore), so discarding them cannot double-pay.
                # Re-sign over the SAME nonce / amount / recipient with
                # a validity window that strictly EXTENDS the rejected
                # one (capped inside the 1h absurdity bound) and resend
                # exactly once. Any second failure — of any class —
                # raises below; nothing loops.
                corrected = True
                self._clear_inflight(url)
                now = int(time.time())
                rejected_vb = int(signed.authorization.get("validBefore") or 0)
                corrected_vb = min(max(rejected_vb, now) + 600, now + 3599)
                signed = self.sign_authorization(
                    replace(req, valid_before=corrected_vb)
                )
                self._record_inflight(url, req, signed)
                continue

            if failure.klass == FAILURE_SETTLEMENT_PENDING:
                # Item 310: reconcile the broadcast hash onchain (inside
                # _pending_error) and HOLD. No second payment, ever.
                raise self._pending_error(failure)

            if status2 == 402:
                # Server still wants payment after our signed retry: do NOT
                # loop. Classify the refusal from the 402 BODY (x402 PR
                # #3795 carries the reason there) into a typed
                # PaymentRefusedError so callers can distinguish
                # unfunded-wallet from bad-signature from settlement
                # failure. Every variant IS an AmbiguousPayment: the
                # payment may have settled, so blind retry stays forbidden.
                raise classify_retry_refusal(raw2)

            raise _error_for_failure(failure)

    # -- preflight ------------------------------------------------------

    def preflight(self, preflight_url: str) -> dict[str, Any]:
        """Free preflight check before any paid call. No signing involved."""
        try:
            status, _, raw = self._do_request(preflight_url, "GET", None, {})
        except PaymentTransportError:
            raise
        except Exception as exc:
            raise PaymentTransportError(
                f"preflight transport error (nothing paid; safe to retry): {exc}"
            ) from exc
        if status != 200:
            raise X402BuyerError(f"preflight failed with status {status}")
        return {"ok": True, "data": json.loads(raw or b"{}")}
