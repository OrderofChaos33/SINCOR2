"""Monetization and value-creation engine — no recovery required."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from launch_content_engine.onchain_stats import fetch_stats  # noqa: E402

TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
HOOK_FLOOR_SINC_M = 20.0


def _treasury_sinc() -> float:
    from launch_content_engine.onchain_stats import _safe_call, SINC

    return _safe_call("balanceOf", SINC, TREASURY)


def _platform_revenue() -> dict:
    from sincor2.data_paths import orders_db_path

    db = orders_db_path()
    if not db.is_file():
        return {"ok": False, "completed_usd": 0.0, "total_orders": 0}
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM orders")
    total = int(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE payment_status='completed'")
    completed = float(cur.fetchone()[0] or 0)
    conn.close()
    return {"ok": True, "completed_usd": round(completed, 2), "total_orders": total}


def fetch_value_summary() -> dict:
    s = fetch_stats()
    treasury_sinc = _treasury_sinc()
    platform = _platform_revenue()
    hook_m = float(s.get("sinc_in_hook_pm_m", 0))

    streams = [
        {
            "id": "hook",
            "title": "USDC hook sell walls",
            "status": "live",
            "sinc_m": hook_m,
            "url": "https://getsincor.com/buy",
            "note": f"~{hook_m}M SINC on hook walls. Official floor $0.15 ($150M / 1B).",
        },
        {
            "id": "platform",
            "title": "SINCOR platform (SINC + AXM billing)",
            "status": "live",
            "url": "https://getsincor.com/buy",
            "completed_usd": platform.get("completed_usd", 0),
            "total_orders": platform.get("total_orders", 0),
            "note": "Subscriptions in SINC, one-off intel in AXM — /buy on Base.",
        },
        {
            "id": "tokenlist",
            "title": "Canonical routing (stops rogue-pool harm)",
            "status": "live",
            "url": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
            "note": "Import token list in MetaMask/Rabby — official SINC only.",
        },
    ]

    return {
        **s,
        "treasury": TREASURY,
        "treasury_sinc": round(treasury_sinc, 0),
        "hook_floor_sinc_m": HOOK_FLOOR_SINC_M,
        "streams": streams,
        "buy_url": "https://getsincor.com/buy",
    }


def social_pack(wallet: str | None = None) -> dict:
    v = fetch_value_summary()
    buy = "https://getsincor.com/buy"
    hook_m = v.get("sinc_in_hook_pm_m", HOOK_FLOOR_SINC_M)
    floor = v.get("official_floor_usd", 0.15)

    tweet = (
        f"SINC on Base — $0.15 floor ($150M FDV / 1B tokens).\n"
        f"• Official price ${floor:.2f}/SINC\n"
        f"• ~{hook_m}M SINC on v4 hook walls\n"
        f"Checkout: {buy}"
    )
    telegram = (
        f"SINC live on Base. Official price ${floor:.2f} "
        f"($150M valuation / 1 billion tokens). Checkout: {buy}"
    )
    farcaster = tweet[:320]

    return {
        "wallet": wallet,
        "referral_link": buy,
        "twitter": tweet,
        "telegram": telegram,
        "farcaster": farcaster,
        "twitter_intent": "https://twitter.com/intent/tweet?text=" + _urlencode(tweet),
        "telegram_share": "https://t.me/share/url?url=" + _urlencode(buy) + "&text=" + _urlencode(telegram[:200]),
    }


def _urlencode(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def run_value_ops() -> dict:
    summary = fetch_value_summary()
    pack = social_pack()
    out_dir = _ROOT / "logs" / "value"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = out_dir / f"value_ops_{stamp}.json"
    report = {"summary": summary, "social_default": pack}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {"ok": True, "report_path": str(report_path), "summary": summary, "social": pack}