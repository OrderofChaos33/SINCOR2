"""W-74: hashless x402 pending payment must not crash the finality poller.

RED-TEAM FINDING (hunt #15, confirmed live twice):
``from_x402_challenge({"status": "pending"})`` with no tx_hash — an ONCHAIN
provider (x402 is in ``_TX_HASH_REQUIRED_PROVIDERS``) mapped without
canonical onchain evidence — driven through ``check_finality``'s offchain
fast-path raises ``MissingTxHashError`` OUT of ``check_finality`` when the
CAS stepping loop attempts ``PENDING_VERIFICATION -> VERIFIED``.  The guard
is fail-closed (state stays pending_verification, no money moves) but the
exception escapes UNCAUGHT, killing an unguarded poller thread — same shape
as W-54.

Fixed behavior: the exception is caught in the poller/CAS path alongside
``IllegalPaymentTransition`` and converted to a clean fail-closed
``FinalityResult`` — the payment stays in pending_verification, nothing
moves, and the poller survives for the next poll.

Self-contained: no network, no real RPC.
Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_hunt15_w74.py --noconftest -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from sincor2.payment_finality import (
    FinalityPolicy,
    check_finality,
    clear_entitlement_hooks,
    register_entitlement_granter,
)
from sincor2.payment_ledger import (
    PaymentLedgerStore,
    PaymentState,
    from_x402_challenge,
)


class FakeChainClient:
    """In-memory chain client; never consulted on the hashless path."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_receipt(self, tx_hash: str) -> Optional[Mapping[str, Any]]:
        self.calls.append("get_receipt")
        return None

    def get_block(self, height: int) -> Optional[Mapping[str, Any]]:
        self.calls.append("get_block")
        return None

    def head_height(self) -> int:
        self.calls.append("head_height")
        return 0


@pytest.fixture()
def store(tmp_path: Path) -> PaymentLedgerStore:
    return PaymentLedgerStore(tmp_path / "ledger.db")


@pytest.fixture()
def policy() -> FinalityPolicy:
    return FinalityPolicy(thresholds={8453: 12})


@pytest.fixture()
def chain() -> FakeChainClient:
    return FakeChainClient()


@pytest.fixture()
def granted_hook():
    clear_entitlement_hooks()
    granted: list[str] = []
    register_entitlement_granter(
        lambda rec: granted.append(rec.payment_id) or {"granted": rec.payment_id}
    )
    yield granted
    clear_entitlement_hooks()


def _hashless_x402_pending(store: PaymentLedgerStore) -> str:
    """The exact red-team trigger: pending x402 challenge, NO tx_hash."""
    rec = from_x402_challenge(
        {
            "status": "pending",
            "challenge_id": "w74-hashless-pending",
            "payer_wallet": "0xPayer",
            "amount_atomic": 10**18,
            "amount_display": 1.0,
            "currency": "USDC",
            "resource_id": "res-1",
            # NOTE: no tx_hash — the mapper maps it to PENDING_VERIFICATION.
        }
    )
    assert rec.state == PaymentState.PENDING_VERIFICATION
    assert not rec.provider_evidence.get("tx_hash")
    store.ingest(rec)
    return rec.payment_id


# ---------------------------------------------------------------------------
# W-74 core: no exception may escape check_finality on this input


def test_hashless_x402_pending_does_not_propagate(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient
):
    pid = _hashless_x402_pending(store)
    # Must NOT raise MissingTxHashError (this raised on the unfixed code).
    res = check_finality(store, pid, chain, policy)
    assert res.ok is False
    assert res.status == "missing_tx_hash"
    assert res.payment_id == pid


def test_hashless_x402_pending_stays_pending_verification(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient
):
    pid = _hashless_x402_pending(store)
    check_finality(store, pid, chain, policy)
    rec = store.get(pid)
    assert rec.state == PaymentState.PENDING_VERIFICATION


def test_hashless_x402_pending_no_money_movement(
    store: PaymentLedgerStore,
    policy: FinalityPolicy,
    chain: FakeChainClient,
    granted_hook,
):
    """No settlement, no entitlement grant, no verified-or-later transition."""
    pid = _hashless_x402_pending(store)
    check_finality(store, pid, chain, policy)
    rec = store.get(pid)
    states_seen = {h.to_state for h in rec.history} | {rec.state}
    assert PaymentState.VERIFIED not in states_seen
    assert PaymentState.AWAITING_FINALITY not in states_seen
    assert PaymentState.SETTLED not in states_seen
    assert PaymentState.FULFILLED not in states_seen
    assert granted_hook == []  # entitlement granter never fired
    # Amount untouched by the finality check.
    assert rec.amount_atomic == 10**18
    assert rec.finality_confirmed is False


def test_poller_survives_repeated_polls(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient
):
    """A poller calls check_finality in a loop: every poll returns cleanly."""
    pid = _hashless_x402_pending(store)
    for _ in range(3):
        res = check_finality(store, pid, chain, policy)
        assert res.ok is False and res.status == "missing_tx_hash"
    assert store.get(pid).state == PaymentState.PENDING_VERIFICATION


# ---------------------------------------------------------------------------
# Regression: genuine offchain (non-hash-required) payments still settle


def test_genuine_offchain_payment_still_settles(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient
):
    """The offchain fast-path must keep working for offchain providers."""
    from sincor2.payment_ledger import PaymentRecord

    rec = PaymentRecord(
        payment_id="w74-offchain-stripe",
        owner="buyer-1",
        amount_atomic=500,
        amount_display=5.00,
        currency="USD",
        provider="stripe",  # NOT in _TX_HASH_REQUIRED_PROVIDERS
        provider_evidence={"event_id": "evt_1"},
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="t", reason="t")
    rec.transition(PaymentState.VERIFIED, actor="t", reason="t")
    store.ingest(rec)

    res = check_finality(store, rec.payment_id, chain, policy)
    assert res.ok is True
    assert res.status == "settled"
    assert store.get(rec.payment_id).state == PaymentState.SETTLED
