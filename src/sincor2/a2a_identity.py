"""Verified wallet identity primitives for A2A registration (wave 32).

First-registration squatting control: an ``agent_id`` claim can be bound to a
wallet by presenting a fresh EIP-191 personal signature over a canonical
registration message. This reuses the codebase's existing EIP-191 recovery
primitive (dispute route, KYA registry, wave-18 quota identity, wave-24
reputation identity) — no new identity scheme.

Canonical messages:
  register: "SINCOR-REGISTER|<agent_id>|<timestamp_ms>"
  transfer: "SINCOR-TRANSFER|<agent_id>|<new_wallet>|<timestamp_ms>"

The wallet claim is mandatory and must equal the recovered signer: ECDSA
recovery returns *some* address for any message/signature pair, so the
recovered address is only meaningful when checked against the caller's
claimed wallet (same footgun closed in waves 18 and 24). ``timestamp_ms``
must be within IDENTITY_MAX_SKEW_MS of server time.

This module never imports eth_account at module scope (lazy import keeps the
package importable without the dependency); when the dependency is absent,
verification is unavailable and every proof resolves to None (fail-closed
toward "unverified", never toward "verified").
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

logger = logging.getLogger("sincor.a2a.identity")

REGISTER_MESSAGE_PREFIX = "SINCOR-REGISTER"
TRANSFER_MESSAGE_PREFIX = "SINCOR-TRANSFER"

# Freshness window for registration/transfer signatures (matches the
# reputation/quota identity window: 5 minutes).
IDENTITY_MAX_SKEW_MS = 5 * 60 * 1000

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def register_message(agent_id: str, timestamp_ms: int) -> str:
    """Canonical EIP-191 message a caller signs to claim an agent_id."""
    return "|".join([REGISTER_MESSAGE_PREFIX, str(agent_id), str(int(timestamp_ms))])


def transfer_message(agent_id: str, new_wallet: str, timestamp_ms: int) -> str:
    """Canonical EIP-191 message the owner signs to transfer an agent_id."""
    return "|".join([TRANSFER_MESSAGE_PREFIX, str(agent_id),
                     str(new_wallet).lower(), str(int(timestamp_ms))])


def recover_signer(message: str, signature: str) -> str:
    """Recover the signer address of an EIP-191 message.

    Same primitive as the dispute route and the KYA registry (lazy
    eth_account import). Raises ValueError on any failure.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except Exception as exc:
        raise ValueError("signature verification unavailable") from exc
    try:
        return str(Account.recover_message(
            encode_defunct(text=message), signature=signature))
    except Exception as exc:
        raise ValueError("bad signature") from exc


def verify_wallet_proof(*, message: str, signature: Optional[str],
                        wallet: Optional[str],
                        timestamp_ms: Optional[int]) -> Optional[str]:
    """Verify a wallet-identity proof against a canonical message.

    Returns the lowercased wallet address on success, None on any failure.
    The claimed ``wallet`` must equal the recovered signer (mandatory), and
    the timestamp must be fresh. Never raises for bad input — callers treat
    None as "no verified identity".
    """
    if not signature or not wallet:
        return None
    claimed = str(wallet).strip()
    if not _ADDRESS_RE.match(claimed):
        logger.warning("identity rejected: invalid wallet claim %r", claimed[:20])
        return None
    try:
        ts = int(str(timestamp_ms).strip())
    except (TypeError, ValueError):
        logger.warning("identity rejected: bad timestamp %r", timestamp_ms)
        return None
    now_ms = int(time.time() * 1000)
    if abs(now_ms - ts) > IDENTITY_MAX_SKEW_MS:
        logger.warning("identity rejected: timestamp outside freshness window")
        return None
    try:
        recovered = recover_signer(message, str(signature).strip())
    except ValueError as exc:
        logger.warning("identity rejected: %s", exc)
        return None
    if recovered.lower() != claimed.lower():
        # Mandatory-claim check: a signature over one message must not mint
        # identity for an arbitrary claimed wallet.
        logger.warning("identity rejected: wallet claim != recovered signer")
        return None
    return claimed.lower()
