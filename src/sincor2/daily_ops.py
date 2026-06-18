"""Safe read-only daily ops — chain snapshot, revenue, wallet watch, buyer scan."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = _PROJECT_ROOT / "logs" / "ops"


def _chain_snapshot() -> dict:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from launch_content_engine.onchain_stats import draft_post, fetch_stats

    stats = fetch_stats()
    stats["draft"] = draft_post()
    return stats


def _revenue_digest() -> dict:
    from sincor2.data_paths import orders_db_path

    db_path = orders_db_path()
    if not db_path.exists():
        return {"ok": False, "reason": "orders.db missing", "path": str(db_path)}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM orders")
    total = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE payment_status = 'completed'")
    completed = float(cur.fetchone()[0] or 0)
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE payment_status = 'pending'")
    pending = float(cur.fetchone()[0] or 0)
    conn.close()
    return {
        "ok": True,
        "total_orders": total,
        "completed_revenue_usd": round(completed, 2),
        "pending_revenue_usd": round(pending, 2),
    }


def _wallet_watch() -> dict:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools.wallet_watch import run_watch

    return run_watch()


def _buyer_scan() -> dict:
    proc = subprocess.run(
        [sys.executable, str(_PROJECT_ROOT / "tools" / "curve_buyers.py")],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return {
        "ok": proc.returncode == 0,
        "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-500:] if proc.stderr else "",
    }


def run_daily(*, include_buyers: bool = False) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "chain": {},
        "revenue": {},
        "wallet_watch": {},
    }
    errors: list[str] = []

    for name, fn in (
        ("chain_snapshot", _chain_snapshot),
        ("revenue", _revenue_digest),
        ("wallet_watch", _wallet_watch),
    ):
        try:
            key = "chain" if name == "chain_snapshot" else name
            report[key] = fn()
        except Exception as e:
            errors.append(f"{name}: {e}")

    if include_buyers:
        try:
            report["buyers"] = _buyer_scan()
        except Exception as e:
            errors.append(f"buyers: {e}")

    try:
        from sincor2.acceptance_status import fetch_acceptance

        report["acceptance"] = fetch_acceptance()
    except Exception as e:
        errors.append(f"acceptance: {e}")

    report["errors"] = errors
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out_path = LOG_DIR / f"daily_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (LOG_DIR / "daily_latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report