"""Regression tests for red-team Hunt 12 batch A: W-53, W-54, W-55, W-56.

Self-contained: temp sqlite files, fake chain, no app, no network.
Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_rt12_batch_a.py -q
"""

from __future__ import annotations

import sqlite3
import threading
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
    DuplicatePaymentError,
    IllegalPaymentTransition,
    MalformedTxHashError,
    MissingTxHashError,
    PaymentLedgerStore,
    PaymentRecord,
    PaymentState,
    from_x402_challenge,
    normalize_tx_hash,
)


TX_CANON = "0x" + "ab" * 32
TX_UPPER = "0X" + "AB" * 32
TX_BARE = "ab" * 32
TX_MIXED_WS = "  0xAbAb" + "ab" * 30 + "  "


@pytest.fixture()
def store(tmp_path: Path) -> PaymentLedgerStore:
    return PaymentLedgerStore(tmp_path / "ledger.db")


@pytest.fixture()
def hooks():
    clear_entitlement_hooks()
    grants: list[str] = []
    revokes: list[str] = []
    register_entitlement_granter(lambda rec: grants.append(rec.payment_id) or {"g": rec.payment_id})
    register_entitlement_revoker(lambda rec: revokes.append(rec.payment_id) or {"r": rec.payment_id})
    yield grants, revokes
    clear_entitlement_hooks()


def _x402(cid: str, tx_hash: Any, provider: str = "x402") -> PaymentRecord:
    rec = from_x402_challenge({
        "challenge_id": cid, "status": "fulfilled", "tx_hash": tx_hash,
        "payer_wallet": "0xPayer", "amount_atomic": 10**18,
        "amount_display": 1.0, "currency": "USDC",
    })
    rec.provider = provider
    return rec


class FakeChain:
    def __init__(self) -> None:
        self.receipts: dict[str, dict] = {}
        self.blocks: dict[int, str] = {}
        self.head = 0

    def add_tx(self, h: str, bn: int, bh: str, status: int = 1) -> None:
        self.receipts[h] = {"blockNumber": bn, "blockHash": bh, "status": status}
        self.blocks[bn] = bh

    def get_receipt(self, h: str) -> Optional[Mapping[str, Any]]:
        return self.receipts.get(h)

    def get_block(self, n: int) -> Optional[Mapping[str, Any]]:
        h = self.blocks.get(n)
        return {"number": n, "hash": h} if h is not None else None

    def head_height(self) -> int:
        return self.head


# ---------------------------------------------------------------------------
# W-53 — tx_hash canonicalization / dedup bypass
# ---------------------------------------------------------------------------


