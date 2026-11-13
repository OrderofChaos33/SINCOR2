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
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sincor2.platform_payments as pp
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


# ===========================================================================
# Fix round 2 (delta re-review residuals NEW-1..NEW-6)
#
# Round 1 made the sync claim crash-safe, but the sync ITSELF was not
# idempotent: _sync_platform_once could not distinguish "claimed, sync
# never ran" from "claimed, sync already committed", and
# platform_payments.activate_subscription has no payment_id dedupe —
# so a crash after the platform commit (or a failed done-write) made
# the repairing ack extend the platform mirror a second time.
# ===========================================================================

@pytest.fixture()
def platform_sync_env(tmp_path, monkeypatch):
    """Real _sync_platform + real platform store; only chain verify,
    the x402 finalize append and the money gate are stubbed."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    x402.init_x402_db()
    pp.init_platform_payments_db()

    calls = {"verify": 0, "finalize": 0}

    def _fake_verify(cid, tx, **kw):
        calls["verify"] += 1
        issued = rs._get_issued(cid) or {}
        plan = issued.get("plan_id") or "starter"
        return {"ok": True, "status": "fulfilled", "challenge_id": cid,
                "resource_id": f"rail-sub-{plan}", "tx_hash": tx,
                "payer_wallet": WALLET, "amount_atomic": 297_000_000,
                "amount_display": 297.0}

    def _fake_finalize(result):
        calls["finalize"] += 1
        return {}

    monkeypatch.setattr(x402, "verify_challenge", _fake_verify)
    monkeypatch.setattr(x402, "finalize_challenge_payment", _fake_finalize)
    monkeypatch.setattr(rs, "_sub_money_gate_allows", lambda **kw: True)
    return {"tmp": tmp_path, "calls": calls}


def _active_platform_subs(wallet=WALLET):
    return [s for s in pp.list_subscriptions(wallet)
            if s["status"] == "active"]


def _child_env(tmp_path):
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": "src:.",
        "ORDERS_DB_PATH": str(tmp_path / "orders.db"),
        "SINCOR_REVENUE_DB": str(tmp_path / "revenue.db"),
        "SINCOR_DATA_DIR": str(tmp_path / "data"),
        "TREASURY_INFLOW_LEDGER": str(tmp_path / "inflow.jsonl"),
    })
    return env


_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# NEW-1: crash after the platform sync commits, before done is written
# ---------------------------------------------------------------------------

_NEW1_CHILD = r'''
import os

import sincor2.rail_subscriptions as rs
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

reset_money_boundary()
x402.init_x402_db()

WALLET = "0x" + "11" * 20
TX = "0x" + "ab" * 32


def _fake_verify(cid, tx, **kw):
    return {"ok": True, "status": "fulfilled", "challenge_id": cid,
            "resource_id": "rail-sub-starter", "tx_hash": tx,
            "payer_wallet": WALLET, "amount_atomic": 297_000_000,
            "amount_display": 297.0}


x402.verify_challenge = _fake_verify
x402.finalize_challenge_payment = lambda result: {}
rs._sub_money_gate_allows = lambda **kw: True

cid = rs.issue_renewal_challenge(WALLET, "starter")["challenge_id"]
print("CID " + cid, flush=True)

_real_update = rs._update_sub_settle


def _die_in_done_write(settle_id, **fields):
    if fields.get("platform_sync_done") == 1:
        os._exit(42)  # killed inside the done-write: the platform sync
        # has committed, the done flag never lands
    return _real_update(settle_id, **fields)


rs._update_sub_settle = _die_in_done_write
rs.settle_renewal(cid, TX, WALLET)
print("SURVIVED", flush=True)
'''


def test_new1_killed_inside_done_write_duplicate_ack_does_not_double_extend(
        platform_sync_env):
    tmp = platform_sync_env["tmp"]
    script = tmp / "new1_child.py"
    script.write_text(_NEW1_CHILD, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script)], cwd=str(_REPO_ROOT),
        env=_child_env(tmp), capture_output=True, text=True, timeout=180)
    assert proc.returncode == 42, proc.stdout + proc.stderr
    cid = next(line.split(" ", 1)[1] for line in proc.stdout.splitlines()
               if line.startswith("CID "))
    settle_id = f"railsub-{cid}"

    # Post-crash state: rail period applied, sync claimed, platform
    # mirror extended exactly once, done flag never written.
    row = _settle_row(tmp, settle_id)
    assert row["extended"] == 1
    assert row["platform_synced"] == 1
    assert row["platform_sync_done"] == 0
    active = _active_platform_subs()
    assert len(active) == 1
    assert active[0]["last_payment_id"] == cid
    first_end = active[0]["period_end"]

    dup = rs.settle_renewal(cid, TX, WALLET)
    assert dup["ok"] is True
    assert dup["status"] == "already_renewed"

    active = _active_platform_subs()
    assert len(active) == 1
    assert active[0]["period_end"] == first_end  # NOT extended a 2nd time
    assert _settle_row(tmp, settle_id)["platform_sync_done"] == 1


def test_new1_done_write_failure_duplicate_ack_does_not_double_extend(
        platform_sync_env, monkeypatch):
    # Same residual via a done-write FAILURE (exception) instead of a
    # kill: the R5 wrap keeps the settle successful, and the repairing
    # ack must still not re-extend the platform mirror.
    real_update = rs._update_sub_settle
    state = {"failed": False}

    def _failing_update(settle_id, **fields):
        if fields.get("platform_sync_done") == 1 and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("done-write failed")
        return real_update(settle_id, **fields)

    monkeypatch.setattr(rs, "_update_sub_settle", _failing_update)

    cid = _issue()
    settle_id = f"railsub-{cid}"
    first = rs.settle_renewal(cid, TX, WALLET)
    assert first["ok"] is True  # R5: committed settle stands
    active = _active_platform_subs()
    assert len(active) == 1
    assert active[0]["last_payment_id"] == cid
    first_end = active[0]["period_end"]
    row = _settle_row(platform_sync_env["tmp"], settle_id)
    assert row["platform_sync_done"] == 0

    dup = rs.settle_renewal(cid, TX, WALLET)
    assert dup["ok"] is True
    assert dup["status"] == "already_renewed"
    active = _active_platform_subs()
    assert len(active) == 1
    assert active[0]["period_end"] == first_end
    row = _settle_row(platform_sync_env["tmp"], settle_id)
    assert row["platform_sync_done"] == 1


# ---------------------------------------------------------------------------
# NEW-2: legacy victim whose old-code sync already ran must not re-sync
# ---------------------------------------------------------------------------

def test_new2_legacy_victim_already_synced_is_not_resynced(
        platform_sync_env):
    tmp = platform_sync_env["tmp"]
    cid, _end = _build_legacy_db(tmp, extended=0)
    # The platform store already carries this payment's sync: old code
    # synced inside _apply_period, then crashed before extended=1.
    seeded = pp.activate_subscription(
        wallet=WALLET, plan_id="starter",
        product_name=rs.SUB_PLANS["starter"]["name"], token="USDC",
        tx_hash=TX, payment_id=cid, email="", period_days=30)
    first_end = seeded["period_end"]

    res = rs.settle_renewal(cid, TX, WALLET)
    assert res["ok"] is True
    assert res["status"] == "already_renewed"

    active = _active_platform_subs()
    assert len(active) == 1
    assert active[0]["period_end"] == first_end  # no second +30d mirror
    assert _settle_row(tmp, f"railsub-{cid}")["platform_sync_done"] == 1


# ---------------------------------------------------------------------------
# NEW-3: legacy plan-change victim must converge to the settle's plan
# ---------------------------------------------------------------------------

def _build_legacy_plan_change_db(tmp_path, challenge_id="legacy-pc-1"):
    """Old-code state for an UPGRADE settle: the rail row already
    applied the professional period (last_challenge_id points at the
    settle), then crashed before extended=1 was written."""
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
            (WALLET, "professional", "active", 1, stamp, period_end,
             (now + timedelta(days=37)).isoformat(), TX, challenge_id,
             0, "", stamp, stamp))
        conn.execute(
            "INSERT INTO rail_sub_challenges (challenge_id, wallet, "
            "plan_id, issued_at) VALUES (?,?,?,?)",
            (challenge_id, WALLET, "professional", stamp))
        conn.execute(
            "INSERT INTO rail_sub_settles (settle_id, challenge_id, "
            "wallet, plan_id, tx_hash, amount_usdc, verified, finalized, "
            "extended, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"railsub-{challenge_id}", challenge_id, WALLET,
             "professional", TX, 997.0, 1, 1, 0, "", stamp))
        conn.commit()
    finally:
        conn.close()
    return challenge_id


def test_new3_legacy_plan_change_victim_converges_to_settle_plan(
        platform_sync_env):
    tmp = platform_sync_env["tmp"]
    cid = _build_legacy_plan_change_db(tmp)
    # Platform store still shows the OLD plan active: the crashed
    # old-code sync never mirrored the upgrade.
    pp.activate_subscription(
        wallet=WALLET, plan_id="starter",
        product_name=rs.SUB_PLANS["starter"]["name"], token="USDC",
        tx_hash="0x" + "ef" * 32, payment_id="earlier-starter-payment",
        email="", period_days=30)

    res = rs.settle_renewal(cid, TX, WALLET)
    assert res["ok"] is True
    assert res["status"] == "already_renewed"
    # Rail side untouched: the one applied period stands.
    assert rs.subscription_status(WALLET)["period_index"] == 1

    active = _active_platform_subs()
    assert len(active) == 1, active  # starter cancelled, not co-active
    assert active[0]["plan_id"] == "professional"
    assert active[0]["last_payment_id"] == cid


# ---------------------------------------------------------------------------
# NEW-4: JSONL dedupe check+append must be atomic across processes
# ---------------------------------------------------------------------------

_NEW4_CHILD = r'''
import os
import sys
import time

mode = sys.argv[1]
go_file = sys.argv[2]

if mode == "inflow":
    from sincor2.treasury_inflow import record_inflow
else:
    from sincor2.agent_billing import record_platform_payment

TX = "0x" + "cd" * 32
WALLET = "0x" + "22" * 20

deadline = time.time() + 60
while not os.path.exists(go_file):
    if time.time() > deadline:
        sys.exit(3)
    time.sleep(0.001)

if mode == "inflow":
    record_inflow(42.0, asset="USDC", source="new4-probe",
                  tx_hash=TX, note="new4-probe-note")
else:
    record_platform_payment(
        tx_hash=TX, payer_wallet=WALLET, token="USDC",
        amount_atomic=42000000, product_name="New4 Probe",
        payment_id="new4-probe-payment")
'''


def _run_new4_probe(tmp_path, mode):
    """8 processes released simultaneously on one dedupe key, x3 rounds.
    Returns the per-round ledger row counts for that key.

    Each round's ledger is pre-seeded with 20k unrelated rows: the
    dedupe check scans the whole file, so a large ledger widens the
    check->append window enough for the release overlap to be a real
    race (an empty ledger's window is sub-millisecond and the race
    only fires sporadically — the delta review caught it 1-in-5)."""
    script = tmp_path / "new4_child.py"
    script.write_text(_NEW4_CHILD, encoding="utf-8")
    counts = []
    for round_no in range(3):
        rdir = tmp_path / f"round{round_no}"
        rdir.mkdir()
        if mode == "inflow":
            ledger = rdir / "inflow.jsonl"
            seed = {"ts": "2026-01-01T00:00:00+00:00", "amount": 1.0,
                    "asset": "USDC", "source": "seed",
                    "usd_estimate": 1.0, "tx_hash": "0x" + "00" * 32,
                    "note": "seed", "projected": False,
                    "event_id": "seed", "supersedes": None}
            id_field = "event_id"
        else:
            ledger = rdir / "data" / "agent_burn_log.jsonl"
            seed = {"ts": "2026-01-01T00:00:00+00:00",
                    "type": "platform_payment",
                    "tx_hash": "0x" + "00" * 32, "payer": "0x" + "33" * 20,
                    "token": "USDC", "amount_atomic": 1,
                    "amount_display": 0.0, "treasury": "0x" + "44" * 20,
                    "product_name": "Seed", "plan_id": "",
                    "payment_id": "seed", "burn_policy": "",
                    "burn_tx": None}
            id_field = "payment_id"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("w", encoding="utf-8") as f:
            for i in range(20000):
                row = dict(seed)
                row[id_field] = f"seed-{i}"
                f.write(json.dumps(row) + "\n")
        go = rdir / "go"
        env = _child_env(rdir)
        procs = [subprocess.Popen(
            [sys.executable, str(script), mode, str(go)],
            cwd=str(_REPO_ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(8)]
        go.write_text("go", encoding="utf-8")
        for p in procs:
            assert p.wait(timeout=180) == 0
        rows = [r for r in _jsonl_rows(ledger)
                if r.get("tx_hash") == "0x" + "cd" * 32]
        counts.append(len(rows))
    return counts


def test_new4_inflow_dedupe_is_atomic_across_processes(tmp_path):
    assert _run_new4_probe(tmp_path, "inflow") == [1, 1, 1]


def test_new4_platform_payment_dedupe_is_atomic_across_processes(tmp_path):
    assert _run_new4_probe(tmp_path, "billing") == [1, 1, 1]


# ---------------------------------------------------------------------------
# NEW-5: migration seeding must not fire on a c2de0901-era DB
# (applied stamps + platform_synced exist, platform_sync_done does not)
# ---------------------------------------------------------------------------

_SUB_AND_CHALLENGE_SCHEMA = """
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
"""

_C2DE_SETTLES_SCHEMA = """
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
    applied_period_end   TEXT NOT NULL DEFAULT '',
    applied_period_index INTEGER NOT NULL DEFAULT 0,
    applied_action       TEXT NOT NULL DEFAULT '',
    platform_synced      INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
