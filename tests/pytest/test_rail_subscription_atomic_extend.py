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

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

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


# ===========================================================================
# Fix round (adversarial review residuals R1-R5)
# ===========================================================================

@pytest.fixture()
def real_finalize_env(tmp_path, monkeypatch):
    """Like atomic_env but the REAL x402 finalize + real JSONL stores run,
    so crash-retry duplicate appends are observable in the files."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    x402.init_x402_db()

    calls = {"verify": 0, "sync": 0}

    def _fake_verify(cid, tx, **kw):
        calls["verify"] += 1
        return {"ok": True, "status": "fulfilled", "challenge_id": cid,
                "resource_id": "rail-sub-starter", "tx_hash": tx,
                "payer_wallet": WALLET, "amount_atomic": 297_000_000,
                "amount_display": 297.0}

    def _fake_sync(**kw):
        calls["sync"] += 1

    monkeypatch.setattr(x402, "verify_challenge", _fake_verify)
    monkeypatch.setattr(rs, "_sub_money_gate_allows", lambda **kw: True)
    monkeypatch.setattr(rs, "_sync_platform", _fake_sync)
    return {"tmp": tmp_path, "calls": calls}


def _jsonl_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# R1: crash after finalize's external appends -> retry must not re-append
# ---------------------------------------------------------------------------

def test_r1_crash_after_finalize_appends_does_not_duplicate_on_retry(
        real_finalize_env, monkeypatch):
    import sincor2.x402_payments as x402_mod

    real_finalize = x402_mod.finalize_challenge_payment
    state = {"crashed": False}

    def _crashing_finalize(result):
        out = real_finalize(result)  # both JSONL appends happen here
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("simulated crash after external appends")
        return out

    monkeypatch.setattr(x402_mod, "finalize_challenge_payment",
                        _crashing_finalize)

    cid = _issue()
    first = rs.settle_renewal(cid, TX, WALLET)
    assert first["ok"] is False  # crash interrupted the settle

    tmp = real_finalize_env["tmp"]
    payments = [r for r in _jsonl_rows(tmp / "platform_payments.jsonl")
                if r.get("tx_hash") == TX]
    inflows = [r for r in _jsonl_rows(tmp / "inflow.jsonl")
               if r.get("tx_hash") == TX]
    assert len(payments) == 1
    assert len(inflows) == 1

    retry = rs.settle_renewal(cid, TX, WALLET)
    assert retry["ok"] is True
    assert retry["status"] == "renewed"
    assert rs.subscription_status(WALLET)["period_index"] == 1

    payments = [r for r in _jsonl_rows(tmp / "platform_payments.jsonl")
                if r.get("tx_hash") == TX]
    inflows = [r for r in _jsonl_rows(tmp / "inflow.jsonl")
               if r.get("tx_hash") == TX]
    assert len(payments) == 1, payments
    assert len(inflows) == 1, inflows


def test_r1_crash_between_the_two_appends_resumes_exactly_once(
        real_finalize_env, monkeypatch):
    import sincor2.treasury_inflow as inflow_mod
    import sincor2.x402_payments as x402_mod

    real_record_inflow = inflow_mod.record_inflow
    state = {"crashed": False}

    def _crashing_inflow(*args, **kwargs):
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("simulated crash between appends")
        return real_record_inflow(*args, **kwargs)

    # finalize_challenge_payment uses the name bound in x402_payments.
    monkeypatch.setattr(x402_mod, "record_inflow", _crashing_inflow)

    cid = _issue()
    first = rs.settle_renewal(cid, TX, WALLET)
    assert first["ok"] is False

    tmp = real_finalize_env["tmp"]
    assert len([r for r in _jsonl_rows(tmp / "platform_payments.jsonl")
                if r.get("tx_hash") == TX]) == 1
    assert _jsonl_rows(tmp / "inflow.jsonl") == []

    retry = rs.settle_renewal(cid, TX, WALLET)
    assert retry["ok"] is True
    assert retry["status"] == "renewed"

    assert len([r for r in _jsonl_rows(tmp / "platform_payments.jsonl")
                if r.get("tx_hash") == TX]) == 1
    assert len([r for r in _jsonl_rows(tmp / "inflow.jsonl")
                if r.get("tx_hash") == TX]) == 1


# ---------------------------------------------------------------------------
# R2: crash after the platform-sync claim -> reconcile must repair it
# ---------------------------------------------------------------------------

class _HardCrash(BaseException):
    """Simulates a process crash: Exception handlers never run."""


def test_r2_crash_after_sync_claim_is_repaired_by_duplicate_ack(
        atomic_env, monkeypatch):
    calls = atomic_env["calls"]
    state = {"crashed": False}

    def _crashing_sync(**kw):
        if not state["crashed"]:
            state["crashed"] = True
            raise _HardCrash("simulated process death mid-sync")
        calls["sync"] += 1

    monkeypatch.setattr(rs, "_sync_platform", _crashing_sync)

    cid = _issue()
    settle_id = f"railsub-{cid}"
    with pytest.raises(_HardCrash):
        rs.settle_renewal(cid, TX, WALLET)

    # Post-crash state: period applied, sync claimed but never done.
    row = _settle_row(atomic_env["tmp"], settle_id)
    assert row["extended"] == 1
    assert row["platform_synced"] == 1
    assert row.get("platform_sync_done") in (0, None)

    dup = rs.settle_renewal(cid, TX, WALLET)
    assert dup["ok"] is True
    assert dup["status"] == "already_renewed"
    assert calls["sync"] == 1  # the repair drove the sync exactly once
    row = _settle_row(atomic_env["tmp"], settle_id)
    assert row.get("platform_sync_done") == 1

    third = rs.settle_renewal(cid, TX, WALLET)
    assert third["ok"] is True
    assert calls["sync"] == 1  # done is terminal: no further sync


# ---------------------------------------------------------------------------
# R3/R4: legacy (pre-W-105 schema) databases
# ---------------------------------------------------------------------------

_OLD_SCHEMA = """
CREATE TABLE rail_subscriptions (
    wallet            TEXT PRIMARY KEY,
    plan_id           TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    period_index      INTEGER NOT NULL DEFAULT 0,
    period_start      TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    grace_until       TEXT NOT NULL DEFAULT '',
    last_tx_hash      TEXT NOT NULL DEFAULT '',
    last_challenge_id TEXT NOT NULL DEFAULT '',
    reminder_count    INTEGER NOT NULL DEFAULT 0,
    last_reminder_at  TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE TABLE rail_sub_challenges (
    challenge_id TEXT PRIMARY KEY,
    wallet       TEXT NOT NULL,
    plan_id      TEXT NOT NULL,
    issued_at    TEXT NOT NULL
);
CREATE TABLE rail_sub_settles (
    settle_id     TEXT PRIMARY KEY,
    challenge_id  TEXT NOT NULL,
    wallet        TEXT NOT NULL,
    plan_id       TEXT NOT NULL,
    tx_hash       TEXT NOT NULL DEFAULT '',
    amount_usdc   REAL NOT NULL DEFAULT 0,
    verified      INTEGER NOT NULL DEFAULT 0,
    finalized     INTEGER NOT NULL DEFAULT 0,
    extended      INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
"""


@pytest.fixture()
def legacy_env(tmp_path, monkeypatch):
    """Stubs only; the DB file is hand-built with the OLD schema."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    x402.init_x402_db()
    calls = {"sync": 0, "finalize": 0}

    def _fake_verify(cid, tx, **kw):
        return {"ok": True, "status": "fulfilled", "challenge_id": cid,
                "resource_id": "rail-sub-starter", "tx_hash": tx,
                "payer_wallet": WALLET, "amount_atomic": 297_000_000,
                "amount_display": 297.0}

    def _fake_sync(**kw):
        calls["sync"] += 1

    def _fake_finalize(result):
        calls["finalize"] += 1
        return {}

    monkeypatch.setattr(x402, "verify_challenge", _fake_verify)
    monkeypatch.setattr(x402, "finalize_challenge_payment", _fake_finalize)
    monkeypatch.setattr(rs, "_sub_money_gate_allows", lambda **kw: True)
    monkeypatch.setattr(rs, "_sync_platform", _fake_sync)
    return {"tmp": tmp_path, "calls": calls}


def _build_legacy_db(tmp_path, *, extended, challenge_id="legacy-ch-1"):
    """Old-code state: one period already applied+committed by old code
    (subscription row period_index=1, last_challenge_id=<challenge>)."""
    now = datetime.now(timezone.utc)
    period_end = (now + timedelta(days=30)).isoformat()
    stamp = now.isoformat()
    conn = sqlite3.connect(str(tmp_path / "revenue.db"))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO rail_subscriptions (wallet, plan_id, status, "
            "period_index, period_start, period_end, grace_until, "
            "last_tx_hash, last_challenge_id, reminder_count, "
            "last_reminder_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (WALLET, "starter", "active", 1, stamp, period_end,
             (now + timedelta(days=37)).isoformat(), TX, challenge_id,
             0, "", stamp, stamp))
        conn.execute(
            "INSERT INTO rail_sub_challenges (challenge_id, wallet, "
            "plan_id, issued_at) VALUES (?,?,?,?)",
            (challenge_id, WALLET, "starter", stamp))
        conn.execute(
            "INSERT INTO rail_sub_settles (settle_id, challenge_id, "
            "wallet, plan_id, tx_hash, amount_usdc, verified, finalized, "
            "extended, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"railsub-{challenge_id}", challenge_id, WALLET, "starter",
             TX, 297.0, 1, 1, extended, "", stamp))
        conn.commit()
    finally:
        conn.close()
    return challenge_id, period_end


