"""Cryptographic settlement proofs for the A2A marketplace (G2.4).

The old ``/api/a2a/settle`` route promised a *signed* proof-of-settlement but
returned plain unsigned JSON — anyone could forge one.  This module provides
the real anchors:

1. **Chain-verified payment.**  The payment ``tx_hash`` is verified server-side
   against the chain (receipt status + AXM ``Transfer`` log to the treasury).
   The tx data in a proof comes from the RPC, never from the caller's claims.
2. **Adjudicator-signed settlement rulings.**  When a task was adjudicated
   (quality dispute), settlement additionally requires an EIP-191 ruling signed
   by the adjudicator (the same trust root as the dispute route:
   ``SINCOR_ADJUDICATOR_ID``).  The ruling binds ``task_id``, ``tx_hash``,
   ``axm_paid_wei``, the adjudicator address, and an expiry, so it cannot be
   replayed onto a different task or amount.

Nothing here is signed by the executing process: there is no server signing
key.  ``proof_hash`` is an integrity checksum (sha256 over the canonical
statement), not a signature.

Offline verification: :func:`verify_proof_of_settlement` recomputes the
statement hash and, when a ruling is embedded, recovers its signer with
``eth_account`` — no RPC access required.  The payment leg of the proof is
independently checkable on Basescan from the recorded ``tx_hash``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

SETTLE_RULING_DOMAIN = "SINCOR-SETTLE"
# Ruling freshness mirrors the dispute route (15-minute window); in-window
# replays are harmless because they re-assert the identical statement.
RULING_MAX_SKEW_MS = 15 * 60 * 1000


def build_settle_ruling_message(task_id: str, tx_hash: str,
                                axm_paid_wei: int, adjudicator: str,
                                expires_at_ms: int) -> str:
    """Canonical EIP-191 message the adjudicator signs for a settlement."""
    return "|".join([
        SETTLE_RULING_DOMAIN,
        str(task_id),
        str(tx_hash).lower(),
        str(int(axm_paid_wei)),
        str(adjudicator).lower(),
        str(int(expires_at_ms)),
    ])


def recover_settle_ruling_signer(message: str, signature: str) -> str:
    """Recover the signer address of a settlement ruling (EIP-191).

    Raises ValueError when verification is unavailable or the signature is bad.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from sincor2.sig_canonical import require_low_s
    except Exception as exc:
        raise ValueError("signature verification unavailable") from exc
    try:
        require_low_s(signature)
        return str(Account.recover_message(
            encode_defunct(text=message), signature=signature))
    except Exception as exc:
        raise ValueError("bad signature") from exc


def verify_settle_ruling(ruling: Dict[str, Any], *, task_id: str,
                         tx_hash: str, axm_paid_wei: int,
                         expected_adjudicator: str,
                         now_ms: Optional[int] = None) -> Tuple[bool, str]:
    """Verify an adjudicator-signed settlement ruling.

    The ruling must bind this task, this payment tx, and this amount, be
    unexpired, and recover to the configured adjudicator address.
    """
    if not isinstance(ruling, dict):
        return False, "ruling must be an object"
    if not expected_adjudicator:
        return False, "adjudicator not configured"
    try:
        expires_at_ms = int(ruling.get("expires_at_ms"))
    except (TypeError, ValueError):
        return False, "expires_at_ms must be an integer"
    now = int(now_ms) if now_ms is not None else int(time.time() * 1000)
    if expires_at_ms <= now:
        return False, "ruling expired"
    if expires_at_ms - now > RULING_MAX_SKEW_MS:
        return False, "expires_at_ms too far in the future"
    adjudicator = str(ruling.get("adjudicator_address") or "")
    signature = str(ruling.get("signature") or "")
    if not adjudicator or not signature:
        return False, "adjudicator_address and signature required"
    message = build_settle_ruling_message(
        task_id, tx_hash, axm_paid_wei, adjudicator, expires_at_ms)
    try:
        signer = recover_settle_ruling_signer(message, signature)
    except ValueError as exc:
        return False, str(exc)
    # Never reveal which of the two checks failed.
    if signer.lower() != expected_adjudicator.lower() \
            or adjudicator.lower() != expected_adjudicator.lower():
        return False, "not the adjudicator"
    return True, "ok"


def canonical_statement_hash(statement: Dict[str, Any]) -> str:
    """sha256 over the canonical JSON encoding of a settlement statement."""
    canonical = json.dumps(statement, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def verify_proof_of_settlement(proof: Dict[str, Any]) -> Tuple[bool, str]:
    """Offline verification of a proof-of-settlement document.

    Recomputes ``proof_hash`` from the statement and, when an adjudicator
    ruling is embedded, verifies its EIP-191 signature recovers to the
    proof's ``adjudicator_address``.  Returns (True, "ok") or (False, reason).
    """
    if not isinstance(proof, dict):
        return False, "proof must be an object"
    pos = proof.get("proof_of_settlement")
    if not isinstance(pos, dict):
        return False, "missing proof_of_settlement"
    expected_hash = pos.get("proof_hash")
    statement = {k: v for k, v in pos.items() if k != "proof_hash"}
    if not expected_hash or canonical_statement_hash(statement) != expected_hash:
        return False, "proof_hash mismatch: statement was tampered with"
    ruling = pos.get("adjudicator_ruling")
    if ruling is not None:
        if not isinstance(ruling, dict):
            return False, "adjudicator_ruling must be an object"
        adjudicator = str(ruling.get("adjudicator_address") or "")
        signature = str(ruling.get("signature") or "")
        expires_raw = ruling.get("expires_at_ms")
        try:
            expires_at_ms = int(expires_raw)
        except (TypeError, ValueError):
            return False, "ruling expires_at_ms must be an integer"
        message = build_settle_ruling_message(
            pos.get("task_id", ""), pos.get("tx_hash", ""),
            int(pos.get("axm_paid_wei") or 0), adjudicator, expires_at_ms)
        try:
            signer = recover_settle_ruling_signer(message, signature)
        except ValueError as exc:
            return False, f"ruling signature invalid: {exc}"
        if not adjudicator or signer.lower() != adjudicator.lower():
            return False, "ruling signer does not match adjudicator_address"
        # A ruling whose statement fields disagree with the proof body is a
        # smuggled or replayed ruling: reject it.
        if str(ruling.get("task_id") or "") != str(pos.get("task_id") or ""):
            return False, "ruling task_id does not match proof"
        if str(ruling.get("tx_hash") or "").lower() != \
                str(pos.get("tx_hash") or "").lower():
            return False, "ruling tx_hash does not match proof"
    verification = pos.get("payment_verification")
    if verification not in ("onchain", "dev_bypass"):
        return False, "missing or unknown payment_verification label"
    if verification == "dev_bypass":
        return True, "ok (dev_bypass: payment was NOT verified on-chain)"
    return True, "ok"


def dev_bypass_active() -> bool:
    """True when on-chain payment checks are bypassed (dev/test envs)."""
    env = os.getenv("FLASK_ENV", "production").lower()
    return env in {"development", "dev", "test", "testing", "local"}
