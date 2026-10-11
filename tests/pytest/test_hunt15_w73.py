"""Tests for W-73 (red-team hunt #15): ingest() enforces the evidence guards.

Before the fix, ``_enforce_evidence_guards`` ran only in the transition
methods, so a hand-built record already in a verified-or-later state with
no tx hash ingested cleanly — defeating the "onchain providers cannot
enter verified-or-later states without a hash" invariant at the ingest
seam.

Run: PYTHONPATH=src:. <venv>/bin/python -m pytest tests/pytest/test_hunt15_w73.py --noconftest -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sincor2.payment_ledger import (
    MalformedTxHashError,
    MissingTxHashError,
    PaymentLedgerStore,
    PaymentRecord,
    PaymentState,
)


@pytest.fixture()
def store(tmp_path: Path) -> PaymentLedgerStore:
    return PaymentLedgerStore(tmp_path / "ledger.db")


_ONCHAIN_PROVIDERS = ["x402", "a2a", "manual", "checkout", "legacy"]
_VERIFIED_OR_LATER = [
    PaymentState.VERIFIED,
    PaymentState.AWAITING_FINALITY,
    PaymentState.SETTLED,
    PaymentState.FULFILLED,
]


# ---------------------------------------------------------------------------
# W-73 core: hand-built records in verified-or-later states without a hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", _ONCHAIN_PROVIDERS)
@pytest.mark.parametrize("state", _VERIFIED_OR_LATER)
def test_onchain_verified_or_later_without_hash_rejected_at_ingest(
    store: PaymentLedgerStore, provider: str, state: PaymentState
) -> None:
    """A hand-built onchain-provider record already in a verified-or-later
    state with no tx hash must be REJECTED at ingest (MissingTxHashError)."""
    rec = PaymentRecord(
        payment_id=f"w73-{provider}-{state.value}",
        owner="0xPayer",
        amount_atomic=10**18,
        amount_display=1.0,
        currency="USDC",
        provider=provider,
        provider_evidence={},  # no tx_hash at all
        state=state,
    )
    with pytest.raises(MissingTxHashError):
        store.ingest(rec)
    assert store.count() == 0


def test_replayed_history_cannot_smuggle_hashless_verified_record(
    store: PaymentLedgerStore,
) -> None:
    """Replaying in-memory transitions (which do not enforce guards) then
    ingesting must still fail closed — the same attacker shape as the
    hand-built constructor state."""
    rec = PaymentRecord(
        payment_id="w73-replay",
        owner="0xPayer",
        amount_atomic=10**18,
        amount_display=1.0,
        currency="USDC",
        provider="x402",
        provider_evidence={},
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="t", reason="t")
    rec.transition(PaymentState.VERIFIED, actor="t", reason="t")
    with pytest.raises(MissingTxHashError):
        store.ingest(rec)
    assert store.count() == 0


def test_pre_verified_states_still_ingest_without_hash(
    store: PaymentLedgerStore,
) -> None:
    """Records not yet in a verified-or-later state may still be ingested
    without a hash (the hash is enforced when they try to *enter* verified
    states via the transition methods) — no behavior change here."""
    rec = PaymentRecord(
        payment_id="w73-pending",
        owner="0xPayer",
        amount_atomic=10**18,
        amount_display=1.0,
        currency="USDC",
        provider="x402",
        provider_evidence={},
        state=PaymentState.PENDING_VERIFICATION,
    )
    store.ingest(rec)
    assert store.count() == 1
    with pytest.raises(MissingTxHashError):
        store.transition_payment(
            "w73-pending", PaymentState.VERIFIED, actor="t", reason="t"
        )


def test_onchain_record_with_hash_still_ingests(store: PaymentLedgerStore) -> None:
    """A verified onchain record WITH a canonical tx hash is unaffected."""
    rec = PaymentRecord(
        payment_id="w73-withhash",
        owner="0xPayer",
        amount_atomic=10**18,
        amount_display=1.0,
        currency="USDC",
        provider="x402",
        provider_evidence={"tx_hash": "0x" + "ab" * 32},
        state=PaymentState.VERIFIED,
    )
    store.ingest(rec)
    assert store.get("w73-withhash").state == PaymentState.VERIFIED


# ---------------------------------------------------------------------------
# No regression: the 11 malformed-hash rejections at ingest keep working
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "0x", "0x" + "zz" * 32, "0x" + "ab" * 31, "not-a-hash", "\t\n"],
)
def test_malformed_hash_strings_still_rejected_at_ingest(
    store: PaymentLedgerStore, bad: str
) -> None:
    rec = PaymentRecord(
        payment_id=f"w73-mal-{abs(hash(bad)) % 10**6}",
        owner="0xPayer",
        amount_atomic=10**18,
        amount_display=1.0,
        currency="USDC",
        provider="x402",
        provider_evidence={"tx_hash": bad},
        state=PaymentState.VERIFIED,
    )
    with pytest.raises(MalformedTxHashError):
        store.ingest(rec)
    assert store.count() == 0


@pytest.mark.parametrize(
    "bad", [0, False, b"0x" + b"ab" * 32, ["0x" + "ab" * 32]]
)
def test_malformed_hash_non_strings_still_rejected_at_ingest(
    store: PaymentLedgerStore, bad: object
) -> None:
    rec = PaymentRecord(
        payment_id=f"w73-mal-ns-{abs(hash(repr(bad))) % 10**6}",
        owner="0xPayer",
        amount_atomic=10**18,
        amount_display=1.0,
        currency="USDC",
        provider="x402",
        provider_evidence={"tx_hash": bad},
        state=PaymentState.VERIFIED,
    )
    with pytest.raises(MalformedTxHashError):
        store.ingest(rec)
    assert store.count() == 0


# ---------------------------------------------------------------------------
# No regression: offchain (stripe) records with no tx hash still ingest
# ---------------------------------------------------------------------------


def test_stripe_without_hash_still_ingests_and_settles(
    store: PaymentLedgerStore,
) -> None:
    """Stripe is not an onchain provider: None tx hash must be accepted at
    ingest in any state, and settlement must keep working."""
    rec = PaymentRecord(
        payment_id="w73-stripe",
        owner="buyer@example.com",
        amount_atomic=4900,
        amount_display=49.0,
        currency="USD",
        provider="stripe",
        provider_evidence={},  # None tx_hash by design for fiat
        state=PaymentState.VERIFIED,
    )
    store.ingest(rec)
    store.transition_payment(
        "w73-stripe", PaymentState.SETTLED, actor="t", reason="t"
    )
    assert store.get("w73-stripe").state == PaymentState.SETTLED


def test_stripe_settled_ingest_unaffected(store: PaymentLedgerStore) -> None:
    """A stripe record mapped straight to SETTLED (as from_stripe_event
    does for completed fiat) ingests fine without any tx hash."""
    rec = PaymentRecord(
        payment_id="w73-stripe-settled",
        owner="buyer@example.com",
        amount_atomic=4900,
        amount_display=49.0,
        currency="USD",
        provider="stripe",
        provider_evidence={},
        state=PaymentState.SETTLED,
    )
    store.ingest(rec)
    assert store.get("w73-stripe-settled").state == PaymentState.SETTLED
