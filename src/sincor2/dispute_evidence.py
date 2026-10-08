"""Receipt-content binding for dispute evidence (dev-watch item 77).

Rationale: an x402/payment receipt proves money moved, not that the
deliverable has any content.  Without a content binding, "a paid API can
return garbage and the receipt still verifies" (custodia-hq
RESEARCH-x402.md; cf. x402 proposals #3234/#3304 proposing a responseHash
over RFC 8785 canonical JSON).

This module makes every dispute-relevant proof carry a content binding:

* ``deliverable_hash`` -- keccak256 of the canonical deliverable bytes.
  The adjudicator recomputes this from the retrieved deliverable, so a
  swapped or tampered deliverable is caught by hash mismatch.
* ``deliverable_uri`` -- where the adjudicator can retrieve the content.
* ``content_binding_sig`` -- optional EIP-191 signature by the delivering
  agent over the deliverable hash (attribution, not just integrity).

Fail-closed rule: :func:`validate_dispute_evidence` REJECTS any evidence
that carries a payment receipt but no deliverable binding.  A paid-for
garbage response is still *admissible* for adjudication -- that is the
point: the binding lets the adjudicator see the garbage and rule against
the seller instead of being unable to tell.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from eth_hash.auto import keccak

#: Allowed URI schemes for deliverable retrieval.  Restricted so the
#: adjudicator is never pointed at a javascript:/data: pseudo-URL.
_ALLOWED_DELIVERABLE_SCHEMES = frozenset({"http", "https", "ipfs"})


def canonicalize_deliverable(deliverable: Any) -> bytes:
    """Deterministically serialize a deliverable to bytes for hashing.

    * bytes/bytearray pass through unchanged.
    * str encodes as UTF-8.
    * anything else serializes as canonical JSON: keys sorted, compact
      separators, UTF-8.  This is the documented RFC 8785-compatible
      subset: no NaN/Infinity, no duplicate keys, shortest-round-trip
      number formatting (Python's ``json`` already emits that), UTF-8
      output.  Callers with non-JSON-native values should pre-normalize.
    """
    if isinstance(deliverable, (bytes, bytearray)):
        return bytes(deliverable)
    if isinstance(deliverable, str):
        return deliverable.encode("utf-8")
    return json.dumps(
        deliverable,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def hash_deliverable(deliverable: Any) -> str:
    """keccak256 (0x-prefixed hex) of the canonical deliverable bytes."""
    return "0x" + keccak(canonicalize_deliverable(deliverable)).hex()


@dataclass(frozen=True)
class DisputeEvidence:
    """Content-bound evidence attached to a proof of completion.

    ``payment_receipt_hash`` is the staged-payout receipt; it proves
    money moved.  ``deliverable_hash`` + ``deliverable_uri`` prove WHAT
    was delivered, which is what the adjudicator actually needs.
    """

    payment_receipt_hash: str
    deliverable_hash: str
    deliverable_uri: str
    content_binding_sig: str = field(default="")


def _require_receipt(receipt_hash: str) -> str:
    receipt_hash = str(receipt_hash or "").strip()
    if not receipt_hash.startswith("0x") or len(receipt_hash) < 10:
        raise ValueError("payment_receipt_hash must be 0x-prefixed")
    return receipt_hash


def _require_deliverable_hash(deliverable_hash: str) -> str:
    deliverable_hash = str(deliverable_hash or "").strip().lower()
    if not deliverable_hash.startswith("0x") or len(deliverable_hash) != 66:
        raise ValueError(
            "deliverable_hash must be 0x-prefixed keccak256 (66 chars)"
        )
    try:
        bytes.fromhex(deliverable_hash[2:])
    except ValueError:
        raise ValueError("deliverable_hash must be hex") from None
    return deliverable_hash


def _require_deliverable_uri(uri: str) -> str:
    uri = str(uri or "").strip()
    if not uri:
        raise ValueError("deliverable_uri is required: the adjudicator "
                         "must be able to retrieve the deliverable")
    scheme = urlparse(uri).scheme.lower()
    if scheme not in _ALLOWED_DELIVERABLE_SCHEMES:
        raise ValueError(
            f"deliverable_uri scheme must be one of "
            f"{sorted(_ALLOWED_DELIVERABLE_SCHEMES)}"
        )
    return uri


def validate_dispute_evidence(
    evidence: Mapping[str, Any] | DisputeEvidence,
) -> DisputeEvidence:
    """Validate evidence, FAIL CLOSED.

    Accepts a ``DisputeEvidence`` or a plain mapping with keys
    ``payment_receipt_hash``, ``deliverable_hash``, ``deliverable_uri``
    and optional ``content_binding_sig``.

    Raises ``ValueError`` when:

    * the evidence carries a payment receipt but no deliverable binding
      (the item-77 hole: a receipt alone proves payment, never content);
    * the receipt, hash, or URI is malformed.
    """
    if isinstance(evidence, DisputeEvidence):
        data: Mapping[str, Any] = {
            "payment_receipt_hash": evidence.payment_receipt_hash,
            "deliverable_hash": evidence.deliverable_hash,
            "deliverable_uri": evidence.deliverable_uri,
            "content_binding_sig": evidence.content_binding_sig,
        }
    else:
        data = evidence or {}
    receipt = str(data.get("payment_receipt_hash") or "").strip()
    dhash = str(data.get("deliverable_hash") or "").strip()
    if receipt and not dhash:
        raise ValueError(
            "evidence binds payment receipt to no deliverable content: "
            "deliverable_hash is required (receipt-only evidence is "
            "rejected; a receipt proves payment, not delivery)"
        )
    return DisputeEvidence(
        payment_receipt_hash=_require_receipt(receipt),
        deliverable_hash=_require_deliverable_hash(dhash),
        deliverable_uri=_require_deliverable_uri(
            str(data.get("deliverable_uri") or "")),
        content_binding_sig=str(data.get("content_binding_sig") or ""),
    )


def build_evidence(
    payment_receipt_hash: str,
    deliverable: Any,
    deliverable_uri: str,
    *,
    content_binding_sig: str = "",
) -> DisputeEvidence:
    """Build validated evidence from raw deliverable content.

    The hash is computed from the content itself, so the caller cannot
    claim a hash that does not match the delivered bytes.
    """
    return validate_dispute_evidence({
        "payment_receipt_hash": payment_receipt_hash,
        "deliverable_hash": hash_deliverable(deliverable),
        "deliverable_uri": deliverable_uri,
        "content_binding_sig": content_binding_sig,
    })


def verify_deliverable_content(
    deliverable: Any,
    expected_hash: str,
) -> bool:
    """Recompute the deliverable hash; the adjudicator's check.

    Returns True on match; raises ``ValueError`` on mismatch so a
    swapped deliverable can never silently pass.
    """
    expected = _require_deliverable_hash(expected_hash)
    actual = hash_deliverable(deliverable)
    if actual.lower() != expected:
        raise ValueError(
            f"deliverable hash mismatch: content does not match the "
            f"bound evidence (expected {expected}, recomputed {actual})"
        )
    return True


def verify_content_binding_sig(
    evidence: DisputeEvidence,
    agent_wallet: str,
) -> bool:
    """Verify the agent's EIP-191 signature over the deliverable hash.

    No-op (returns True) when no signature is bound -- the field is
    optional.  When a signature IS present and the agent registered an
    onchain-style wallet, the recovered signer must equal that wallet,
    otherwise ``ValueError`` (fail closed on forged attribution).
    """
    sig = (evidence.content_binding_sig or "").strip()
    if not sig:
        return True
    wallet = str(agent_wallet or "").strip()
    if not (wallet.startswith("0x") and len(wallet) == 42):
        # No onchain identity to check against; the binding still holds
        # via deliverable_hash.  Attribution is unverifiable, not invalid.
        return True
    from eth_account import Account
    from eth_account.messages import encode_defunct

    message = encode_defunct(text=evidence.deliverable_hash)
    try:
        signer = Account.recover_message(message, signature=sig)
    except Exception as err:  # noqa: BLE001 - any recovery failure rejects
        raise ValueError(f"content_binding_sig does not recover: {err}")
    if signer.lower() != wallet.lower():
        raise ValueError(
            "content_binding_sig signer does not match the agent wallet"
        )
    return True


def evidence_to_dict(evidence: DisputeEvidence) -> dict[str, Any]:
    """JSON-safe rendering for proof records."""
    return {
        "payment_receipt_hash": evidence.payment_receipt_hash,
        "deliverable_hash": evidence.deliverable_hash,
        "deliverable_uri": evidence.deliverable_uri,
        "content_binding_sig": evidence.content_binding_sig,
    }


def parse_evidence_sequence(
    items: Sequence[Mapping[str, Any]] | None,
) -> list[DisputeEvidence]:
    """Validate a list of evidence mappings (batch disputes)."""
    return [validate_dispute_evidence(item) for item in (items or [])]
