"""Facilitator client — interface + LOCAL REFERENCE implementation.

In x402 the facilitator verifies a signed payment payload and settles it.
Here settlement means "fund the escrow", not "pay the seller directly" —
the seller is paid only on SEAL release after verification.

HONESTY LABEL: ``LocalFacilitator`` performs structural checks only
(signature present, authorization fields present, amounts equal to the
requirement it was handed). It does NOT verify EIP-712 signatures or touch
a chain. Production plugs a real facilitator (e.g. Coinbase CDP) behind
this same protocol.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .types import PaymentRequirement, canonical_json


@dataclass
class FacilitatorResult:
    ok: bool
    payer: str = ""
    payment_ref: str = ""
    error: str = ""


@runtime_checkable
class FacilitatorClient(Protocol):
    def verify_and_hold(self, *, payment_payload: dict[str, Any],
                        requirement: PaymentRequirement) -> FacilitatorResult:
        """Verify `payment_payload` against THE REQUIREMENT ARGUMENT.

        Callers (the server core) must pass their OWN retained requirement —
        never a dict reconstructed from client-supplied data.

        Payment-consumption contract (W-146): a successful result carries
        a ``payment_ref`` that uniquely identifies the underlying payment
        authorization. A production facilitator MUST settle/consume the
        EIP-3009 authorization nonce (on-chain, so the nonce can never be
        authorized or settled again) BEFORE the caller opens an escrow
        against the returned ``payment_ref`` — verifying a signature alone
        does NOT consume anything and is not sufficient. The
        ``payment_ref`` must be derived deterministically from the
        canonical authorization (never from process-randomized state such
        as builtin ``hash()``), so the same authorization yields the same
        ref in every process and the platform can dedupe on it across
        restarts. The platform, in turn, stores the ref on the escrow
        record and rejects any second escrow for a consumed ref.
        """
        ...


# --- EIP-3009 authorization normalization (W-146 mutation fix) -----------
#
# The payment_ref must identify the *semantic* payment, not the raw JSON
# presentation of it. canonical_json() alone only sorts keys: the same
# signed authorization re-presented with a string instead of an int, an
# upper-cased hex nonce/address/signature, a dropped ``0x`` prefix, or an
# extra (unsigned, ignored) field produced a different hash — and thus a
# second escrow — even though LocalFacilitator itself treated every one
# of those variants as the same payment. The fix is to parse exactly the
# six EIP-3009 schema fields into canonical typed values and hash only
# those, plus the signature in canonical form. Anything outside the six
# fields cannot affect the ref; anything malformed fails closed and
# never produces a ref at all.

_EIP3009_FIELDS = ("from", "to", "value", "validAfter", "validBefore", "nonce")

_UINT256_MAX = 2**256 - 1
_ADDRESS_RE = re.compile(r"0[xX][0-9a-fA-F]{40}")
# Canonical decimal: "0", or a non-zero digit followed by digits. No sign,
# no leading zeros, no whitespace, no decimal point, no hex prefix.
_DECIMAL_RE = re.compile(r"(0|[1-9][0-9]*)")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")


def _normalize_address(value: Any, field: str) -> str:
    """Canonical address: ``0x`` + lowercase hex. Rejects anything else."""
    if not isinstance(value, str) or not _ADDRESS_RE.fullmatch(value):
        raise ValueError(f"authorization.{field} is not a valid address")
    return "0x" + value[2:].lower()


def _normalize_uint(value: Any, field: str) -> int:
    """Canonical uint256 from an int or a canonical decimal string.

    Floats (even integral ones like ``1000.0``), hex strings, bools and
    junk are rejected rather than coerced: a form we do not accept must
    fail closed, and every form we do accept must normalize to the same
    integer, hence the same payment_ref.
    """
    if isinstance(value, bool):
        raise ValueError(f"authorization.{field} is not a valid uint256")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and _DECIMAL_RE.fullmatch(value):
        result = int(value)
    else:
        raise ValueError(f"authorization.{field} is not a valid uint256")
    if result < 0 or result > _UINT256_MAX:
        raise ValueError(f"authorization.{field} is out of uint256 range")
    return result


def _normalize_hex_bytes(value: Any, field: str, *,
                         expected_bytes: int | None = None) -> str:
    """Canonical hex bytes: ``0x`` + lowercase hex of the decoded bytes.

    Accepts an optional ``0x``/``0X`` prefix and any hex case, so the
    same bytes always normalize identically. Also accepts raw bytes.
    """
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, str):
        text = value[2:] if value[:2] in ("0x", "0X") else value
        if not text or len(text) % 2 != 0 or not _HEX_RE.fullmatch(text):
            raise ValueError(f"{field} is not valid hex")
        raw = bytes.fromhex(text)
    else:
        raise ValueError(f"{field} is not valid hex")
    if expected_bytes is not None and len(raw) != expected_bytes:
        raise ValueError(
            f"{field} must be {expected_bytes} bytes, got {len(raw)}")
    return "0x" + raw.hex()


def _normalize_authorization(authorization: Any) -> dict[str, Any]:
    """Parse exactly the six EIP-3009 fields into canonical typed values.

    Extra fields are ignored (they are not signed EIP-712 fields and
    must not affect identity); missing fields fail closed.
    """
    if not isinstance(authorization, dict):
        raise ValueError("authorization is not an object")
    for field in _EIP3009_FIELDS:
        if field not in authorization:
            raise ValueError(f"authorization.{field} is missing")
    return {
        "from": _normalize_address(authorization["from"], "from"),
        "to": _normalize_address(authorization["to"], "to"),
        "value": _normalize_uint(authorization["value"], "value"),
        "validAfter": _normalize_uint(
            authorization["validAfter"], "validAfter"),
        "validBefore": _normalize_uint(
            authorization["validBefore"], "validBefore"),
        "nonce": _normalize_hex_bytes(
            authorization["nonce"], "authorization.nonce",
            expected_bytes=32),
    }


def _normalize_signature(signature: Any) -> str:
    return _normalize_hex_bytes(signature, "signature")


class LocalFacilitator:
    """Structural reference facilitator. REFERENCE ONLY."""

    def __init__(self) -> None:
        # Test observability: every requirement object this facilitator was
        # handed, in order. Lets tests prove the server passed its OWN
        # requirement, never the client's claimed one.
        self.seen_requirements: list[PaymentRequirement] = []
        self.calls = 0

    def verify_and_hold(self, *, payment_payload: dict[str, Any],
                        requirement: PaymentRequirement) -> FacilitatorResult:
        self.calls += 1
        self.seen_requirements.append(requirement)
        if not isinstance(payment_payload, dict):
            return FacilitatorResult(
                False, error="malformed_authorization: payload is not an object")
        payload = payment_payload.get("payload") or {}
        if not isinstance(payload, dict):
            return FacilitatorResult(
                False, error="malformed_authorization: payload is not an object")
        auth = payload.get("authorization") or {}
        signature = payload.get("signature") or ""
        if not signature or (isinstance(signature, str)
                             and not signature.strip()):
            return FacilitatorResult(False, error="missing_signature")
        # Fail closed on malformed input: normalize FIRST, and never let
        # a ref (or a payer/amount/payee check) be derived from raw,
        # un-normalized client data.
        try:
            normalized_signature = _normalize_signature(signature)
        except ValueError as exc:
            return FacilitatorResult(
                False, error=f"malformed_signature: {exc}")
        if isinstance(auth, dict) and not auth.get("from"):
            return FacilitatorResult(False, error="missing_authorization_from")
        try:
            normalized_auth = _normalize_authorization(auth)
        except ValueError as exc:
            return FacilitatorResult(
                False, error=f"malformed_authorization: {exc}")
        payer = normalized_auth["from"]
        # The authorization must be for exactly the requirement's amount and
        # recipient — checked against the server's requirement argument,
        # using the normalized values.
        if normalized_auth["value"] != int(requirement.max_amount_required):
            return FacilitatorResult(False, error="amount_mismatch")
        try:
            expected_to = _normalize_address(requirement.pay_to, "to")
        except ValueError:
            expected_to = str(requirement.pay_to).lower()
        if normalized_auth["to"] != expected_to:
            return FacilitatorResult(False, error="payto_mismatch")
        return FacilitatorResult(
            True, payer=payer,
            payment_ref=_stable_payment_ref(normalized_auth,
                                            normalized_signature))


def _stable_payment_ref(authorization: dict[str, Any], signature: Any) -> str:
    """Stable payment reference for one authorization (W-146).

    A SHA-256 over the canonical (sorted-key) JSON of the NORMALIZED
    authorization plus its signature in canonical form, domain-separated
    for this use. Only the six EIP-3009 schema fields contribute, in
    canonical typed form (addresses lowercased, integers as integers,
    nonce/signature as prefix-normalized lowercase hex), so every
    re-presentation of the same signed authorization — string vs int
    amounts, hex case or prefix changes, extra unsigned fields — yields
    the identical ref, and distinct authorizations yield distinct refs,
    in every process. Malformed input raises ``ValueError`` instead of
    producing a ref that hashes raw input. Builtin ``hash()`` is
    process-randomized for strings and must never be used here: a ref
    that changes across restarts cannot be consumed or deduped.
    """
    normalized_auth = _normalize_authorization(authorization)
    normalized_signature = _normalize_signature(signature)
    material = canonical_json({
        "domain": "sincor-x402/payment-ref/v2",
        "authorization": normalized_auth,
        "signature": normalized_signature,
    })
    return "localref_" + hashlib.sha256(material).hexdigest()