class TestW53CanonicalDedup:
    def test_variants_of_same_tx_rejected(self, store: PaymentLedgerStore):
        store.ingest(_x402("w53-1", TX_CANON))
        for cid, variant in (("w53-2", TX_UPPER), ("w53-3", TX_BARE), ("w53-4", TX_MIXED_WS)):
            with pytest.raises(DuplicatePaymentError):
                store.ingest(_x402(cid, variant))
        assert store.count() == 1

    def test_exact_replay_still_rejected(self, store: PaymentLedgerStore):
        store.ingest(_x402("w53-1", TX_CANON))
        with pytest.raises(DuplicatePaymentError):
            store.ingest(_x402("w53-2", TX_CANON))

    def test_find_by_tx_hash_canonicalizes_probe(self, store: PaymentLedgerStore):
        store.ingest(_x402("w53-1", TX_CANON))
        for probe in (TX_CANON, TX_UPPER, TX_BARE, TX_MIXED_WS, "0x" + "AB" * 32):
            found = store.find_by_tx_hash("x402", probe)
            assert found is not None and found.payment_id == "x402-w53-1", probe

    def test_stored_hash_is_canonical(self, store: PaymentLedgerStore):
        store.ingest(_x402("w53-1", TX_UPPER))
        rec = store.get("x402-w53-1")
        assert rec.provider_evidence["tx_hash"] == TX_CANON
        row = sqlite3.connect(str(store.db_path)).execute(
            "SELECT tx_hash, tx_hash_norm FROM payments WHERE payment_id='x402-w53-1'"
        ).fetchone()
        assert row[0] == TX_CANON and row[1] == TX_CANON

    def test_unique_index_on_normalized_column(self, store: PaymentLedgerStore):
        # W-70: the dedup key is GLOBAL across providers — the index must
        # be UNIQUE on tx_hash_norm alone, with no provider column.
        idx = sqlite3.connect(str(store.db_path)).execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_payments_tx_hash_norm_global'"
        ).fetchone()[0]
        assert "UNIQUE" in idx and "tx_hash_norm" in idx
        assert "provider" not in idx
        legacy = sqlite3.connect(str(store.db_path)).execute(
            "SELECT name FROM sqlite_master WHERE name='idx_payments_provider_tx_norm'"
        ).fetchone()
        assert legacy is None

    def test_normalize_tx_hash_unit(self):
        assert normalize_tx_hash(TX_UPPER) == TX_CANON
        assert normalize_tx_hash(TX_BARE) == TX_CANON
        assert normalize_tx_hash(TX_MIXED_WS) == TX_CANON
        for bad in ("", "   ", None, 0, b"0x" + b"ab" * 32, "0x", "0x" + "zz" * 32,
                    "0x" + "ab" * 31, "0x" + "ab" * 33, "not-a-hash", "0x0x" + "ab" * 32):
            with pytest.raises(MalformedTxHashError):
                normalize_tx_hash(bad)

    def test_double_credit_end_to_end_one_grant(self, store: PaymentLedgerStore, hooks):
        grants, _ = hooks
        policy = FinalityPolicy(thresholds={8453: 1})
        chain = FakeChain()
        chain.add_tx(TX_CANON, 100, "0x" + "b1" * 32)
        chain.head = 100
        store.ingest(_x402("w53-1", TX_CANON))
        variants_ingested = 0
        for cid, variant in (("w53-2", TX_UPPER), ("w53-3", TX_BARE)):
            try:
                store.ingest(_x402(cid, variant))
                variants_ingested += 1
            except DuplicatePaymentError:
                pass
        assert variants_ingested == 0
        res = check_finality(store, "x402-w53-1", chain, policy)
        assert res.status == "settled"
        assert grants == ["x402-w53-1"]  # exactly one entitlement for one tx

    def test_legacy_db_migration_quarantines_canonical_collision(self, tmp_path: Path):
        # Build a pre-W-53 database with the old schema and two spellings of
        # the same tx, then open it with the new store: the later row must
        # be quarantined fail-closed, the earliest keeps the dedup key.
        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE payments (payment_id TEXT PRIMARY KEY, owner TEXT NOT NULL,"
            " amount_atomic INTEGER NOT NULL, amount_display REAL NOT NULL,"
            " currency TEXT NOT NULL, provider TEXT NOT NULL,"
            " provider_evidence TEXT NOT NULL DEFAULT '{}', entitlements TEXT NOT NULL DEFAULT '{}',"
            " refunds TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,"
            " version INTEGER NOT NULL DEFAULT 0, block_number INTEGER, block_hash TEXT,"
            " confirmations INTEGER, finality_confirmed INTEGER NOT NULL DEFAULT 0,"
            " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, tx_hash TEXT)"
        )
        conn.execute(
            "CREATE TABLE payment_history (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " payment_id TEXT NOT NULL, from_state TEXT NOT NULL, to_state TEXT NOT NULL,"
            " ts TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '')"
        )
        for pid, h, ts in (("x402-old-1", TX_CANON, "2026-01-01T00:00:00"),
                           ("x402-old-2", TX_UPPER, "2026-01-02T00:00:00")):
            conn.execute(
                "INSERT INTO payments (payment_id, owner, amount_atomic, amount_display,"
                " currency, provider, state, created_at, updated_at, tx_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, "o", 1, 1.0, "USDC", "x402", "verified", ts, ts, h),
            )
        conn.commit()
        conn.close()

        store = PaymentLedgerStore(db)
        assert store.get("x402-old-1").state == PaymentState.VERIFIED
        dup = store.get("x402-old-2")
        assert dup.state == PaymentState.QUARANTINED
        assert dup.history[-1].actor == "rt12-migration"
        # The survivor still dedups: a fresh canonical ingest is rejected.
        with pytest.raises(DuplicatePaymentError):
            store.ingest(_x402("old-3", TX_BARE))
        # ...while the quarantined row cannot settle.
        with pytest.raises(IllegalPaymentTransition):
            store.transition_payment("x402-old-2", PaymentState.SETTLED,
                                     actor="t", reason="t", allow_finality_bypass=True)


