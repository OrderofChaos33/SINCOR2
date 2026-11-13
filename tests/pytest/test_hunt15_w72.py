"""Regression tests for red-team Hunt 15: W-72.

W-72 — stale finality_confirmed survives reorg: ``on_reorg`` moved the
payment to DISPUTED but never cleared the write-once ``finality_confirmed``
flag, so after dispute resolution the payment could re-settle on the stale
confirmation without fresh finality evidence.  Batch-A's claim 4
("on_reorg resets finality_confirmed") was merged as fixed but never
implemented — this test discriminates: it FAILS on the unfixed code.

Fix: any transition INTO disputed clears ``finality_confirmed`` (both the
CAS path used by ``on_reorg`` and the direct path).  Only a fresh
``mark_finality_confirmed`` can re-set it.

Self-contained: temp sqlite files, no app, no network.
Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_hunt15_w72.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sincor2.payment_finality import on_reorg
from sincor2.payment_ledger import (
    FinalityNotConfirmed,
    PaymentLedgerStore,
    PaymentRecord,
    PaymentState,
    from_x402_challenge,
)


TX_CANON = "0x" + "ab" * 32
BH1 = "0x" + "b1" * 32
BH2 = "0x" + "b2" * 32


@pytest.fixture()
def store(tmp_path: Path) -> PaymentLedgerStore:
    return PaymentLedgerStore(tmp_path / "ledger.db")


def _settled_onchain(store: PaymentLedgerStore, cid: str) -> str:
    """Drive a payment to SETTLED with confirmed finality; return its id."""
    rec: PaymentRecord = from_x402_challenge({
        "challenge_id": cid, "status": "fulfilled", "tx_hash": TX_CANON,
        "payer_wallet": "0xPayer", "amount_atomic": 10**18,
        "amount_display": 1.0, "currency": "USDC",
    })
    store.ingest(rec)
    pid = rec.payment_id
    store.transition_payment(pid, PaymentState.AWAITING_FINALITY, actor="t")
    store.mark_finality_confirmed(
        pid, block_number=100, block_hash=BH1, confirmations=5
    )
    assert store.get(pid).finality_confirmed is True
    store.transition_payment(pid, PaymentState.SETTLED, actor="t")
    return pid


class TestW72ReorgClearsFinality:
    def test_on_reorg_clears_finality_confirmed(self, store: PaymentLedgerStore):
        pid = _settled_onchain(store, "w72-1")
        res = on_reorg(store, pid, block_number=100,
                       old_block_hash=BH1, new_block_hash=BH2)
        assert res.status == "disputed"
        after = store.get(pid)
        assert after.state == PaymentState.DISPUTED
        # THE discriminating assertion: fails on unfixed code where the
        # write-once flag survives the reorg.
        assert after.finality_confirmed is False

    def test_resettlement_requires_fresh_finality(self, store: PaymentLedgerStore):
        pid = _settled_onchain(store, "w72-2")
        on_reorg(store, pid, block_number=100,
                 old_block_hash=BH1, new_block_hash=BH2)
        # Dispute resolution: the only legal exit is re-verification.
        store.transition_payment(pid, PaymentState.PENDING_VERIFICATION, actor="t")
        store.transition_payment(pid, PaymentState.VERIFIED, actor="t")
        store.transition_payment(pid, PaymentState.AWAITING_FINALITY, actor="t")
        # Without a FRESH mark_finality_confirmed, settlement must fail
        # closed — the stale pre-reorg confirmation is void.
        with pytest.raises(FinalityNotConfirmed):
            store.transition_payment(pid, PaymentState.SETTLED, actor="t")
        # Fresh finality evidence re-arms settlement.
        store.mark_finality_confirmed(
            pid, block_number=101, block_hash=BH2, confirmations=5
        )
        store.transition_payment(pid, PaymentState.SETTLED, actor="t")
        assert store.get(pid).state == PaymentState.SETTLED

    def test_direct_dispute_transition_clears_flag(self, store: PaymentLedgerStore):
        pid = _settled_onchain(store, "w72-3")
        store.transition_payment(
            pid, PaymentState.DISPUTED, actor="t", reason="manual dispute probe"
        )
        assert store.get(pid).finality_confirmed is False

    def test_cas_dispute_transition_clears_flag(self, store: PaymentLedgerStore):
        pid = _settled_onchain(store, "w72-4")
        applied, record = store.transition_payment_if_state(
            pid, from_state=PaymentState.SETTLED, to_state=PaymentState.DISPUTED,
            actor="t", reason="cas dispute probe",
        )
        assert applied is True
        assert record.finality_confirmed is False
        assert store.get(pid).finality_confirmed is False

    def test_non_dispute_transitions_keep_flag(self, store: PaymentLedgerStore):
        # Clearing is scoped to disputes: a normal VERIFIED->AWAITING_FINALITY
        # hop must NOT drop a live confirmation.
        rec: PaymentRecord = from_x402_challenge({
            "challenge_id": "w72-5", "status": "fulfilled", "tx_hash": TX_CANON,
            "payer_wallet": "0xPayer", "amount_atomic": 10**18,
            "amount_display": 1.0, "currency": "USDC",
        })
        store.ingest(rec)
        pid = rec.payment_id
        store.mark_finality_confirmed(
            pid, block_number=100, block_hash=BH1, confirmations=5
        )
        store.transition_payment(pid, PaymentState.AWAITING_FINALITY, actor="t")
        assert store.get(pid).finality_confirmed is True
