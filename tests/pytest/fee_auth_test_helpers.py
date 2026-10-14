"""Test-only helpers for the fee-conversion authorization interface.

Deterministic synthetic owner accounts live here — NOT in the production
security module (sincor2/onchain/fee_authorization.py): key-generating code
must never sit beside the verification machinery it could be confused with.

Tests/dry-runs only.  NEVER real keys.
"""

from __future__ import annotations

from typing import Any, List

from eth_hash.auto import keccak


def synthetic_approvers(n: int = 2, seed: int = 0xFEE) -> List[Any]:
    """Deterministic synthetic owner accounts.  NEVER real keys.

    For tests and dry-runs only.  These accounts exist solely so the
    verification machinery can be exercised without owner key material.
    """
    from eth_account import Account

    return [Account.from_key(keccak(f"sincor-fee-auth-test-{seed}-{i}".encode()))
            for i in range(n)]