# ---------------------------------------------------------------------------
# W-54 — concurrent check_finality pollers must never crash
# ---------------------------------------------------------------------------


def _hammer(store: PaymentLedgerStore, pids: list[str], chain: FakeChain,
            policy: FinalityPolicy, n: int = 16):
    errors: list[tuple] = []
    results: list[tuple] = []
    barrier = threading.Barrier(n)

    def worker(i: int):
        try:
            barrier.wait(timeout=15)
            r = check_finality(store, pids[i % len(pids)], chain, policy, actor=f"poller-{i}")
            results.append((i, r.status))
        except Exception as exc:  # noqa: BLE001
            errors.append((i, type(exc).__name__, str(exc)[:150]))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "poller threads hung"
    return errors, results


class TestW54ConcurrentPollers:
    def test_onchain_settle_race_no_crash_exactly_once(
        self, store: PaymentLedgerStore, hooks
    ):
        grants, _ = hooks
        policy = FinalityPolicy(thresholds={8453: 1})
        chain = FakeChain()
        chain.add_tx(TX_CANON, 100, "0x" + "b1" * 32)
        chain.head = 100
        store.ingest(_x402("w54-1", TX_CANON))
        errors, results = _hammer(store, ["x402-w54-1"], chain, policy, n=16)
        assert errors == [], errors[:5]
        assert len(results) == 16
        assert grants == ["x402-w54-1"]  # exactly one entitlement grant
        assert store.get("x402-w54-1").state == PaymentState.SETTLED
        statuses = {s for _, s in results}
        assert statuses <= {"settled", "already_settled"}

    def test_reverted_receipt_race_no_crash(self, store: PaymentLedgerStore, hooks):
        grants, _ = hooks
        policy = FinalityPolicy(thresholds={8453: 1})
        chain = FakeChain()
        chain.add_tx(TX_CANON, 100, "0x" + "b1" * 32, status=0)
        chain.head = 100
        store.ingest(_x402("w54-2", TX_CANON))
        errors, results = _hammer(store, ["x402-w54-2"], chain, policy, n=16)
        assert errors == [], errors[:5]
        assert grants == []
        assert store.get("x402-w54-2").state == PaymentState.FAILED

    def test_offchain_settle_race_no_crash_exactly_once(
        self, store: PaymentLedgerStore, hooks
    ):
        grants, _ = hooks
        policy = FinalityPolicy(thresholds={8453: 1})
        chain = FakeChain()
        rec = PaymentRecord(
            payment_id="w54-off", owner="o", amount_atomic=100, amount_display=1.0,
            currency="USD", provider="stripe", provider_evidence={},
            state=PaymentState.CREATED,
        )
        store.ingest(rec)
        errors, results = _hammer(store, ["w54-off"], chain, policy, n=16)
        assert errors == [], errors[:5]
        assert grants == ["w54-off"]
        assert store.get("w54-off").state == PaymentState.SETTLED

    def test_reorg_race_single_dispute_single_revoke_single_alert(
        self, store: PaymentLedgerStore, hooks
    ):
        from sincor2.payment_ledger import list_alerts
        grants, revokes = hooks
        policy = FinalityPolicy(thresholds={8453: 1})

        class ReorgChain(FakeChain):
            reorged = False

            def get_block(self, n: int):
                if self.reorged:
                    return {"number": n, "hash": "0x" + "cc" * 32}
                return super().get_block(n)

        chain = ReorgChain()
        chain.add_tx(TX_CANON, 100, "0x" + "b1" * 32)
        chain.head = 100
        store.ingest(_x402("w54-3", TX_CANON))
        pid = "x402-w54-3"
        # Settle it first so pollers hit the settled fast-path reorg check.
        store.transition_payment(pid, PaymentState.AWAITING_FINALITY, actor="t", reason="t")
        store.mark_finality_confirmed(pid, block_number=100,
                                      block_hash="0x" + "b1" * 32, confirmations=5)
        store.transition_payment(pid, PaymentState.SETTLED, actor="t", reason="t",
                                 allow_finality_bypass=True)
        grants.clear()
        chain.reorged = True
        errors, results = _hammer(store, [pid], chain, policy, n=16)
        assert errors == [], errors[:5]
        assert store.get(pid).state == PaymentState.DISPUTED
        assert revokes == [pid]  # entitlements revoked exactly once
        alerts = [a for a in list_alerts(store, pid) if a.kind == "reorg_detected"]
        assert len(alerts) == 1  # exactly one durable alert
        statuses = {s for _, s in results}
        assert statuses <= {"disputed", "already_disputed"}

    def test_transition_payment_if_state_loser_gets_clean_result(
        self, store: PaymentLedgerStore
    ):
        rec = PaymentRecord(
            payment_id="w54-cas", owner="o", amount_atomic=1, amount_display=1.0,
            currency="USD", provider="stripe", provider_evidence={},
            state=PaymentState.CREATED,
        )
        store.ingest(rec)
        applied, fresh = store.transition_payment_if_state(
            "w54-cas", from_state=PaymentState.CREATED,
            to_state=PaymentState.PENDING_VERIFICATION, actor="a", reason="r")
        assert applied is True and fresh.state == PaymentState.PENDING_VERIFICATION
        # Loser: expected predecessor already moved on — no exception.
        applied, fresh = store.transition_payment_if_state(
            "w54-cas", from_state=PaymentState.CREATED,
            to_state=PaymentState.PENDING_VERIFICATION, actor="b", reason="r")
        assert applied is False
        assert fresh.state == PaymentState.PENDING_VERIFICATION
        # Already at target is also a clean loss (no double-apply).
        applied, fresh = store.transition_payment_if_state(
            "w54-cas", from_state=PaymentState.PENDING_VERIFICATION,
            to_state=PaymentState.VERIFIED, actor="a", reason="r")
        assert applied is True
        applied, _ = store.transition_payment_if_state(
            "w54-cas", from_state=PaymentState.PENDING_VERIFICATION,
            to_state=PaymentState.VERIFIED, actor="b", reason="r")
        assert applied is False


