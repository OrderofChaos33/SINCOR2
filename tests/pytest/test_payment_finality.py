"""Tests for finality and reorg-aware settlement (PAY-03).

Self-contained: FakeChainClient implements the ChainClient protocol.
No network, no real RPC.
Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_payment_finality.py --noconftest -q
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

from sincor2.payment_finality import (
    FinalityPolicy,
    check_finality,
    clear_entitlement_hooks,
    on_reorg,
    register_entitlement_granter,
    register_entitlement_revoker,
)
from sincor2.payment_ledger import (
    PaymentLedgerStore,
    PaymentRecord,
    PaymentState,
    from_stripe_event,
    is_paid_state,
    list_alerts,
)


class FakeChainClient:
    """In-memory chain: receipts, block hashes, head height, outage switch."""

    def __init__(self) -> None:
        self.receipts: dict[str, Mapping[str, Any]] = {}
        self.blocks: dict[int, str] = {}
        self.head: int = 0
        self.fail: bool = False

    def add_tx(self, tx_hash: str, block_number: int, block_hash: str,
               status: int = 1) -> None:
        self.receipts[tx_hash] = {
            "blockNumber": block_number,
            "blockHash": block_hash,
            "status": status,
        }
        self.blocks[block_number] = block_hash

    def get_receipt(self, tx_hash: str) -> Optional[Mapping[str, Any]]:
        if self.fail:
            raise ConnectionError("fake chain is down")
        return self.receipts.get(tx_hash)

    def get_block(self, height: int) -> Optional[Mapping[str, Any]]:
        if self.fail:
            raise ConnectionError("fake chain is down")
        h = self.blocks.get(height)
        return {"number": height, "hash": h} if h is not None else None

    def head_height(self) -> int:
        if self.fail:
            raise ConnectionError("fake chain is down")
        return self.head


TX = "0x" + "aa" * 32
BH1 = "0x" + "b1" * 32
BH2 = "0x" + "b2" * 32


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
def hooks():
    clear_entitlement_hooks()
    granted: list[str] = []
    revoked: list[str] = []
    register_entitlement_granter(lambda rec: granted.append(rec.payment_id) or {"granted": rec.payment_id})
    register_entitlement_revoker(lambda rec: revoked.append(rec.payment_id) or {"revoked": rec.payment_id})
    yield granted, revoked
    clear_entitlement_hooks()


def _verified_onchain(store: PaymentLedgerStore, pid: str = "pay-fin-1") -> str:
    rec = PaymentRecord(
        payment_id=pid, owner="0xPayer", amount_atomic=10**18, amount_display=1.0,
        currency="AXM", provider="a2a",
        provider_evidence={"tx_hash": TX, "chain_id": 8453},
        entitlements={"task_id": "task-9"},
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="t", reason="t")
    rec.transition(PaymentState.VERIFIED, actor="t", reason="t")
    store.ingest(rec)
    return pid


# ---------------------------------------------------------------------------


def test_below_threshold_stays_awaiting_finality_no_entitlement(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    granted, _revoked = hooks
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 105  # 6 confirmations < 12

    res = check_finality(store, pid, chain, policy)
    assert res.ok is False
    assert res.status == "awaiting_finality"
    assert res.confirmations == 6 and res.required == 12

    rec = store.get(pid)
    assert rec.state == PaymentState.AWAITING_FINALITY
    assert rec.confirmations == 6
    assert rec.block_hash == BH1
    assert granted == []  # no entitlement below threshold
    assert not is_paid_state(rec.state)


def test_at_threshold_settles_and_grants(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    granted, _revoked = hooks
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 105
    check_finality(store, pid, chain, policy)  # below threshold
    chain.head = 111  # 12 confirmations >= 12

    res = check_finality(store, pid, chain, policy)
    assert res.ok is True and res.status == "settled"
    rec = store.get(pid)
    assert rec.state == PaymentState.SETTLED
    assert rec.finality_confirmed is True
    assert granted == [pid]  # entitlement granted exactly once


def test_reorg_disputes_revokes_and_alerts(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    granted, revoked = hooks
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 105
    first = check_finality(store, pid, chain, policy)
    assert first.status == "awaiting_finality"
    assert store.get(pid).block_hash == BH1

    # Chain reorgs: height 100 now has a different hash.
    chain.blocks[100] = BH2
    chain.head = 111

    res = check_finality(store, pid, chain, policy)
    assert res.status == "disputed"
    rec = store.get(pid)
    assert rec.state == PaymentState.DISPUTED
    assert revoked == [pid]  # entitlements revoked
    assert granted == []     # never granted

    alerts = list_alerts(store, pid)
    assert len(alerts) == 1
    assert alerts[0].kind == "reorg_detected"
    assert "100" in alerts[0].message


def test_missing_receipt_stays_no_crash(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    granted, _revoked = hooks
    pid = _verified_onchain(store)
    chain.head = 200  # head moves, but no receipt for the tx

    res = check_finality(store, pid, chain, policy)
    assert res.ok is False and res.status == "receipt_not_found"
    assert store.get(pid).state == PaymentState.AWAITING_FINALITY
    assert granted == []


def test_chain_outage_then_recovery_no_double_settle(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    granted, _revoked = hooks
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 120

    chain.fail = True
    down = check_finality(store, pid, chain, policy)
    assert down.status == "chain_unavailable"
    assert store.get(pid).state == PaymentState.AWAITING_FINALITY

    chain.fail = False  # recovery
    up = check_finality(store, pid, chain, policy)
    assert up.status == "settled"
    assert granted == [pid]

    # Repeated checks after settlement are idempotent: no re-grant, no dup history.
    again = check_finality(store, pid, chain, policy)
    assert again.status == "already_settled"
    assert granted == [pid]
    settled_entries = [h for h in store.get(pid).history if h.to_state == "settled"]
    assert len(settled_entries) == 1


def test_reverted_tx_fails_no_entitlement(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    granted, _revoked = hooks
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1, status=0)
    chain.head = 120

    res = check_finality(store, pid, chain, policy)
    assert res.status == "tx_reverted"
    assert store.get(pid).state == PaymentState.FAILED
    assert granted == []


def test_offchain_fiat_settles_without_chain(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    granted, _revoked = hooks
    rec = from_stripe_event({
        "event": "checkout.session.completed",
        "session_id": "cs_offchain_1",
        "customer_email": "buyer@example.com",
        "plan_id": "report",
        "amount": 49.0,
        "currency": "USD",
        "payment_status": "completed",
    })
    store.ingest(rec)
    res = check_finality(store, rec.payment_id, chain, policy)
    assert res.ok is True and res.status == "settled"
    assert store.get(rec.payment_id).state == PaymentState.SETTLED
    assert granted == [rec.payment_id]


def test_on_reorg_handler_direct(
    store: PaymentLedgerStore, chain: FakeChainClient, hooks
):
    _granted, revoked = hooks
    pid = _verified_onchain(store)
    store.transition_payment(pid, PaymentState.AWAITING_FINALITY, actor="t")

    res = on_reorg(store, pid, block_number=100, old_block_hash=BH1,
                   new_block_hash=BH2)
    assert res.status == "disputed"
    assert store.get(pid).state == PaymentState.DISPUTED
    assert revoked == [pid]
    assert list_alerts(store, pid)[0].kind == "reorg_detected"

    # Idempotent: second call does not duplicate alerts.
    res2 = on_reorg(store, pid, block_number=100, old_block_hash=BH1,
                    new_block_hash=BH2)
    assert res2.status == "already_disputed"
    assert len(list_alerts(store, pid)) == 1


def test_env_threshold_override(store: PaymentLedgerStore, chain: FakeChainClient,
                                hooks, monkeypatch):
    granted, _revoked = hooks
    monkeypatch.setenv("SINCOR_FINALITY_CONFIRMATIONS", '{"8453": 3}')
    policy = FinalityPolicy()
    assert policy.confirmations_for(8453) == 3

    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 102  # 3 confirmations
    res = check_finality(store, pid, chain, policy)
    assert res.status == "settled"
    assert granted == [pid]


def test_progress_recorded_without_unlocking_settlement(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient
):
    """record_chain_progress must not let a direct settle slip through."""
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 105
    check_finality(store, pid, chain, policy)
    rec = store.get(pid)
    assert rec.confirmations == 6
    assert rec.finality_confirmed is False


def test_reorg_after_settlement_disputes_and_revokes(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    """A deep reorg that orphans an already-settled tx must dispute it."""
    granted, revoked = hooks
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 120
    assert check_finality(store, pid, chain, policy).status == "settled"
    assert granted == [pid]

    chain.blocks[100] = BH2  # deep reorg after settlement
    chain.head = 130
    res = check_finality(store, pid, chain, policy)
    assert res.status == "disputed"
    assert store.get(pid).state == PaymentState.DISPUTED
    assert revoked == [pid]
    assert list_alerts(store, pid)[0].kind == "reorg_detected"


def test_settled_payment_chain_outage_is_not_a_reorg(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient
):
    """Outage/missing block on a settled payment must not trigger dispute."""
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 120
    assert check_finality(store, pid, chain, policy).status == "settled"

    chain.fail = True
    res = check_finality(store, pid, chain, policy)
    assert res.status == "already_settled"
    assert store.get(pid).state == PaymentState.SETTLED
    assert list_alerts(store, pid) == []


def test_concurrent_settlers_grant_exactly_once(
    store: PaymentLedgerStore, policy: FinalityPolicy, chain: FakeChainClient, hooks
):
    """Two pollers racing at threshold: one settles, the other gets already_settled."""
    import threading

    granted, _revoked = hooks
    pid = _verified_onchain(store)
    chain.add_tx(TX, block_number=100, block_hash=BH1)
    chain.head = 120

    results: list = []

    def poll():
        results.append(check_finality(store, pid, chain, policy).status)

    threads = [threading.Thread(target=poll) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("settled") == 1, results
    assert all(s == "already_settled" for s in results if s != "settled")
    assert granted == [pid]  # entitlement granted exactly once
    settled_entries = [h for h in store.get(pid).history if h.to_state == "settled"]
    assert len(settled_entries) == 1
