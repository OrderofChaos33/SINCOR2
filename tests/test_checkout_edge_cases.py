"""Checkout idempotency, expiry, verify lock, RPC fallbacks."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sincor2 import platform_payments as pp  # noqa: E402
from sincor2.a2a_task_store import MemoryTaskStore  # noqa: E402


def test_create_checkout_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    pp._spot_cache.clear()
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        a = pp.create_checkout(
            "starter",
            payer_wallet="0x1111111111111111111111111111111111111111",
            idempotency_key="k-1",
        )
        b = pp.create_checkout(
            "starter",
            payer_wallet="0x1111111111111111111111111111111111111111",
            idempotency_key="k-1",
        )
    assert a["ok"] and b["ok"]
    assert a["payment_id"] == b["payment_id"]
    assert b["reused"] is True


def test_expire_stale_checkouts(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    pp._spot_cache.clear()
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        created = pp.create_checkout("starter", idempotency_key="exp-1")
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with pp._conn() as conn:
        conn.execute(
            "UPDATE platform_checkouts SET expires_at=? WHERE payment_id=?",
            (past, created["payment_id"]),
        )
        conn.commit()
    n = pp.expire_stale_checkouts()
    assert n == 1
    fake_tx = "0x" + "ab" * 32
    result = pp.verify_checkout(created["payment_id"], fake_tx)
    assert result["ok"] is False
    assert result["error"] == "checkout_expired"


def test_verify_already_completed_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    pp._spot_cache.clear()
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        created = pp.create_checkout("starter", idempotency_key="done-1")
    tx = "0x" + "cd" * 32
    with pp._conn() as conn:
        conn.execute(
            "UPDATE platform_checkouts SET status='completed', tx_hash=? WHERE payment_id=?",
            (tx, created["payment_id"]),
        )
        conn.commit()
    result = pp.verify_checkout(created["payment_id"], tx)
    assert result["ok"] is True
    assert result["status"] == "already_completed"


def test_verify_in_progress_blocks_race(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    pp._spot_cache.clear()
    with patch.object(pp, "fetch_axm_spot_usd", return_value=None):
        created = pp.create_checkout("starter", idempotency_key="race-1")
    with pp._conn() as conn:
        conn.execute(
            "UPDATE platform_checkouts SET status='verifying' WHERE payment_id=?",
            (created["payment_id"],),
        )
        conn.commit()
    result = pp.verify_checkout(created["payment_id"], "0x" + "ee" * 32)
    assert result["ok"] is False
    assert result["error"] == "verify_in_progress"


def test_rpc_urls_include_public_fallbacks(monkeypatch):
    monkeypatch.setenv("BASE_RPC_URL", "https://example.invalid/rpc")
    urls = pp._rpc_urls()
    assert urls[0] == "https://example.invalid/rpc"
    assert "https://mainnet.base.org" in urls


def test_memory_exec_lock_is_exclusive():
    store = MemoryTaskStore()
    assert store.acquire_exec_lock("t1", ttl_seconds=30) is True
    assert store.acquire_exec_lock("t1", ttl_seconds=30) is False
    store.release_exec_lock("t1")
    assert store.acquire_exec_lock("t1", ttl_seconds=30) is True


def test_assignment_deadline_times_out():
    from sincor2.a2a_timeouts import assignment_deadline_ms

    assert assignment_deadline_ms(1000, 10, timeout_ms=120000) == 1020
    assert assignment_deadline_ms(1000, 0, timeout_ms=5000) == 6000
    assert assignment_deadline_ms(0, 10) > 0


def test_place_bid_still_exported():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src/sincor2/a2a_inbound_market.py").read_text()
    assert "\ndef place_bid(" in text
    assert "\ndef expire_stale_assignments(" in text