# ---------------------------------------------------------------------------
# W-55 — disputed payments cannot resurrect without fresh finality
# ---------------------------------------------------------------------------


class TestW55DisputedResurrection:
    def _settled_then_disputed(self, store, hooks):
        grants, _ = hooks
        policy = FinalityPolicy(thresholds={8453: 1})
        chain = FakeChain()
        chain.add_tx(TX_CANON, 100, "0x" + "b1" * 32)
        chain.head = 100
        store.ingest(_x402("w55-1", TX_CANON))
        pid = "x402-w55-1"
        res = check_finality(store, pid, chain, policy)
        assert res.status == "settled"
        assert grants == [pid]
        chain.blocks[100] = "0x" + "cc" * 32  # reorg: new block hash at height 100
        chain.receipts[TX_CANON]["blockHash"] = "0x" + "cc" * 32  # receipt follows the new fork
        res = check_finality(store, pid, chain, policy)
        assert res.status == "disputed"
        assert store.get(pid).state == PaymentState.DISPUTED
        return pid, policy, chain

    def test_disputed_cannot_settle_or_fulfill_or_refund_directly(
        self, store: PaymentLedgerStore, hooks
    ):
        pid, _, _ = self._settled_then_disputed(store, hooks)
        for target in (PaymentState.SETTLED, PaymentState.FULFILLED,
                       PaymentState.REFUNDED, PaymentState.QUARANTINED):
            with pytest.raises(IllegalPaymentTransition):
                store.transition_payment(pid, target, actor="evil",
                                         reason="shortcut",
                                         allow_finality_bypass=True)
        assert store.get(pid).state == PaymentState.DISPUTED

    def test_disputed_only_exits_to_pending_verification(
        self, store: PaymentLedgerStore, hooks
    ):
        pid, _, _ = self._settled_then_disputed(store, hooks)
        rec = store.transition_payment(pid, PaymentState.PENDING_VERIFICATION,
                                       actor="adjudicator",
                                       reason="dispute resolved; re-verify")
        assert rec.state == PaymentState.PENDING_VERIFICATION

    def test_full_reverification_then_settle_with_fresh_finality(
        self, store: PaymentLedgerStore, hooks
    ):
        grants, _ = hooks
        pid, policy, chain = self._settled_then_disputed(store, hooks)
        n_grants_before = len(grants)
        # Adjudicator re-admits; fresh finality observations are REQUIRED:
        # the new chain view (block 100 = 0xcc..) must be recorded before
        # check_finality will settle — the stale pre-reorg block hash alone
        # keeps the payment disputed.
        store.transition_payment(pid, PaymentState.PENDING_VERIFICATION,
                                 actor="adjudicator", reason="re-verify after reorg")
        store.record_chain_progress(pid, block_number=100,
                                    block_hash="0x" + "cc" * 32, confirmations=3)
        store.transition_payment(pid, PaymentState.VERIFIED,
                                 actor="adjudicator",
                                 reason="fresh observation: tx in block 100 (0xcc..)")
        res = check_finality(store, pid, chain, policy)
        assert res.status == "settled", res.detail
        assert store.get(pid).state == PaymentState.SETTLED
        # Fresh finality observations -> exactly one new entitlement grant.
        assert len(grants) == n_grants_before + 1

    def test_disputed_blocks_check_finality_shortcut(
        self, store: PaymentLedgerStore, hooks
    ):
        pid, policy, chain = self._settled_then_disputed(store, hooks)
        res = check_finality(store, pid, chain, policy)
        assert res.status == "disputed"
        assert store.get(pid).state == PaymentState.DISPUTED


