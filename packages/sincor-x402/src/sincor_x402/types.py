"""Core value types.

PaymentRequirement mirrors the platform seller payload in
``sincor2/x402_payments.py`` (x402Version 1, scheme "exact", network "base",
fields: maxAmountRequired / asset / payTo / extra) extended with the
standard x402 ``maxTimeoutSeconds`` field used by the accepts[] hedge.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

USDC_BASE_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_CHAIN_ID = 8453

# The six fields the server-side accepts[] hedge compares, exactly.
ACCEPTS_MATCH_FIELDS = (
    "scheme",
    "network",
    "maxAmountRequired",
    "asset",
    "payTo",
    "maxTimeoutSeconds",
)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(bytes(obj)).hexdigest()
    if isinstance(obj, str):
        return hashlib.sha256(obj.encode("utf-8")).hexdigest()
    return hashlib.sha256(canonical_json(obj)).hexdigest()


@dataclass(frozen=True)
class PaymentRequirement:
    """One entry of a 402 ``accepts[]`` list — the server's own terms."""

    scheme: str = "exact"
    network: str = "base"
    max_amount_required: str = "0"          # atomic units, as string (x402 style)
    asset: str = USDC_BASE_ADDRESS
    pay_to: str = ""
    max_timeout_seconds: int = 60
    resource: str = ""
    description: str = ""
    mime_type: str = "application/json"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_accepts_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "network": self.network,
            "maxAmountRequired": str(self.max_amount_required),
            "asset": self.asset,
            "payTo": self.pay_to,
            "maxTimeoutSeconds": int(self.max_timeout_seconds),
            "resource": self.resource,
            "description": self.description,
            "mimeType": self.mime_type,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_accepts_dict(cls, d: dict[str, Any]) -> "PaymentRequirement":
        return cls(
            scheme=str(d.get("scheme", "exact")),
            network=str(d.get("network", "base")),
            max_amount_required=str(d.get("maxAmountRequired", "0")),
            asset=str(d.get("asset", "")),
            pay_to=str(d.get("payTo", "")),
            max_timeout_seconds=int(d.get("maxTimeoutSeconds", 60)),
            resource=str(d.get("resource", "")),
            description=str(d.get("description", "")),
            mime_type=str(d.get("mimeType", "application/json")),
            extra=dict(d.get("extra") or {}),
        )

    def matches_claimed(self, claimed: dict[str, Any]) -> bool:
        """Exact match on the six hedge fields against a client-claimed dict.

        Amounts compare as integers (so "1000" == 1000), addresses compare
        case-insensitively (checksummed vs lower-case hex is the same
        address), everything else compares as exact strings/ints.
        """
        own = self.to_accepts_dict()
        for f in ACCEPTS_MATCH_FIELDS:
            if f not in claimed:
                return False
            a, b = own[f], claimed[f]
            if f == "maxAmountRequired":
                try:
                    if int(a) != int(b):  # type: ignore[arg-type]
                        return False
                except (TypeError, ValueError):
                    return False
            elif f == "maxTimeoutSeconds":
                try:
                    if int(a) != int(b):  # type: ignore[arg-type]
                        return False
                except (TypeError, ValueError):
                    return False
            elif f in ("asset", "payTo"):
                if str(a).lower() != str(b).lower():
                    return False
            else:
                if str(a) != str(b):
                    return False
        return True


@dataclass
class EscrowTerms:
    """Escrow + SIGIL commitment terms attached to a 402 challenge."""

    requirement: PaymentRequirement
    spec: dict[str, Any]                # what a good result must satisfy
    spec_hash: str = ""                 # SIGIL-style commitment: sha256(spec)
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.spec_hash:
            self.spec_hash = sha256_hex(self.spec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": "sincor-escrow-v1",
            "spec": self.spec,
            "specHash": self.spec_hash,
            "timeoutSeconds": self.timeout_seconds,
            "settlement": "seal-release-on-verify-pass / refund-on-fail-or-timeout",
            "note": ("REFERENCE TERMS — escrow is held by the configured "
                     "PlatformClient; no Base-mainnet contract is deployed "
                     "by this package."),
        }