"""


def test_new5_c2de_era_pending_sync_is_not_seeded_done(
        tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    period_end = (now + timedelta(days=30)).isoformat()
    conn = sqlite3.connect(str(tmp_path / "revenue.db"))
    try:
        conn.executescript(_SUB_AND_CHALLENGE_SCHEMA
                           + _C2DE_SETTLES_SCHEMA)
        conn.execute(
            "INSERT INTO rail_sub_settles (settle_id, challenge_id, "
            "wallet, plan_id, tx_hash, amount_usdc, verified, finalized, "
            "extended, applied_period_end, applied_period_index, "
            "applied_action, platform_synced, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("railsub-c2de-ch-1", "c2de-ch-1", WALLET, "starter", TX,
             297.0, 1, 1, 1, period_end, 1, "renewed", 0, "", stamp))
        conn.commit()
    finally:
        conn.close()

    conn = rs._connect()  # the migration moment: platform_sync_done added
    conn.close()

    row = _settle_row(tmp_path, "railsub-c2de-ch-1")
    # The sync is legitimately still pending (platform_synced=0 on a
    # schema that HAS the flag): it must NOT be seeded done — reconcile
    # still has to drive it.
    assert row["platform_synced"] == 0
    assert row["platform_sync_done"] == 0


def test_new5_genuine_pre_w105_db_still_seeds_done(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    cid, _end = _build_legacy_db(tmp_path, extended=1)
    conn = rs._connect()  # neither platform column existed pre-connect
    conn.close()
    row = _settle_row(tmp_path, f"railsub-{cid}")
    assert row["platform_synced"] == 1
    assert row["platform_sync_done"] == 1


# ---------------------------------------------------------------------------
# NEW-6: empty-payment_id dedupe must not swallow a different amount
# ---------------------------------------------------------------------------

def test_new6_empty_payment_id_different_amount_is_not_deduped(
        tmp_path, monkeypatch):
    import sincor2.agent_billing as ab

    log_file = tmp_path / "platform_payments.jsonl"
    monkeypatch.setattr(ab, "_log_path", lambda: log_file)

    ab.record_platform_payment(
        tx_hash=TX, payer_wallet=WALLET, token="USDC",
        amount_atomic=100, product_name="Probe", payment_id="")
    ab.record_platform_payment(
        tx_hash=TX, payer_wallet=WALLET, token="USDC",
        amount_atomic=200, product_name="Probe", payment_id="")
    rows = [r for r in _jsonl_rows(log_file) if r.get("tx_hash") == TX]
    assert len(rows) == 2  # different amount = different payment

    # An identical retry (same amount) still dedupes.
    ab.record_platform_payment(
        tx_hash=TX, payer_wallet=WALLET, token="USDC",
        amount_atomic=100, product_name="Probe", payment_id="")
    rows = [r for r in _jsonl_rows(log_file) if r.get("tx_hash") == TX]
    assert len(rows) == 2


# ===========================================================================
# Fix round 3 (delta re-review #3 residuals R3-1 / R3-2)
#
# R3-1: round 2 derived plan_changed from the WHOLE platform store, so a
# rail settle cancelled unrelated non-rail platform subscriptions (the
# store is shared and multi-plan by design — e.g. an `intel` row bought
# via the human checkout). The derivation must be scoped to the rail
# plan family (SUB_PLANS) and non-rail rows must never be cancelled.
#
# R3-2: the last_payment_id early-return reads ONE mutable slot on the
# subscription row. CH-A crashes post-activation / pre-done, CH-B (a
# legitimate next purchase) overwrites the slot, and CH-A's late
# repairing ack then re-drives the sync and extends a THIRD period for
# two payments. Idempotency needs a per-payment journal that cannot be
# overwritten, written atomically with the activation.
# ===========================================================================

TX_B = "0x" + "ef" * 32


def _seed_platform_plan(plan_id, payment_id, tx_hash="0x" + "cd" * 32):
    plan = pp.PLATFORM_PLANS[plan_id]
    return pp.activate_subscription(
        wallet=WALLET, plan_id=plan_id,
        product_name=plan["product_name"], token=plan["token"],
        tx_hash=tx_hash, payment_id=payment_id, email="", period_days=30)


def _platform_sub(plan_id, wallet=WALLET):
    for s in pp.list_subscriptions(wallet):
        if s["plan_id"] == plan_id:
            return s
    return None


# ---------------------------------------------------------------------------
# R3-1: non-rail platform rows survive rail settles
# ---------------------------------------------------------------------------

def test_r31_intel_survives_first_time_rail_starter_settle(
        platform_sync_env):
    seeded = _seed_platform_plan("intel", "intel-payment-1")
    intel_end = seeded["period_end"]

    cid = _issue()
    res = rs.settle_renewal(cid, TX, WALLET)
    assert res["ok"] is True, res
    assert res["status"] == "renewed"

    active = {s["plan_id"]: s for s in _active_platform_subs()}
    assert set(active) == {"intel", "starter"}, active
    assert active["intel"]["period_end"] == intel_end  # untouched
    assert active["intel"]["last_payment_id"] == "intel-payment-1"
    assert active["starter"]["last_payment_id"] == cid


def test_r31_intel_survives_same_plan_rail_renewal(platform_sync_env):
    seeded = _seed_platform_plan("intel", "intel-payment-1")
    intel_end = seeded["period_end"]

    cid1 = _issue()
    first = rs.settle_renewal(cid1, TX, WALLET)
    assert first["ok"] is True, first
    starter_end_1 = _platform_sub("starter")["period_end"]

    cid2 = _issue()
    second = rs.settle_renewal(cid2, TX_B, WALLET)
    assert second["ok"] is True, second
    assert second["status"] == "renewed"

    active = {s["plan_id"]: s for s in _active_platform_subs()}
    assert set(active) == {"intel", "starter"}, active
    assert active["intel"]["period_end"] == intel_end  # still untouched
    # The renewal really extended the rail plan (not a no-op).
    assert active["starter"]["period_end"] > starter_end_1
    assert active["starter"]["last_payment_id"] == cid2


def test_r31_genuine_rail_plan_switch_cancels_only_rail_rows(
        platform_sync_env):
    seeded = _seed_platform_plan("intel", "intel-payment-1")
    intel_end = seeded["period_end"]

    cid1 = _issue()
    assert rs.settle_renewal(cid1, TX, WALLET)["ok"] is True

    cid2 = rs.issue_renewal_challenge(WALLET, "professional")[
        "challenge_id"]
    res = rs.settle_renewal(cid2, TX_B, WALLET)
    assert res["ok"] is True, res
    assert res["status"] == "upgraded"

    active = {s["plan_id"]: s for s in _active_platform_subs()}
    # Rail family converged on professional; intel was NOT cancelled.
    assert set(active) == {"intel", "professional"}, active
    assert active["intel"]["period_end"] == intel_end
    assert active["professional"]["last_payment_id"] == cid2
    starter = _platform_sub("starter")
    assert starter["status"] == "cancelled"


# ---------------------------------------------------------------------------
# R3-2: per-payment journal defeats the single-slot interleave
# ---------------------------------------------------------------------------

def _fail_first_done_write(monkeypatch):
    real_update = rs._update_sub_settle
    state = {"failed": False}

    def _failing_update(settle_id, **fields):
        if fields.get("platform_sync_done") == 1 and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("done-write failed (simulated crash)")
        return real_update(settle_id, **fields)

    monkeypatch.setattr(rs, "_update_sub_settle", _failing_update)
    return state


def test_r32_interleaved_crash_repair_does_not_extend_third_period(
        platform_sync_env, monkeypatch):
    _fail_first_done_write(monkeypatch)

    # CH-A: activation commits, the done-write dies -> claim held,
    # platform_sync_done=0 (the crash state).
    cid_a = _issue()
    res_a = rs.settle_renewal(cid_a, TX, WALLET)
    assert res_a["ok"] is True, res_a  # R5: committed settle stands
    row_a = _settle_row(platform_sync_env["tmp"], f"railsub-{cid_a}")
    assert row_a["platform_sync_done"] == 0
    end_a = _platform_sub("starter")["period_end"]

    # CH-B: a legitimate next purchase settles fully and overwrites
    # the single last_payment_id slot.
    cid_b = _issue()
    res_b = rs.settle_renewal(cid_b, TX_B, WALLET)
    assert res_b["ok"] is True, res_b
    sub_b = _platform_sub("starter")
    assert sub_b["last_payment_id"] == cid_b
    end_b = sub_b["period_end"]
    assert end_b > end_a  # B really bought a second period

    # CH-A's late repairing ack: must NOT extend a third period.
    dup = rs.settle_renewal(cid_a, TX, WALLET)
    assert dup["ok"] is True, dup
    assert dup["status"] == "already_renewed"
    sub = _platform_sub("starter")
    assert sub["period_end"] == end_b  # exactly 2 periods for 2 payments
    assert sub["last_payment_id"] == cid_b
    assert len(_active_platform_subs()) == 1
    row_a = _settle_row(platform_sync_env["tmp"], f"railsub-{cid_a}")
    assert row_a["platform_sync_done"] == 1  # repair completed the sync


def test_r32_plan_change_redrive_after_interleave_does_not_reactivate(
        platform_sync_env, monkeypatch):
    cid0 = _issue()
    assert rs.settle_renewal(cid0, TX, WALLET)["ok"] is True

    _fail_first_done_write(monkeypatch)

    # CH-A: a plan-change (upgrade) settle whose activation commits
    # but whose done-write dies.
    cid_a = rs.issue_renewal_challenge(WALLET, "professional")[
        "challenge_id"]
    res_a = rs.settle_renewal(cid_a, TX_B, WALLET)
    assert res_a["ok"] is True, res_a
    assert res_a["status"] == "upgraded"
    row_a = _settle_row(platform_sync_env["tmp"], f"railsub-{cid_a}")
    assert row_a["platform_sync_done"] == 0
    assert _platform_sub("professional")["last_payment_id"] == cid_a

    # CH-B: a legitimate professional renewal settles fully and
    # overwrites last_payment_id.
    cid_b = rs.issue_renewal_challenge(WALLET, "professional")[
        "challenge_id"]
    tx_c = "0x" + "98" * 32
    res_b = rs.settle_renewal(cid_b, tx_c, WALLET)
    assert res_b["ok"] is True, res_b
    sub_b = _platform_sub("professional")
    assert sub_b["last_payment_id"] == cid_b
    end_b = sub_b["period_end"]

    # CH-A's late ack re-drives a plan_changed sync: it must not
    # cancel / re-activate / extend again.
    dup = rs.settle_renewal(cid_a, TX_B, WALLET)
    assert dup["ok"] is True, dup
    assert dup["status"] == "already_renewed"
    sub = _platform_sub("professional")
    assert sub["status"] == "active"
    assert sub["period_end"] == end_b
    assert sub["last_payment_id"] == cid_b
    active_plans = {s["plan_id"] for s in _active_platform_subs()}
    assert active_plans == {"professional"}, active_plans


def test_r32_idempotent_entry_point_journals_per_payment(
        platform_sync_env):
    fn = pp.activate_subscription_idempotent
    kw = dict(wallet=WALLET, plan_id="starter",
              product_name=rs.SUB_PLANS["starter"]["name"], token="USDC",
              tx_hash=TX, email="", period_days=30)
    first = fn(payment_id="pay-1", **kw)
    again = fn(payment_id="pay-1", **kw)
    assert again["period_end"] == first["period_end"]  # no re-extension
    assert again["subscription_id"] == first["subscription_id"]

    # A NEW payment_id is a legitimate repeat purchase: it extends.
    second = fn(payment_id="pay-2", **kw)
    assert second["period_end"] > first["period_end"]
    # ...and is itself idempotent thereafter.
    assert fn(payment_id="pay-2", **kw)["period_end"] == \
        second["period_end"]