# ---------------------------------------------------------------------------
# W-56 — empty / malformed tx_hash rejected fail-closed at ingestion
# ---------------------------------------------------------------------------


class TestW56EmptyTxHash:
    @pytest.mark.parametrize("bad", ["", "   ", "0x", "0x" + "zz" * 32,
                                     "0x" + "ab" * 31, "not-a-hash", "\t\n"])
    def test_malformed_hash_rejected_at_ingest(self, store: PaymentLedgerStore, bad: str):
        with pytest.raises(MalformedTxHashError):
            store.ingest(_x402(f"w56-{abs(hash(bad)) % 10**6}", bad))
        assert store.count() == 0

    def test_non_string_hash_rejected(self, store: PaymentLedgerStore):
        for bad in (0, False, b"0x" + b"ab" * 32, ["0x" + "ab" * 32]):
            with pytest.raises(MalformedTxHashError):
                store.ingest(_x402("w56-ns", bad))

    def test_empty_probe_never_matches(self, store: PaymentLedgerStore):
        store.ingest(_x402("w56-1", TX_CANON))
        assert store.find_by_tx_hash("x402", "") is None
        assert store.find_by_tx_hash("x402", "   ") is None
        assert store.find_by_tx_hash("x402", "junk") is None

    def test_offchain_payment_without_hash_still_settles(self, store: PaymentLedgerStore):
        # None tx_hash is legitimate for offchain providers: no dedup key,
        # no bypass — settlement works as before.
        rec = PaymentRecord(
            payment_id="w56-off", owner="o", amount_atomic=100, amount_display=1.0,
            currency="USD", provider="stripe", provider_evidence={},
            state=PaymentState.CREATED,
        )
        store.ingest(rec)
        store.transition_payment("w56-off", PaymentState.PENDING_VERIFICATION,
                                 actor="t", reason="t")
        store.transition_payment("w56-off", PaymentState.VERIFIED, actor="t", reason="t")
        store.transition_payment("w56-off", PaymentState.SETTLED, actor="t", reason="t")
        assert store.get("w56-off").state == PaymentState.SETTLED

    def test_onchain_payment_requires_hash_for_verified_states(
        self, store: PaymentLedgerStore
    ):
        # An onchain-provider payment with no tx hash cannot enter
        # verified-or-later states — fail closed (W-56 "prefer requiring it").
        rec = PaymentRecord(
            payment_id="w56-nohash", owner="o", amount_atomic=1, amount_display=1.0,
            currency="USDC", provider="x402", provider_evidence={},
            state=PaymentState.PENDING_VERIFICATION,
        )
        store.ingest(rec)  # ingest itself is fine: hash is simply absent
        with pytest.raises(MissingTxHashError):
            store.transition_payment("w56-nohash", PaymentState.VERIFIED,
                                     actor="t", reason="t")
        assert store.get("w56-nohash").state == PaymentState.PENDING_VERIFICATION

    def test_onchain_payment_requires_hash_via_conditional_path(
        self, store: PaymentLedgerStore
    ):
        rec = PaymentRecord(
            payment_id="w56-nohash2", owner="o", amount_atomic=1, amount_display=1.0,
            currency="USDC", provider="a2a", provider_evidence={},
            state=PaymentState.PENDING_VERIFICATION,
        )
        store.ingest(rec)
        with pytest.raises(MissingTxHashError):
            store.transition_payment_if_state(
                "w56-nohash2", from_state=PaymentState.PENDING_VERIFICATION,
                to_state=PaymentState.VERIFIED, actor="t", reason="t")