def test_r3_legacy_crash_victim_is_not_double_extended(legacy_env):
    cid, period_end = _build_legacy_db(legacy_env["tmp"], extended=0)

    res = rs.settle_renewal(cid, TX, WALLET)
    assert res["ok"] is True
    assert res["status"] == "already_renewed"

    status = rs.subscription_status(WALLET)
    assert status["period_index"] == 1  # unchanged: never re-applied
    assert status["period_end"] == period_end


def test_r4_legacy_completed_settle_does_not_resync_platform(legacy_env):
    cid, _period_end = _build_legacy_db(legacy_env["tmp"], extended=1)

    res = rs.settle_renewal(cid, TX, WALLET)
    assert res["ok"] is True
    assert res["status"] == "already_renewed"
    # Old code already synced this settle inside _apply_period; the
    # migration must seed that fact so reconcile fires NO platform sync.
    assert legacy_env["calls"]["sync"] == 0


# ---------------------------------------------------------------------------
# R5: platform-sync failure must not flip a committed settle to failure
# ---------------------------------------------------------------------------

def test_r5_sync_failure_keeps_settle_success_and_repairs_later(
        atomic_env, monkeypatch):
    calls = atomic_env["calls"]
    state = {"fail": True}

    def _flaky_sync(**kw):
        if state["fail"]:
            raise RuntimeError("platform store unavailable")
        calls["sync"] += 1

    monkeypatch.setattr(rs, "_sync_platform", _flaky_sync)

    cid = _issue()
    settle_id = f"railsub-{cid}"
    first = rs.settle_renewal(cid, TX, WALLET)
    assert first["ok"] is True, first
    assert first["status"] == "renewed"
    assert rs.subscription_status(WALLET)["period_index"] == 1

    # Sync left repairable: claim released, done not set.
    row = _settle_row(atomic_env["tmp"], settle_id)
    assert row["platform_synced"] == 0
    assert row.get("platform_sync_done") in (0, None)

    state["fail"] = False
    dup = rs.settle_renewal(cid, TX, WALLET)
    assert dup["ok"] is True
    assert dup["status"] == "already_renewed"
    assert calls["sync"] == 1  # the duplicate ack completed the sync
    row = _settle_row(atomic_env["tmp"], settle_id)
    assert row.get("platform_sync_done") == 1
    # And the period was still applied exactly once overall.
    assert rs.subscription_status(WALLET)["period_index"] == 1
