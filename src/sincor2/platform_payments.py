"""SINC + AXIOM platform payments — replaces Stripe/PayPal for SINCOR billing."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SPOT_CACHE_TTL_SEC = int(os.environ.get("PLATFORM_SPOT_CACHE_TTL_SEC", "60"))
_spot_cache: dict[str, tuple[float, float | None]] = {}

# Canonical Base mainnet (see CANONICAL_ADDRESSES.md)
SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
AXM = "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822"
TREASURY = os.environ.get("PLATFORM_TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac")
CHAIN_ID = 8453

SINC_DECIMALS = 8
AXM_DECIMALS = 18

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AMOUNT_TOLERANCE_BPS = 150  # 1.5% underpayment slack for spot drift

# SINC = platform subscriptions · AXM = one-off intel / agent execution
PLATFORM_PLANS: dict[str, dict[str, Any]] = {
    "report": {
        "label": "One-Time Report",
        "product_name": "Business Intelligence Report",
        "order_type": "bi_report",
        "usd_reference": 49,
        "token": "AXM",
        "billing": "once",
        # Fixed AXM list price when DEX spot unavailable (agent execution token)
        "fixed_amount": float(os.environ.get("AXM_REPORT_PRICE", "500")),
    },
    "intel": {
        "label": "Monthly Intelligence",
        "product_name": "Professional",
        "order_type": "subscription",
        "usd_reference": 149,
        "token": "SINC",
        "billing": "month",
    },
    "starter": {
        "label": "Starter Agents",
        "product_name": "Starter",
        "order_type": "subscription",
        "usd_reference": 297,
        "token": "SINC",
        "billing": "month",
        "agents": 10,
    },
    "professional": {
        "label": "Professional Agents",
        "product_name": "Professional",
        "order_type": "subscription",
        "usd_reference": 997,
        "token": "SINC",
        "billing": "month",
        "agents": 25,
    },
    "enterprise": {
        "label": "Enterprise Agents",
        "product_name": "Enterprise",
        "order_type": "subscription",
        "usd_reference": 2997,
        "token": "SINC",
        "billing": "month",
        "agents": 42,
    },
}


def fiat_payments_enabled() -> bool:
    return os.environ.get("LEGACY_FIAT_PAYMENTS_ENABLED", "false").lower() == "true"


def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _db_path() -> Path:
    return _root() / "orders.db"


def init_platform_payments_db() -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS platform_subscriptions (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            email TEXT,
            plan_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            token TEXT NOT NULL DEFAULT 'SINC',
            status TEXT NOT NULL DEFAULT 'active',
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            last_tx_hash TEXT,
            last_payment_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS platform_checkouts (
            payment_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            token TEXT NOT NULL,
            amount_atomic TEXT NOT NULL,
            amount_display REAL NOT NULL,
            usd_reference REAL NOT NULL,
            payer_wallet TEXT,
            customer_email TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            tx_hash TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            metadata TEXT
        )"""
    )
    conn.commit()
    conn.close()


def _conn() -> sqlite3.Connection:
    init_platform_payments_db()
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def token_address(symbol: str) -> str:
    s = symbol.upper()
    if s == "SINC":
        return SINC
    if s in ("AXM", "AXIOM"):
        return AXM
    raise ValueError(f"unknown token: {symbol}")


def token_decimals(symbol: str) -> int:
    return SINC_DECIMALS if symbol.upper() == "SINC" else AXM_DECIMALS


def _http_json(url: str, timeout: int = 12) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SINCOR-platform-payments/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


def _cached_spot(key: str, fetcher) -> float | None:
    now = time.monotonic()
    cached = _spot_cache.get(key)
    if cached and now - cached[0] < _SPOT_CACHE_TTL_SEC:
        return cached[1]
    spot = fetcher()
    _spot_cache[key] = (now, spot)
    return spot


