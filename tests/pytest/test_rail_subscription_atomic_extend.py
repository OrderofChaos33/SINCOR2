"""W-105 (hunt-29): subscription settle must apply its period EXACTLY ONCE.

The bug: settle_renewal applied the 30-day period via _apply_period
(which committed), and only AFTERWARDS wrote extended=1 on the settle
row in a separate transaction. A crash between the two commits — or a
concurrent second settle arriving inside that window — re-applied the
period: one payment, two periods.

The fix makes claim -> apply -> flag atomic: the subscription upsert
and the settle row's extended flag + applied-period stamp commit in a
single BEGIN IMMEDIATE transaction, and the payment finalize is
claimed under the same write lock so concurrent settles cannot append
duplicate platform-payment records for one tx.

These tests are fresh (not imported from the red-team PoCs) and drive
the real rail_subscriptions claim/apply/flag logic; only the external
boundaries (on-chain verify, finalize append, money gate, platform
sync) are stubbed, mirroring how the PoCs isolated the flaw.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest

import sincor2.rail_subscriptions as rs
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

WALLET = "0x" + "11" * 20
TX = "0x" + "ab" * 32


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def atomic_env(tmp_path, monkeypatch):
    """Hermetic stores + stubbed external boundaries (real settle logic)."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    x402.init_x402_db()

    calls = {"verify": 0, "finalize": 0, "sync": 0}

    def _fake_verify(cid, tx, **kw):
        calls["verify"] += 1
        return {"ok": True, "status": "fulfilled", "challenge_id": cid,
                "resource_id": "rail-sub-starter", "tx_hash": tx,
                "payer_wallet": WALLET, "amount_atomic": 297_000_000,
                "amount_display": 297.0}

    def _fake_finalize(result):
        calls["finalize"] += 1
        return {}

    def _fake_sync(**kw):
        calls["sync"] += 1

    monkeypatch.setattr(x402, "verify_challenge", _fake_verify)
    monkeypatch.setattr(x402, "finalize_challenge_payment", _fake_finalize)
    monkeypatch.setattr(rs, "_sub_money_gate_allows", lambda **kw: True)
    monkeypatch.setattr(rs, "_sync_platform", _fake_sync)
    return {"tmp": tmp_path, "calls": calls}


def _issue():
    ch = rs.issue_renewal_challenge(WALLET, "starter")
    return ch["challenge_id"]


def _settle_row(tmp_path, settle_id):
    conn = sqlite3.connect(str(tmp_path / "revenue.db"))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM rail_sub_settles WHERE settle_id=?",
            (settle_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _reset_extended_flag(tmp_path, settle_id):
    """Simulate the crash window: extension committed, flag write lost."""
    conn = sqlite3.connect(str(tmp_path / "revenue.db"))
    try:
        conn.execute(
            "UPDATE rail_sub_settles SET extended=0 WHERE settle_id=?",
            (settle_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# crash window: apply committed, extended flag lost -> retry must NOT re-apply
# ---------------------------------------------------------------------------

def test_crash_after_apply_before_flag_write_does_not_double_extend(
        atomic_env):
    cid = _issue()
    settle_id = f"railsub-{cid}"

    first = rs.settle_renewal(cid, TX, WALLET)
    assert first["ok"] is True
    assert first["status"] == "renewed"
    s1 = rs.subscription_status(WALLET)
    assert s1["period_index"] == 1

    # The applied-period stamp was committed atomically with the extension.
    row = _settle_row(atomic_env["tmp"], settle_id)
    assert row["extended"] == 1
    assert row["applied_period_end"] == s1["period_end"]
    assert row["applied_period_index"] == 1

    # Simulate the crash: the extension stands, the flag write is lost.
    _reset_extended_flag(atomic_env["tmp"], settle_id)

    retry = rs.settle_renewal(cid, TX, WALLET)
    assert retry["ok"] is True
    assert retry["status"] == "already_renewed"
    assert retry["duplicate"] is True

    s2 = rs.subscription_status(WALLET)
    assert s2["period_index"] == 1  # exactly one period total
    assert s2["period_end"] == s1["period_end"]

    # The retry repaired the flag from the applied stamp, and neither the
    # payment finalize nor the platform sync ran a second time.
    row = _settle_row(atomic_env["tmp"], settle_id)
    assert row["extended"] == 1
    assert atomic_env["calls"]["finalize"] == 1
    assert atomic_env["calls"]["sync"] == 1


# ---------------------------------------------------------------------------
# concurrency: N simultaneous settles of one challenge -> one period only
# ---------------------------------------------------------------------------

def test_concurrent_settles_apply_exactly_one_period(atomic_env, monkeypatch):
    # Widen the verified -> extended window the way the hunt PoC did: a
    # slow money gate gives every racer time to enter the pipeline.
    def _slow_gate(**kw):
        time.sleep(0.3)
        return True

    monkeypatch.setattr(rs, "_sub_money_gate_allows", _slow_gate)

    cid = _issue()
    n_threads = 4
    barrier = threading.Barrier(n_threads)
    results = []
    errors = []

    def _run():
        try:
            barrier.wait(timeout=15)
            results.append(rs.settle_renewal(cid, TX, WALLET))
        except Exception as exc:  # surfaced by the assertions below
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    assert len(results) == n_threads
    assert all(r.get("ok") is True for r in results), results
    statuses = [r.get("status") for r in results]
    assert statuses.count("renewed") == 1, statuses
    assert statuses.count("already_renewed") == n_threads - 1, statuses

    status = rs.subscription_status(WALLET)
    assert status["period_index"] == 1
    end = datetime.fromisoformat(status["period_end"])
    delta_days = (end - datetime.now(timezone.utc)).total_seconds() / 86400
    assert 29.0 < delta_days <= 30.1, status["period_end"]

    # Corollary (same root cause): exactly one payment finalize and one
    # platform sync for the one tx, despite four racing settles.
    assert atomic_env["calls"]["finalize"] == 1
    assert atomic_env["calls"]["sync"] == 1


# ---------------------------------------------------------------------------
# sequential duplicate: existing already_renewed behaviour preserved
# ---------------------------------------------------------------------------

def test_sequential_duplicate_settle_still_acks_already_renewed(atomic_env):
    cid = _issue()
    first = rs.settle_renewal(cid, TX, WALLET)
    assert first["ok"] is True
    assert first["status"] == "renewed"
    first_end = first["subscription"]["period_end"]

    second = rs.settle_renewal(cid, TX, WALLET)
    assert second["ok"] is True
    assert second["status"] == "already_renewed"
    assert second["duplicate"] is True
    assert second["subscription"]["period_end"] == first_end
    assert second["subscription"]["period_index"] == 1

    assert atomic_env["calls"]["finalize"] == 1
    assert atomic_env["calls"]["sync"] == 1
