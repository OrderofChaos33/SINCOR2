"""WP4 tests: auction settlement reconciliation scaffolding (D4).

Off-chain only. No on-chain anchoring, no broadcasts, no funding.
The reconciler stores the left side of the v2 reconciliation comparisons;
onchain_tx_hash must stay None until v2.
"""

import pytest

from sincor2.payments.reconciliation import SettlementRecord, SettlementReconciler


def _make_record(**overrides):
    base = dict(
        record_id="rec-1",
        auction_id="auc-1",
        task_id="task-1",
        assigned_worker_id="worker-1",
        assigned_worker_wallet="0x" + "aa" * 20,
        asset="USDC",
        amount_atomic=2_000_000,
        beneficiary_wallet="0x" + "bb" * 20,
        platform_fee_atomic=100_000,
    )
    base.update(overrides)
    return SettlementRecord(**base)


def test_record_assignment():
    r = SettlementReconciler()
    rec = r.record_assignment(_make_record())
    assert rec.status == "assigned"
    assert r.get("rec-1") == rec


def test_duplicate_record_id_rejected():
    r = SettlementReconciler()
    r.record_assignment(_make_record())
    with pytest.raises(ValueError, match="duplicate"):
        r.record_assignment(_make_record())


def test_settle_offchain_transition():
    r = SettlementReconciler()
    r.record_assignment(_make_record())
    settled = r.mark_settled_offchain("rec-1")
    assert settled.status == "settled_offchain"
    assert settled.settled_at is not None


def test_double_settle_rejected():
    r = SettlementReconciler()
    r.record_assignment(_make_record())
    r.mark_settled_offchain("rec-1")
    with pytest.raises(ValueError, match="cannot settle"):
        r.mark_settled_offchain("rec-1")


def test_fiat_asset_rejected():
    with pytest.raises(ValueError, match="USDC\\|AXM"):
        _make_record(asset="USD")


def test_onchain_tx_hash_forbidden_until_v2():
    """D4: on-chain anchoring deferred. The field must stay None."""
    with pytest.raises(ValueError, match="deferred to v2"):
        _make_record(onchain_tx_hash="0x" + "cc" * 32)


def test_reconciliation_view_exposes_v2_expectations():
    r = SettlementReconciler()
    r.record_assignment(_make_record())
    r.mark_settled_offchain("rec-1")
    view = r.reconciliation_view("rec-1")
    assert view["expected_onchain_winner"] == "0x" + "aa" * 20
    assert view["expected_funded_amount_atomic"] == 2_000_000
    assert view["expected_beneficiary"] == "0x" + "bb" * 20
    assert view["expected_platform_fee_atomic"] == 100_000
    assert view["asset"] == "USDC"
    assert view["onchain_anchored"] is False


def test_pending_reconciliation_lists_unsettled():
    r = SettlementReconciler()
    r.record_assignment(_make_record(record_id="a"))
    r.record_assignment(_make_record(record_id="b"))
    r.mark_settled_offchain("a")
    pending = r.pending_reconciliation()
    assert [x.record_id for x in pending] == ["a"]


def test_unknown_record_returns_none():
    r = SettlementReconciler()
    assert r.get("nope") is None
    assert r.reconciliation_view("nope") is None


def test_record_is_immutable():
    rec = _make_record()
    with pytest.raises(Exception):
        rec.status = "anchored_onchain"  # type: ignore[misc]
