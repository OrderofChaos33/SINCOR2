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
GRADUATION_ETH = 0.5
HOOK_FLOOR_SINC_M = 20.0
REFERRAL_PCT = 3


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
    eth = float(s.get("curve_eth_accumulated", 0))
    grad_pct = min(100.0, round(eth / GRADUATION_ETH * 100, 2)) if GRADUATION_ETH else 0.0
    treasury_sinc = _treasury_sinc()
    platform = _platform_revenue()
    hook_m = float(s.get("sinc_in_hook_pm_m", 0))

    streams = [
        {
            "id": "referral",
            "title": "3% referral on every curve buy",
            "status": "live",
            "url": "https://getsincor.com/refer",
            "note": "Share getsincor.com/sinc?ref=YOUR_WALLET — contract pays 3% ETH instantly.",
            "example": "1 ETH buy through your link ≈ 0.03 ETH to you",
        },
        {
            "id": "curve",
            "title": "Bonding curve sales → ETH in curve",
            "status": "live",
            "url": "https://getsincor.com/sinc",
            "eth_accumulated": eth,
            "graduation_pct": grad_pct,
            "graduation_target_eth": GRADUATION_ETH,
            "note": "At 0.5 ETH accumulated the curve graduates to Uniswap v4 + LP burn event.",
        },
        {
            "id": "hook",
            "title": "USDC hook sell walls",
            "status": "live",
            "sinc_m": hook_m,
            "url": "https://getsincor.com/sinc#buy-usdc",
            "note": f"~{hook_m}M SINC on $0.10–$100 walls. Every USDC buy fills orders → USDC revenue.",
        },
        {
            "id": "platform",
            "title": "SINCOR platform (SINC + AXM billing)",
            "status": "live",
            "url": "https://getsincor.com/buy",
            "completed_usd": platform.get("completed_usd", 0),
            "total_orders": platform.get("total_orders", 0),
            "note": "Subscriptions in SINC, one-off intel in AXM — /buy on Base. No Stripe/PayPal.",
        },
        {
            "id": "tokenlist",
            "title": "Canonical routing (stops rogue-pool harm)",
            "status": "live",
            "url": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
            "note": "Import token list in MetaMask/Rabby — drives real buyers to curve, not fake V2 pool.",
        },
    ]

    return {
        **s,
        "treasury": TREASURY,
        "treasury_sinc": round(treasury_sinc, 0),
        "graduation_pct": grad_pct,
        "graduation_eth_target": GRADUATION_ETH,
        "hook_floor_sinc_m": HOOK_FLOOR_SINC_M,
        "referral_pct": REFERRAL_PCT,
        "streams": streams,
        "buy_url": "https://getsincor.com/sinc",
        "earn_url": "https://getsincor.com/earn",
        "refer_url": "https://getsincor.com/refer",
    }


def social_pack(wallet: str | None = None) -> dict:
    v = fetch_value_summary()
    ref = f"https://getsincor.com/sinc?ref={wallet}" if wallet else "https://getsincor.com/refer"
    grad = v["graduation_pct"]
    hook_m = v.get("sinc_in_hook_pm_m", HOOK_FLOOR_SINC_M)
    sold_m = v.get("sinc_sold_m", 0)
    eth_acc = v.get("curve_eth_accumulated", 0)

    tweet = (
        f"SINC on Base — audited bonding curve + v4 hook walls.\n"
        f"• ~{v.get('sinc_in_curve_m', 65)}M SINC in curve\n"
        f"• {sold_m}M sold · {eth_acc} ETH toward graduation ({grad}%)\n"
        f"• ~{hook_m}M SINC on $0.10+ sell walls\n"
        f"Buy: {ref}\n"
        f"Earn 3% referring: getsincor.com/refer"
    )
    telegram = (
        f"SINC live on Base. Curve spot rises with demand. "
        f"Graduation {grad}% ({eth_acc}/0.5 ETH). "
        f"Hook walls ~{hook_m}M SINC. Buy: {ref}"
    )
    farcaster = tweet[:320]

    return {
        "wallet": wallet,
        "referral_link": ref,
        "twitter": tweet,
        "telegram": telegram,
        "farcaster": farcaster,
        "twitter_intent": "https://twitter.com/intent/tweet?text=" + _urlencode(tweet),
        "telegram_share": "https://t.me/share/url?url=" + _urlencode(ref) + "&text=" + _urlencode(telegram[:200]),
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