def _fetch_sinc_spot_usd_uncached() -> float | None:
    try:
        import sys

        root = str(_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        from launch_content_engine.onchain_stats import fetch_stats

        spot = fetch_stats().get("curve_spot_usd")
        return float(spot) if spot and float(spot) > 0 else None
    except Exception:
        return None


def fetch_sinc_spot_usd() -> float | None:
    return _cached_spot("SINC", _fetch_sinc_spot_usd_uncached)


def _fetch_axm_spot_usd_uncached() -> float | None:
    # CoinGecko (primary — DexScreener often 403s from datacenter IPs)
    cg = _http_json(
        f"https://api.coingecko.com/api/v3/simple/token_price/base"
        f"?contract_addresses={AXM.lower()}&vs_currencies=usd"
    )
    if isinstance(cg, dict):
        row = cg.get(AXM.lower()) or cg.get(AXM) or next(iter(cg.values()), None)
        if isinstance(row, dict) and row.get("usd"):
            try:
                p = float(row["usd"])
                if p > 0:
                    return p
            except (TypeError, ValueError):
                pass

    data = _http_json(f"https://api.dexscreener.com/latest/dex/tokens/{AXM}")
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            chain = str(pair.get("chainId", "")).lower()
            if chain not in ("base", "8453"):
                continue
            price = pair.get("priceUsd")
            if price:
                try:
                    p = float(price)
                    if p > 0:
                        return p
                except (TypeError, ValueError):
                    continue

    fallback = os.environ.get("AXM_USD_FALLBACK", "").strip()
    if fallback:
        try:
            p = float(fallback)
            if p > 0:
                return p
        except ValueError:
            pass
    return None


def fetch_axm_spot_usd() -> float | None:
    return _cached_spot("AXM", _fetch_axm_spot_usd_uncached)


def usd_to_token_amount(usd: float, token: str) -> tuple[float, float | None]:
    """Return (display_amount, spot_usd)."""
    if token.upper() == "SINC":
        spot = fetch_sinc_spot_usd()
    else:
        spot = fetch_axm_spot_usd() or float(os.environ.get("AXM_USD_FALLBACK", "0") or 0) or None
    if not spot or spot <= 0:
        return 0.0, None
    return round(usd / spot, 8 if token.upper() == "SINC" else 4), spot


def display_to_atomic(amount: float, token: str) -> int:
    dec = token_decimals(token)
    return int(round(amount * (10**dec)))


def atomic_to_display(amount_atomic: int, token: str) -> float:
    dec = token_decimals(token)
    return amount_atomic / (10**dec)


def _plan_token_quote(
    plan: dict[str, Any],
    *,
    sinc_spot: float | None = None,
    axm_spot: float | None = None,
) -> tuple[float, float | None, str]:
    """Return (display_amount, spot_usd, pricing_mode)."""
    token = plan["token"]
    fixed = plan.get("fixed_amount")
    if fixed is not None:
        try:
            amt = float(fixed)
            if amt > 0:
                return amt, None, "fixed"
        except (TypeError, ValueError):
            pass
    if token.upper() == "SINC":
        spot = sinc_spot if sinc_spot is not None else fetch_sinc_spot_usd()
    else:
        spot = axm_spot if axm_spot is not None else fetch_axm_spot_usd()
    if not spot or spot <= 0:
        return 0.0, None, "spot"
    dec = 8 if token.upper() == "SINC" else 4
    return round(plan["usd_reference"] / spot, dec), spot, "spot"


def list_plans() -> list[dict[str, Any]]:
    sinc_spot = fetch_sinc_spot_usd()
    axm_spot = fetch_axm_spot_usd()
    out = []
    for plan_id, plan in PLATFORM_PLANS.items():
        token = plan["token"]
        display, spot, mode = _plan_token_quote(
            plan,
            sinc_spot=sinc_spot if token.upper() == "SINC" else None,
            axm_spot=axm_spot if token.upper() != "SINC" else None,
        )
        out.append(
            {
                "id": plan_id,
                "label": plan["label"],
                "product_name": plan["product_name"],
                "usd_reference": plan["usd_reference"],
                "billing": plan["billing"],
                "token": token,
                "token_address": token_address(token),
                "amount_display": display,
                "amount_atomic": str(display_to_atomic(display, token)) if display > 0 else None,
                "spot_usd": spot,
                "pricing_mode": mode,
                "treasury": TREASURY,
                "chain_id": CHAIN_ID,
                "spot_available": display > 0,
            }
        )
    return out


def get_plan(plan_id: str) -> dict[str, Any] | None:
    return PLATFORM_PLANS.get((plan_id or "").strip().lower())


def create_checkout(
    plan_id: str,
    *,
    payer_wallet: str = "",
    customer_email: str = "",
) -> dict[str, Any]:
    plan = get_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "unknown_plan"}

    token = plan["token"]
    display, spot, mode = _plan_token_quote(plan)
    if display <= 0:
        return {"ok": False, "error": "price_unavailable", "token": token}

    atomic = display_to_atomic(display, token)
    now = datetime.now(timezone.utc)
    payment_id = f"PLAT-{now.strftime('%Y%m%d%H%M%S')}-{plan_id[:4].upper()}"

    expires = now.timestamp() + 3600
    expires_at = datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()

    with _conn() as conn:
        conn.execute(
            """INSERT INTO platform_checkouts
               (payment_id, plan_id, product_name, token, amount_atomic, amount_display,
                usd_reference, payer_wallet, customer_email, status, created_at, expires_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                payment_id,
                plan_id,
                plan["product_name"],
                token,
                str(atomic),
                display,
                plan["usd_reference"],
                payer_wallet.lower() if payer_wallet else "",
                customer_email,
                now.isoformat(),
                expires_at,
                json.dumps({"billing": plan["billing"], "order_type": plan["order_type"]}),
            ),
        )
        conn.commit()

    return {
        "ok": True,
        "payment_id": payment_id,
        "plan_id": plan_id,
        "plan_label": plan["label"],
        "product_name": plan["product_name"],
        "token": token,
        "token_address": token_address(token),
        "token_decimals": token_decimals(token),
        "amount_display": display,
        "amount_atomic": str(atomic),
        "usd_reference": plan["usd_reference"],
        "spot_usd": spot,
        "pricing_mode": mode,
        "treasury": TREASURY,
        "chain_id": CHAIN_ID,
        "billing": plan["billing"],
        "expires_at": expires_at,
        "message": f"Send {display} {token} on Base to {TREASURY}",
    }


def _rpc_call(method: str, params: list) -> Any:
    rpc = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        rpc,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "SINCOR-platform-payments/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def _parse_transfer_logs(receipt: dict, token_addr: str, treasury: str) -> list[dict[str, Any]]:
    treasury_l = treasury.lower()
    token_l = token_addr.lower()
    matches = []
    for log in receipt.get("logs") or []:
        if (log.get("address") or "").lower() != token_l:
            continue
        topics = log.get("topics") or []
        if not topics or topics[0].lower() != TRANSFER_TOPIC.lower():
            continue
        if len(topics) < 3:
            continue
        to_addr = "0x" + topics[2][-40:].lower()
        if to_addr != treasury_l:
            continue
        amount = int(log.get("data", "0x0"), 16)
        from_addr = "0x" + topics[1][-40:].lower()
        matches.append({"from": from_addr, "to": to_addr, "amount_atomic": amount})
    return matches


def verify_treasury_transfer(
    tx_hash: str,
    *,
    token: str,
    expected_atomic: int,
    treasury: str | None = None,
    payer_wallet: str = "",
) -> dict[str, Any]:
    """Shared on-chain ERC-20 treasury transfer verification."""
    treasury = (treasury or TREASURY).lower()
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return {"ok": False, "error": "invalid_tx_hash"}

    try:
        receipt = _rpc_call("eth_getTransactionReceipt", [tx_hash])
    except Exception as e:
        return {"ok": False, "error": "rpc_failed", "detail": str(e)}

    if not receipt:
        return {"ok": False, "error": "tx_pending"}
    if receipt.get("status") != "0x1":
        return {"ok": False, "error": "tx_failed"}

    token_addr = token_address(token)
    min_amount = expected_atomic * (10000 - AMOUNT_TOLERANCE_BPS) // 10000
    transfers = _parse_transfer_logs(receipt, token_addr, treasury)
    if not transfers:
        return {"ok": False, "error": "no_treasury_transfer", "token": token}

    paid = max(t["amount_atomic"] for t in transfers)
    if paid < min_amount:
        return {
            "ok": False,
            "error": "insufficient_amount",
            "paid_display": atomic_to_display(paid, token),
            "required_display": atomic_to_display(expected_atomic, token),
            "token": token,
        }

    payer = transfers[0]["from"]
    if payer_wallet and payer_wallet.lower() != payer:
        return {"ok": False, "error": "payer_wallet_mismatch"}

    return {
        "ok": True,
        "payer_wallet": payer,
        "amount_atomic": paid,
        "amount_display": atomic_to_display(paid, token),
        "token": token,
    }


def activate_subscription(
    *,
    wallet: str,
    plan_id: str,
    product_name: str,
    token: str,
    tx_hash: str,
    payment_id: str,
    email: str = "",
    period_days: int = 30,
) -> dict[str, Any]:
    """Create or extend a wallet-linked subscription after verified payment."""
    import secrets
    from datetime import timedelta

    wallet = wallet.lower()
    now = datetime.now(timezone.utc)
    period_start = now.isoformat()
    period_end = (now + timedelta(days=period_days)).isoformat()

    with _conn() as conn:
        existing = conn.execute(
            "SELECT id, period_end FROM platform_subscriptions WHERE wallet=? AND plan_id=? AND status='active'",
            (wallet, plan_id),
        ).fetchone()

        if existing:
            sub_id = existing["id"]
            try:
                prev_end = datetime.fromisoformat(existing["period_end"].replace("Z", "+00:00"))
                base = prev_end if prev_end > now else now
            except ValueError:
                base = now
            period_end = (base + timedelta(days=period_days)).isoformat()
            conn.execute(
                """UPDATE platform_subscriptions SET period_end=?, last_tx_hash=?, last_payment_id=?,
                   email=COALESCE(NULLIF(?, ''), email), updated_at=?, status='active'
                   WHERE id=?""",
                (period_end, tx_hash, payment_id, email, now.isoformat(), sub_id),
            )
        else:
            sub_id = f"sub_{secrets.token_hex(8)}"
            conn.execute(
                """INSERT INTO platform_subscriptions
                   (id, wallet, email, plan_id, product_name, token, status,
                    period_start, period_end, last_tx_hash, last_payment_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
                (
                    sub_id, wallet, email, plan_id, product_name, token,
                    period_start, period_end, tx_hash, payment_id,
                    now.isoformat(), now.isoformat(),
                ),
            )
        conn.commit()

    return {
        "subscription_id": sub_id,
        "wallet": wallet,
        "plan_id": plan_id,
        "period_end": period_end,
        "status": "active",
    }


def get_subscription(wallet: str, plan_id: str | None = None) -> dict[str, Any] | None:
    wallet = (wallet or "").lower()
    if not wallet:
        return None
    with _conn() as conn:
        if plan_id:
            row = conn.execute(
                """SELECT * FROM platform_subscriptions WHERE wallet=? AND plan_id=?
                   ORDER BY period_end DESC LIMIT 1""",
                (wallet, plan_id),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM platform_subscriptions WHERE wallet=?
                   ORDER BY period_end DESC LIMIT 1""",
                (wallet,),
            ).fetchone()
    return dict(row) if row else None


def list_subscriptions(wallet: str) -> list[dict[str, Any]]:
    wallet = (wallet or "").lower()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM platform_subscriptions WHERE wallet=? ORDER BY period_end DESC",
            (wallet,),
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_wallet_subscriptions(wallet: str) -> int:
    wallet = (wallet or "").lower()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE platform_subscriptions SET status='cancelled', updated_at=? WHERE wallet=? AND status='active'",
            (now, wallet),
        )
        conn.commit()
        return cur.rowcount


def subscriptions_needing_renewal(within_days: int = 7) -> list[dict[str, Any]]:
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) + timedelta(days=within_days)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM platform_subscriptions
               WHERE status='active' AND period_end <= ? AND period_end >= ?""",
            (cutoff, now),
        ).fetchall()
    return [dict(r) for r in rows]


def verify_checkout(
    payment_id: str,
    tx_hash: str,
    *,
    customer_email: str = "",
    payer_wallet: str = "",
) -> dict[str, Any]:
    if not payment_id or not tx_hash:
        return {"ok": False, "error": "payment_id_and_tx_hash_required"}
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return {"ok": False, "error": "invalid_tx_hash"}

    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM platform_checkouts WHERE payment_id=?", (payment_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "checkout_not_found"}
        if row["status"] == "completed":
            return {"ok": True, "status": "already_completed", "tx_hash": row["tx_hash"]}

        expires = row["expires_at"]
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return {"ok": False, "error": "checkout_expired"}

        dup = conn.execute(
            "SELECT payment_id FROM platform_checkouts WHERE tx_hash=? AND status='completed'",
            (tx_hash,),
        ).fetchone()
        if dup:
            return {"ok": False, "error": "tx_already_used"}

    token = row["token"]
    expected = int(row["amount_atomic"])
    vr = verify_treasury_transfer(
        tx_hash, token=token, expected_atomic=expected, payer_wallet=payer_wallet
    )
    if not vr.get("ok"):
        return vr
    payer = vr["payer_wallet"]
    paid = vr["amount_atomic"]

    meta = json.loads(row["metadata"] or "{}")
    order_id = f"TOKEN-ORD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    with _conn() as conn:
        conn.execute(
            """UPDATE platform_checkouts SET status='completed', tx_hash=?, payer_wallet=?,
               customer_email=COALESCE(NULLIF(?, ''), customer_email) WHERE payment_id=?""",
            (tx_hash, payer, customer_email, payment_id),
        )
        conn.commit()

    return {
        "ok": True,
        "status": "verified",
        "payment_id": payment_id,
        "order_id": order_id,
        "tx_hash": tx_hash,
        "payer_wallet": payer,
        "product_name": row["product_name"],
        "plan_id": row["plan_id"],
        "token": token,
        "amount_display": atomic_to_display(paid, token),
        "usd_reference": row["usd_reference"],
        "order_type": meta.get("order_type", "generic"),
        "billing": meta.get("billing", "once"),
        "customer_email": customer_email or row["customer_email"],
        "amount_atomic": paid,
    }