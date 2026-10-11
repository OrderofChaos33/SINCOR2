"""Regression tests for red-team Hunt 15: W-70, W-71.

W-70 — cross-provider tx_hash double-credit: the dedup key is GLOBAL
across providers.  The same canonical onchain tx ingested under a second
provider label is a duplicate, not a second payment.

W-71 — provider string canonicalization: the provider label is
strip+lowercased and whitelist-validated at the ingest choke point, so no
spelling variant can dodge the tx-hash-required guard or the dedup key,
and unknown providers are rejected fail-closed.

Self-contained: temp sqlite files, no app, no network.
Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_hunt15_w70w71.py -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from sincor2.payment_ledger import (
    DuplicatePaymentError,
    MissingTxHashError,
    PaymentLedgerStore,
    PaymentNotFound,
    PaymentRecord,
    PaymentState,
    UnknownProviderError,
    canonicalize_provider,
    from_x402_challenge,
    normalize_tx_hash,
)


TX_CANON = "0x" + "ab" * 32
TX_UPPER = "0X" + "AB" * 32
TX_OTHER = "0x" + "cd" * 32


@pytest.fixture()
def store(tmp_path: Path) -> PaymentLedgerStore:
    return PaymentLedgerStore(tmp_path / "ledger.db")


def _rec(
    cid: str, tx_hash: Any, provider: str = "x402", status: str = "fulfilled"
) -> PaymentRecord:
    rec = from_x402_challenge({
        "challenge_id": cid, "status": status, "tx_hash": tx_hash,
        "payer_wallet": "0xPayer", "amount_atomic": 10**18,
        "amount_display": 1.0, "currency": "USDC",
    })
    rec.provider = provider
    return rec


# ---------------------------------------------------------------------------
# canonicalize_provider unit tests
# ---------------------------------------------------------------------------


class TestCanonicalizeProvider:
    def test_lowercases(self):
        assert canonicalize_provider("X402") == "x402"

    def test_strips_whitespace(self):
        assert canonicalize_provider("  a2a\t") == "a2a"

    def test_all_whitelisted_pass_through(self):
        for p in ("x402", "a2a", "manual", "checkout", "legacy", "stripe"):
            assert canonicalize_provider(p) == p

    def test_unknown_rejected(self):
        with pytest.raises(UnknownProviderError):
            canonicalize_provider("evil")

    def test_empty_rejected(self):
        with pytest.raises(UnknownProviderError):
            canonicalize_provider("   ")

    def test_non_string_rejected(self):
        with pytest.raises(UnknownProviderError):
            canonicalize_provider(None)


# ---------------------------------------------------------------------------
# W-70 — global dedup key
# ---------------------------------------------------------------------------


class TestW70GlobalDedup:
    def test_same_tx_second_provider_rejected(self, store: PaymentLedgerStore):
        store.ingest(_rec("w70-1", TX_CANON, provider="a2a"))
        with pytest.raises(DuplicatePaymentError):
            store.ingest(_rec("w70-2", TX_CANON, provider="x402"))

    def test_same_tx_spelling_variant_second_provider_rejected(
        self, store: PaymentLedgerStore
    ):
        store.ingest(_rec("w70-3", TX_CANON, provider="a2a"))
        with pytest.raises(DuplicatePaymentError):
            store.ingest(_rec("w70-4", TX_UPPER, provider="x402"))

    def test_distinct_txs_distinct_providers_ok(self, store: PaymentLedgerStore):
        store.ingest(_rec("w70-5", TX_CANON, provider="a2a"))
        store.ingest(_rec("w70-6", TX_OTHER, provider="x402"))
        assert store.get("x402-w70-5").provider == "a2a"
        assert store.get("x402-w70-6").provider == "x402"

    def test_find_by_tx_hash_is_global(self, store: PaymentLedgerStore):
        store.ingest(_rec("w70-7", TX_CANON, provider="a2a"))
        found = store.find_by_tx_hash("x402", TX_CANON)
        assert found is not None
        assert found.payment_id == "x402-w70-7"
        assert found.provider == "a2a"

    def test_find_by_tx_hash_validates_provider(self, store: PaymentLedgerStore):
        with pytest.raises(UnknownProviderError):
            store.find_by_tx_hash("evil", TX_CANON)

    def test_migration_quarantines_cross_provider_collision(
        self, tmp_path: Path
    ):
        # Simulate a pre-W-70 database where the old (provider, norm) key
        # allowed the same canonical tx under two provider labels.
        db = tmp_path / "legacy.db"
        s = PaymentLedgerStore(db)
        s.ingest(_rec("w70-8", TX_CANON, provider="a2a"))
        s.ingest(_rec("w70-9", TX_OTHER, provider="x402"))
        # Raw-SQL second claim of the same tx under another provider,
        # bypassing ingest (what the old schema permitted).  Drop the new
        # global index first so the legacy collision can be staged.
        conn = sqlite3.connect(str(db))
        conn.execute("DROP INDEX idx_payments_tx_hash_norm_global")
        conn.execute(
            "INSERT INTO payments (payment_id, owner, amount_atomic,"
            " amount_display, currency, provider, provider_evidence,"
            " entitlements, refunds, state, version, tx_hash_norm,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "x402-w70-evil", "0xPayer", 10**18, 1.0, "USDC", "x402",
                '{"tx_hash": "%s"}' % TX_CANON, "{}", "[]",
                PaymentState.PENDING_VERIFICATION.value, 1,
                normalize_tx_hash(TX_CANON),
                # Later than the keeper: this is the double-credit row.
                "2026-10-12T00:00:01", "2026-10-12T00:00:01",
            ),
        )
        conn.commit()
        conn.close()
        # Reopen: the W-70 sweep must quarantine the later collision.
        s2 = PaymentLedgerStore(db)
        evil = s2.get("x402-w70-evil")
        assert evil.state == PaymentState.QUARANTINED
        keeper = s2.get("x402-w70-8")
        assert keeper.state == PaymentState.VERIFIED
        # The global unique index now exists and enforces the invariant.
        idx = [
            r[0] for r in sqlite3.connect(str(db)).execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        ]
        assert "idx_payments_tx_hash_norm_global" in idx
        assert "idx_payments_provider_tx_norm" not in idx


# ---------------------------------------------------------------------------
# W-71 — provider canonicalization at ingest
# ---------------------------------------------------------------------------


class TestW71ProviderCanonicalization:
    def test_spelling_variant_canonicalized(self, store: PaymentLedgerStore):
        store.ingest(_rec("w71-1", TX_CANON, provider="X402"))
        assert store.get("x402-w71-1").provider == "x402"

    def test_whitespace_variant_canonicalized(self, store: PaymentLedgerStore):
        store.ingest(_rec("w71-2", TX_CANON, provider="  A2A "))
        assert store.get("x402-w71-2").provider == "a2a"

    def test_unknown_provider_rejected_fail_closed(
        self, store: PaymentLedgerStore
    ):
        with pytest.raises(UnknownProviderError):
            store.ingest(_rec("w71-3", TX_CANON, provider="evil"))
        with pytest.raises(PaymentNotFound):
            store.get("x402-w71-3")

    def test_hash_required_guard_applies_to_spelling_variant(
        self, store: PaymentLedgerStore
    ):
        # W-71 P2: provider "X402" with zero tx_hash must NOT walk to
        # VERIFIED — the guard must see the canonical "x402".
        # (status "pending": the mapper quarantines fulfilled-without-tx
        # by design; the guard under test is the ledger's.)
        rec = _rec("w71-4", None, provider="X402", status="pending")
        store.ingest(rec)
        with pytest.raises(MissingTxHashError):
            store.transition_payment(
                rec.payment_id, PaymentState.VERIFIED,
                actor="test", reason="w71 guard probe",
            )

    def test_offchain_provider_unaffected(self, store: PaymentLedgerStore):
        store.ingest(_rec("w71-5", None, provider="STRIPE", status="pending"))
        rec = store.get("x402-w71-5")
        assert rec.provider == "stripe"
        store.transition_payment(
            rec.payment_id, PaymentState.VERIFIED,
            actor="test", reason="stripe has no hash by design",
        )
