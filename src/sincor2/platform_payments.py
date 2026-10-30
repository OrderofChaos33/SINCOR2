"""SINC + AXIOM platform payments — replaces Stripe/PayPal for SINCOR billing.

CEO 2026-08-18/19: AXM primary address corrected to 0x4c3fb66f14fbaa2088c9ae91017ba770da53715a.
CEO 2026-08-19: SINC updated to new 8-decimal live contract 0xe1D836087F6573b665d25CE088793E916D7892f8.
New flows prefer AXM. SINC is legacy for residual subscription renewals only.
CEO 2026-09-01: USD-priced human checkout falls back to Base USDC when AXM has
no DEX/CoinGecko spot. A2A skills stay AXM-only (fixed wei).

Checkout state machine (B1 receipt-finality hardening):
    pending ──► verifying ──► awaiting_finality ──► finalized ──► fulfilled
       │             │                │                  │
       │             │                ▼                  ▼
       │             │            reverted            disputed
       │             │         (reorg/dropped      (reorg detected
       │             │          pre-fulfillment)    AFTER fulfillment)
       │             ▼
       │          expired (TTL passed while pending/verifying/awaiting_finality)
       ▼
    blocked (terminal compliance denial: OFAC sanctions hit, or screening
             unavailable while PLATFORM_OFAC_STRICT=true)

- `pending`: checkout created, awaiting a payment claim.
- `verifying`: transient claim lock while one verify call owns the row.
- `awaiting_finality`: receipt is valid on-chain but below the confirmation
  depth. NOT provisioned. The client re-polls verify until the depth is met.
- `finalized`: confirmation depth reached (and sanctions screen passed);
  safe to provision exactly once.
- `fulfilled`: entitlements provisioned. (Legacy rows may carry `completed`;
  treated as `fulfilled` on read.)
- `reverted`: tx disappeared from chain (reorg/drop) BEFORE fulfillment —
  never silently left fulfilled.
- `disputed`: tx disappeared AFTER fulfillment — needs human remediation
  (e.g. entitlement clawback); never silently stays fulfilled.
- `blocked`: compliance denial; never provisions.

Every reorg/blocked transition writes an audit row to
`platform_payment_audits`.
"""

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

from sincor2.onchain.constants import (
    AXIOM_TOKEN,
    AXM_DECIMALS as _AXM_DECIMALS,
    BASE_CHAIN_ID,
    SINC_DECIMALS as _SINC_DECIMALS,
    SINC_TOKEN,
    TREASURY,
    USDC_DECIMALS as _USDC_DECIMALS,
    USDC_TOKEN,
    resolve_address,
)

# Canonical Base mainnet (see sincor2.onchain.constants)
SINC = SINC_TOKEN
AXM = AXIOM_TOKEN
USDC = USDC_TOKEN
TREASURY = resolve_address("PLATFORM_TREASURY_ADDRESS", TREASURY)
CHAIN_ID = BASE_CHAIN_ID

SINC_DECIMALS = _SINC_DECIMALS
AXM_DECIMALS = _AXM_DECIMALS
USDC_DECIMALS = _USDC_DECIMALS

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AMOUNT_TOLERANCE_BPS = 150  # 1.5% underpayment slack for spot drift

# --- B1: chain confirmation-depth finality gates ---------------------------------
# A valid receipt is NOT finality: shallow reorgs can still drop the tx. Orders
# must stay `pending`/`awaiting_finality` (never provisioned) until the payment
# tx is buried under `finality_confirmations(chain_id)` blocks.
#
# Base produces a block every ~2s. 12 confirmations (~24s) is the default:
# cheap enough for human-checkout UX, deep enough that a reorg at that depth
# is for all practical purposes impossible on a healthy L2.
FINALITY_CONFIRMATIONS_BY_CHAIN: dict[int, int] = {
    BASE_CHAIN_ID: 12,  # 8453 — Base mainnet
}
# Fallback for chains with no configured entry. Named (not a magic number) so
# operators can reason about it; per-chain overrides below take precedence.
FINALITY_CONFIRMATIONS_FALLBACK = 12


def finality_confirmations(chain_id: int = CHAIN_ID) -> int:
    """Confirmation depth required before a receipt counts as final.

    Precedence: PLATFORM_FINALITY_CONFIRMATIONS_<chain_id> (e.g.
    PLATFORM_FINALITY_CONFIRMATIONS_8453) > PLATFORM_FINALITY_CONFIRMATIONS_JSON
    (a JSON object like '{"8453": 12}') > the per-chain table above > fallback.
    """
    env = os.environ.get(f"PLATFORM_FINALITY_CONFIRMATIONS_{chain_id}", "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    raw = os.environ.get("PLATFORM_FINALITY_CONFIRMATIONS_JSON", "").strip()
    if raw:
        try:
            table = json.loads(raw)
            if str(chain_id) in table:
                return max(1, int(table[str(chain_id)]))
        except (ValueError, TypeError):
            pass
    return max(
        1,
        FINALITY_CONFIRMATIONS_BY_CHAIN.get(
            chain_id, FINALITY_CONFIRMATIONS_FALLBACK
        ),
    )

# AXM = primary for new flows (reports, one-offs, A2A). SINC = legacy subscriptions only.
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
        "token": "AXM",  # CEO 2026-08-19: new flows AXM-only
        "billing": "month",
    },
    "starter": {
        "label": "Starter Agents",
        "product_name": "Starter",
        "order_type": "subscription",
        "usd_reference": 297,
        "token": "AXM",  # CEO 2026-08-19: new flows AXM-only
        "billing": "month",
        "agents": 10,
    },
    "professional": {
        "label": "Professional Agents",
        "product_name": "Professional",
        "order_type": "subscription",
        "usd_reference": 997,
        "token": "AXM",  # CEO 2026-08-19: new flows AXM-only
        "billing": "month",
        "agents": 25,
    },
    "enterprise": {
        "label": "Enterprise Agents",
        "product_name": "Enterprise",
        "order_type": "subscription",
        "usd_reference": 2997,
        "token": "AXM",  # CEO 2026-08-19: new flows AXM-only
        "billing": "month",
        "agents": 45,  # up to 45; orchestration agent decides dispatch count
    },
}


def fiat_payments_enabled() -> bool:
    return os.environ.get("LEGACY_FIAT_PAYMENTS_ENABLED", "false").lower() == "true"


def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _db_path() -> Path:
    from sincor2.data_paths import orders_db_path

    return orders_db_path()


def init_platform_payments_db() -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS platform_subscriptions (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            email TEXT,
            plan_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            token TEXT NOT NULL DEFAULT 'AXM',
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
        """CREATE TABLE IF NOT EXISTS platform_activation_journal (
            payment_id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            subscription_id TEXT NOT NULL DEFAULT '',
            period_end TEXT NOT NULL,
            applied_at TEXT NOT NULL
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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(platform_checkouts)").fetchall()}
    if "idempotency_key" not in cols:
        conn.execute("ALTER TABLE platform_checkouts ADD COLUMN idempotency_key TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_platform_checkouts_idem "
        "ON platform_checkouts(idempotency_key, status)"
    )
    # B1: one tx pays for one checkout. The uniqueness must already hold at
    # 'finalized' (pre-provisioning), otherwise two checkouts could finalize
    # the same tx and double-provision. The v1 index covered only
    # fulfilled/completed, so drop it and recreate with the wider predicate.
    conn.execute("DROP INDEX IF EXISTS idx_platform_checkouts_tx_hash")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_checkouts_tx_hash_v2 "
        "ON platform_checkouts(tx_hash) WHERE tx_hash IS NOT NULL AND tx_hash != '' "
        "AND status IN ('finalized', 'fulfilled', 'completed')"
    )
    # B1: append-only audit trail for reorg / compliance state transitions.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS platform_payment_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id TEXT NOT NULL,
            tx_hash TEXT,
            event TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_platform_payment_audits_payment
           ON platform_payment_audits(payment_id)"""
    )
    # D2: shared consumed-tx registry (see claim_consumed_onchain_tx).
    conn.execute(CONSUMED_TX_DDL)
    conn.commit()
    conn.close()


def _audit_payment_event(
    payment_id: str,
    event: str,
    tx_hash: str = "",
    detail: str = "",
) -> None:
    """Append an audit record for a reorg/compliance state transition."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO platform_payment_audits
               (payment_id, tx_hash, event, detail, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                payment_id,
                tx_hash or None,
                event,
                detail,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def list_payment_audits(payment_id: str) -> list[dict[str, Any]]:
    """Audit trail for one checkout (reorgs, sanctions blocks, finality events)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM platform_payment_audits WHERE payment_id=? ORDER BY id",
            (payment_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _conn() -> sqlite3.Connection:
    init_platform_payments_db()
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


# --- D2: shared consumed_onchain_txs registry (cross-ledger double-spend) --
# Before D2, each ledger enforced UNIQUE(tx_hash) only inside itself:
# platform_checkouts had a partial unique index, x402_challenges a unique
# index on its own table, and the rail tables (rail_events /
# rail_sub_settles, in the separate revenue DB) carried tx_hash as plain
# TEXT with no index at all. One on-chain transfer could therefore fulfill
# TWO entitlements in two different systems (reproduced 2026-10-26: one
# 297 USDC transfer settled both a rail pack and a platform 'starter'
# checkout).
#
# The registry below is the single system-wide claim book. It lives in
# the orders DB (shared by platform_payments and x402_payments); the rail
# modules, whose own ledgers live in the revenue DB, claim here too, via
# claim_consumed_onchain_tx(). A tx_hash may be claimed by exactly ONE
# entitlement, identified by ``claim_ref``:
#   * platform checkout  -> claim_ref = payment_id,  claim_system="platform"
#   * x402 challenge     -> claim_ref = challenge_id, claim_system="x402"
#   * rail pack settle   -> claim_ref = challenge_id, claim_system="rail_pack"
#   * rail subscription  -> claim_ref = challenge_id, claim_system="rail_sub"
# Rail settles ride on an x402 challenge, so for a rail settle the x402
# claim and the rail claim name the SAME claim_ref (the challenge_id) and
# are the same entitlement, not two: re-claiming with the same claim_ref
# is an idempotent success (settle retries must keep working), while any
# claim for a tx_hash already held under a DIFFERENT claim_ref is refused
# with tx_hash_already_used and must not fulfill.
CONSUMED_TX_DDL = """CREATE TABLE IF NOT EXISTS consumed_onchain_txs (
    tx_hash TEXT PRIMARY KEY,
    claim_system TEXT NOT NULL,
    claim_ref TEXT NOT NULL,
    payer_wallet TEXT NOT NULL DEFAULT '',
    claimed_at TEXT NOT NULL
)"""


def _ensure_consumed_registry(conn: sqlite3.Connection) -> None:
    conn.execute(CONSUMED_TX_DDL)


def _registry_conn() -> sqlite3.Connection:
    """Lightweight connection for registry-only work.

    Deliberately NOT _conn(): that runs the full platform init on every
    call, whose conditional ALTER TABLE migrations race under
    concurrent settles. The registry helpers need only their own table;
    CREATE TABLE IF NOT EXISTS is safe under concurrency with a
    generous busy timeout.
    """
    c = sqlite3.connect(_db_path(), timeout=30.0)
    c.row_factory = sqlite3.Row
    c.execute(CONSUMED_TX_DDL)
    c.commit()
    return c


def normalize_tx_hash(tx_hash: Any) -> str:
    return str(tx_hash or "").strip().lower()


def get_consumed_onchain_tx(tx_hash: str) -> dict[str, Any] | None:
    """Return the registry row for a tx_hash, or None if unclaimed."""
    tx = normalize_tx_hash(tx_hash)
    if not tx:
        return None
    with _registry_conn() as conn:
        row = conn.execute(
            "SELECT * FROM consumed_onchain_txs WHERE tx_hash=?", (tx,)
        ).fetchone()
    return dict(row) if row else None


def claim_consumed_onchain_tx(
    tx_hash: str,
    *,
    claim_system: str,
    claim_ref: str,
    payer_wallet: str = "",
) -> dict[str, Any]:
    """Atomically claim an on-chain tx for exactly one entitlement.

    BEGIN IMMEDIATE + INSERT on the PRIMARY KEY(tx_hash): the first
    claimant wins. Re-claiming with the same ``claim_ref`` returns
    ``{"ok": True, "already_claimed": True}`` (idempotent retries of the
    same entitlement). A claim under any other ``claim_ref`` returns
    ``{"ok": False, "error": "tx_hash_already_used", ...}`` naming the
    holding system/ref, and the caller MUST NOT fulfill.
    """
    tx = normalize_tx_hash(tx_hash)
    ref = str(claim_ref or "").strip()
    if not tx.startswith("0x") or len(tx) != 66:
        return {"ok": False, "error": "invalid_tx_hash"}
    if not ref:
        return {"ok": False, "error": "claim_ref_required"}
    conn = _registry_conn()
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            return {"ok": False, "error": "store_busy"}
        try:
            conn.execute(
                "INSERT INTO consumed_onchain_txs "
                "(tx_hash, claim_system, claim_ref, payer_wallet, claimed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tx, str(claim_system), ref,
                 str(payer_wallet or "").strip().lower(),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return {"ok": True, "already_claimed": False,
                    "tx_hash": tx, "claim_system": str(claim_system),
                    "claim_ref": ref}
        except sqlite3.IntegrityError:
            existing = conn.execute(
                "SELECT * FROM consumed_onchain_txs WHERE tx_hash=?",
                (tx,),
            ).fetchone()
            conn.rollback()
            if existing is not None and existing["claim_ref"] == ref:
                return {"ok": True, "already_claimed": True,
                        "tx_hash": tx,
                        "claim_system": existing["claim_system"],
                        "claim_ref": existing["claim_ref"]}
            return {
                "ok": False, "error": "tx_hash_already_used",
                "tx_hash": tx,
                "consumed_by_system": (
                    existing["claim_system"] if existing else ""),
                "consumed_by_ref": (
                    existing["claim_ref"] if existing else ""),
            }
    finally:
        conn.close()


def release_consumed_onchain_tx(tx_hash: str, *, claim_ref: str) -> bool:
    """Release a claim (only by its owning claim_ref). Used when the
    checkout that claimed a tx is reverted pre-fulfillment (reorg/drop):
    no entitlement was granted, so the claim must not linger. Returns
    True when a row was deleted."""
    tx = normalize_tx_hash(tx_hash)
    if not tx:
        return False
    with _registry_conn() as conn:
        cur = conn.execute(
            "DELETE FROM consumed_onchain_txs WHERE tx_hash=? AND claim_ref=?",
            (tx, str(claim_ref or "").strip()),
        )
        conn.commit()
        return cur.rowcount == 1


def token_address(symbol: str) -> str:
    s = symbol.upper()
    if s == "SINC":
        return SINC
    if s in ("AXM", "AXIOM"):
        return AXM
    if s == "USDC":
        return USDC
    raise ValueError(f"unknown token: {symbol}")


def token_decimals(symbol: str) -> int:
    s = symbol.upper()
    if s == "SINC":
        return SINC_DECIMALS
    if s == "USDC":
        return USDC_DECIMALS
    return AXM_DECIMALS


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
    """Official SINC quote price — $0.15 floor ($150M / 1B tokens)."""
    floor = float(os.environ.get("SINC_FLOOR_USD", "0.15"))
    try:
        import sys

        root = str(_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        from launch_content_engine.onchain_stats import SINC_FLOOR_USD

        floor = float(SINC_FLOOR_USD)
    except Exception:
        pass
    return floor if floor > 0 else None


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
    t = token.upper()
    if t == "USDC":
        return round(usd, 2), 1.0
    if t == "SINC":
        spot = fetch_sinc_spot_usd()
    else:
        spot = fetch_axm_spot_usd() or float(os.environ.get("AXM_USD_FALLBACK", "0") or 0) or None
    if not spot or spot <= 0:
        return 0.0, None
    return round(usd / spot, 8 if t == "SINC" else 4), spot


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


def settle_plan_quote(
    plan: dict[str, Any],
    *,
    sinc_spot: float | None = None,
    axm_spot: float | None = None,
) -> tuple[str, float, float | None, str]:
    """Return (token, display_amount, spot_usd, pricing_mode).

    Reports / A2A-style fixed AXM amounts stay AXM. USD-priced human checkout
    (Starter $297, Professional, Enterprise, intel) falls back to Base USDC
    1:1 when AXM has no live DEX/CoinGecko spot. SINC is never a buy path.
    """
    preferred = plan["token"]
    display, spot, mode = _plan_token_quote(
        plan, sinc_spot=sinc_spot, axm_spot=axm_spot
    )
    if display > 0:
        return preferred, display, spot, mode
    usd = float(plan.get("usd_reference") or 0)
    if usd > 0 and preferred.upper() != "SINC":
        return "USDC", round(usd, 2), 1.0, "usdc_fallback"
    return preferred, 0.0, None, mode


def list_plans() -> list[dict[str, Any]]:
    sinc_spot = fetch_sinc_spot_usd()
    axm_spot = fetch_axm_spot_usd()
    out = []
    for plan_id, plan in PLATFORM_PLANS.items():
        preferred = plan["token"]
        token, display, spot, mode = settle_plan_quote(
            plan,
            sinc_spot=sinc_spot if preferred.upper() == "SINC" else None,
            axm_spot=axm_spot if preferred.upper() != "SINC" else None,
        )
        out.append(
            {
                "id": plan_id,
                "label": plan["label"],
                "product_name": plan["product_name"],
                "usd_reference": plan["usd_reference"],
                "billing": plan["billing"],
                "token": token,
                "preferred_token": preferred,
                "token_address": token_address(token) if display > 0 else token_address(preferred),
                "amount_display": display,
                "amount_atomic": str(display_to_atomic(display, token)) if display > 0 else None,
                "spot_usd": spot,
                "pricing_mode": mode,
                "treasury": TREASURY,
                "chain_id": CHAIN_ID,
                "spot_available": display > 0,
                "agents": plan.get("agents"),
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
    idempotency_key: str = "",
) -> dict[str, Any]:
    plan = get_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "unknown_plan"}

    token, display, spot, mode = settle_plan_quote(plan)
    if display <= 0:
        return {"ok": False, "error": "price_unavailable", "token": token}

    atomic = display_to_atomic(display, token)
    now = datetime.now(timezone.utc)
    wallet = payer_wallet.lower() if payer_wallet else ""
    key = (idempotency_key or "").strip()
    if not key and wallet:
        window = now.strftime("%Y%m%d%H")
        key = f"{wallet}:{plan_id}:{window}"

    if key:
        with _conn() as conn:
            existing = conn.execute(
                """SELECT * FROM platform_checkouts
                   WHERE idempotency_key=? AND status IN
                       ('pending', 'verifying', 'awaiting_finality', 'finalized',
                        'fulfilled', 'completed')
                   ORDER BY created_at DESC LIMIT 1""",
                (key,),
            ).fetchone()
        if existing:
            expires = existing["expires_at"]
            still_open = True
            if expires:
                try:
                    still_open = datetime.fromisoformat(
                        expires.replace("Z", "+00:00")
                    ) >= now
                except ValueError:
                    still_open = True
            if existing["status"] in ("completed", "fulfilled") or still_open:
                return _checkout_payload(dict(existing), reused=True, spot=spot, mode=mode)

    expire_stale_checkouts()

    payment_id = f"PLAT-{now.strftime('%Y%m%d%H%M%S')}-{plan_id[:4].upper()}"
    expires = now.timestamp() + 3600
    expires_at = datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()

    with _conn() as conn:
        conn.execute(
            """INSERT INTO platform_checkouts
               (payment_id, plan_id, product_name, token, amount_atomic, amount_display,
                usd_reference, payer_wallet, customer_email, status, created_at, expires_at,
                metadata, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
            (
                payment_id,
                plan_id,
                plan["product_name"],
                token,
                str(atomic),
                display,
                plan["usd_reference"],
                wallet,
                customer_email,
                now.isoformat(),
                expires_at,
                json.dumps({
                    "billing": plan["billing"],
                    "order_type": plan["order_type"],
                    "pricing_mode": mode,
                    "preferred_token": plan["token"],
                }),
                key,
            ),
        )
        conn.commit()

    return {
        "ok": True,
        "reused": False,
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
        "idempotency_key": key,
        "message": f"Send {display} {token} on Base to {TREASURY}",
    }


def _checkout_payload(row: dict[str, Any], *, reused: bool, spot: float | None, mode: str) -> dict[str, Any]:
    token = row["token"]
    display = row["amount_display"]
    return {
        "ok": True,
        "reused": reused,
        "payment_id": row["payment_id"],
        "plan_id": row["plan_id"],
        "product_name": row["product_name"],
        "token": token,
        "token_address": token_address(token),
        "token_decimals": token_decimals(token),
        "amount_display": display,
        "amount_atomic": str(row["amount_atomic"]),
        "usd_reference": row["usd_reference"],
        "spot_usd": spot,
        "pricing_mode": mode,
        "treasury": TREASURY,
        "chain_id": CHAIN_ID,
        "status": row["status"],
        "expires_at": row["expires_at"],
        "idempotency_key": row.get("idempotency_key") or "",
        "message": f"Send {display} {token} on Base to {TREASURY}",
    }


def _rpc_urls() -> list[str]:
    urls: list[str] = []
    primary = (os.environ.get("BASE_RPC_URL") or "").strip()
    if primary:
        urls.append(primary)
    extra = (os.environ.get("BASE_RPC_URLS") or "").strip()
    if extra:
        urls.extend(u.strip() for u in extra.split(",") if u.strip())
    for fallback in ("https://mainnet.base.org", "https://base.llamarpc.com"):
        if fallback not in urls:
            urls.append(fallback)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _rpc_call(method: str, params: list, attempts: int = 3) -> Any:
    last_err: Exception | None = None
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for url in _rpc_urls():
        for i in range(max(1, attempts)):
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "SINCOR-platform-payments/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode())
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result")
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as err:
                last_err = err
                time.sleep(0.25 * (2 ** i))
    raise last_err or RuntimeError("rpc_failed")


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


# --- B2: OFAC sanctions screening on the payment path ------------------------------
# The sender address recovered from the verified transfer is screened against
# the vendored OFAC SDN snapshot (sincor2.defi.ofac_sdn). Strict mode defaults
# to fail-closed for the payment path: a sanctioned payer is ALWAYS blocked,
# and an unavailable/stale screening service blocks too unless the operator
# explicitly opts out via PLATFORM_OFAC_STRICT=false (audited when it happens).
#
# FOUNDER DECISION REQUIRED: this implements the screening *mechanism*.
# Jurisdiction-specific legal sign-off from qualified compliance counsel is
# still needed before relying on it for any regulated activity.

_OFAC_ORACLE_RETRY_SEC = 60  # retry building the oracle this often after failure

_ofac_oracle = None
_ofac_oracle_error_at: float | None = None


def ofac_strict_mode() -> bool:
    """Fail-closed default for the payment path.

    PLATFORM_OFAC_STRICT=false opts out (fail-open on screening outage only —
    a sanctions HIT always blocks regardless of this flag).
    """
    return os.environ.get("PLATFORM_OFAC_STRICT", "true").lower() == "true"


def _get_ofac_oracle():
    """Lazily built, cached OFAC screening oracle. Retries after failures."""
    global _ofac_oracle, _ofac_oracle_error_at
    if _ofac_oracle is not None:
        return _ofac_oracle
    if _ofac_oracle_error_at is not None and (
        time.time() - _ofac_oracle_error_at < _OFAC_ORACLE_RETRY_SEC
    ):
        raise RuntimeError("ofac_oracle_unavailable_cached")
    try:
        from sincor2.defi.ofac_sdn import build_ofac_oracle

        _ofac_oracle = build_ofac_oracle()
        _ofac_oracle_error_at = None
        return _ofac_oracle
    except Exception:
        _ofac_oracle_error_at = time.time()
        raise


def screen_payer_sanctions(payer_wallet: str) -> dict[str, Any]:
    """Screen a payer wallet against the OFAC SDN snapshot. Never raises.

    Returns {'verdict': 'pass'|'hit'|'unavailable', 'detail': str}.
    A stale (>48h) list is treated as 'unavailable', not a pass — strict mode
    must fail closed rather than trust an expired list.
    """
    if not payer_wallet:
        return {"verdict": "unavailable", "detail": "no_payer_wallet"}
    try:
        oracle = _get_ofac_oracle()
    except Exception as exc:
        return {"verdict": "unavailable", "detail": f"oracle_unavailable: {exc}"}
    try:
        from sincor2.defi.compliance_automation import Verdict
        from sincor2.defi.ofac_sdn import screen_wallet_address

        result = screen_wallet_address(oracle, payer_wallet)
    except Exception as exc:
        # ComplianceError (provider unreachable) and any other screening
        # failure both land here: fail-closed under strict mode.
        return {"verdict": "unavailable", "detail": f"screen_error: {exc}"}
    if result.verdict == Verdict.FAIL:
        return {"verdict": "hit", "detail": result.detail or "sanctions_hit"}
    if result.verdict != Verdict.PASS:
        return {
            "verdict": "unavailable",
            "detail": f"screen_not_pass: {result.verdict} {result.detail or ''}".strip(),
        }
    return {"verdict": "pass", "detail": result.detail or "clean"}


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

    # B1: confirmation depth. A mined receipt is not finality — count how many
    # blocks bury it. Fail closed when the head is unreachable: never assume
    # depth.
    try:
        receipt_block = int(receipt.get("blockNumber", "0x0"), 16)
    except (TypeError, ValueError):
        return {"ok": False, "error": "receipt_malformed", "detail": "bad blockNumber"}
    try:
        latest_block = int(_rpc_call("eth_blockNumber", []), 16)
    except Exception as e:
        return {"ok": False, "error": "confirmations_unavailable", "detail": str(e)}
    confirmations = max(0, latest_block - receipt_block + 1)
    chain_ctx = {
        "confirmations": confirmations,
        "receipt_block": receipt_block,
        "latest_block": latest_block,
        "required_confirmations": finality_confirmations(),
    }

    token_addr = token_address(token)
    min_amount = expected_atomic * (10000 - AMOUNT_TOLERANCE_BPS) // 10000
    transfers = _parse_transfer_logs(receipt, token_addr, treasury)
    if not transfers:
        return {"ok": False, "error": "no_treasury_transfer", "token": token, **chain_ctx}

    paid = max(t["amount_atomic"] for t in transfers)
    if paid < min_amount:
        return {
            "ok": False,
            "error": "insufficient_amount",
            "paid_display": atomic_to_display(paid, token),
            "required_display": atomic_to_display(expected_atomic, token),
            "token": token,
            **chain_ctx,
        }

    payer = transfers[0]["from"]
    if payer_wallet and payer_wallet.lower() != payer:
        return {"ok": False, "error": "payer_wallet_mismatch", **chain_ctx}

    return {
        "ok": True,
        "payer_wallet": payer,
        "amount_atomic": paid,
        "amount_display": atomic_to_display(paid, token),
        "token": token,
        **chain_ctx,
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


def activate_subscription_idempotent(
    *,
    wallet: str,
    plan_id: str,
    product_name: str,
    token: str,
    tx_hash: str,
    payment_id: str,
    email: str = "",
    period_days: int = 30,
    cancel_plan_ids: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Idempotent subscription activation, journaled per payment (R3-2).

    ``activate_subscription`` has no payment_id dedupe of its own, and
    the subscription row's single ``last_payment_id`` slot cannot serve
    as one: a later legitimate purchase overwrites the slot, so a
    delayed re-drive of an earlier payment (crash post-activation,
    pre-done) no longer matches it and would extend AGAIN.

    This entry point instead records every activation it applies in
    ``platform_activation_journal`` (payment_id PRIMARY KEY — a record
    that cannot be overwritten), in the SAME transaction (BEGIN
    IMMEDIATE) as the activation itself. A re-drive whose payment_id
    is already journaled returns the journaled result without
    extending, cancelling, or otherwise touching any row.

    Optional plan replacement: when ``cancel_plan_ids`` is given, the
    wallet's ACTIVE rows in those plans are cancelled inside the same
    transaction, before the activation — so a plan change is
    cancel-then-activate atomically, and only for the named plans
    (callers scope this to their own plan family).

    Legacy fallback: an activation that predates the journal carries
    ``last_payment_id == payment_id`` on the still-active target row.
    That state is detected, journaled (freezing the protection), and
    returned without extending.

    Existing callers of ``activate_subscription`` are unaffected: this
    is a separate entry point with its own transaction.
    """
    import secrets
    from datetime import timedelta

    wallet = wallet.lower()
    now = datetime.now(timezone.utc)
    period_start = now.isoformat()
    period_end = (now + timedelta(days=period_days)).isoformat()

    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            journaled = conn.execute(
                "SELECT * FROM platform_activation_journal "
                "WHERE payment_id=?",
                (payment_id,),
            ).fetchone()
            if journaled is not None:
                conn.commit()
                return {
                    "subscription_id": journaled["subscription_id"],
                    "wallet": journaled["wallet"],
                    "plan_id": journaled["plan_id"],
                    "period_end": journaled["period_end"],
                    "status": "active",
                    "already_applied": True,
                }

            existing = conn.execute(
                "SELECT id, period_end, last_payment_id FROM "
                "platform_subscriptions WHERE wallet=? AND plan_id=? "
                "AND status='active'",
                (wallet, plan_id),
            ).fetchone()
            if (existing is not None
                    and existing["last_payment_id"] == payment_id):
                # Legacy pre-journal activation of this very payment:
                # capture it in the journal and return it unchanged.
                conn.execute(
                    "INSERT OR IGNORE INTO platform_activation_journal "
                    "(payment_id, wallet, plan_id, subscription_id, "
                    " period_end, applied_at) VALUES (?,?,?,?,?,?)",
                    (payment_id, wallet, plan_id, existing["id"],
                     existing["period_end"], now.isoformat()))
                conn.commit()
                return {
                    "subscription_id": existing["id"],
                    "wallet": wallet,
                    "plan_id": plan_id,
                    "period_end": existing["period_end"],
                    "status": "active",
                    "already_applied": True,
                }

            cancel_ids = [p for p in cancel_plan_ids if p]
            if cancel_ids:
                placeholders = ",".join("?" * len(cancel_ids))
                conn.execute(
                    "UPDATE platform_subscriptions SET status='cancelled', "
                    f"updated_at=? WHERE wallet=? AND status='active' "
                    f"AND plan_id IN ({placeholders})",
                    (now.isoformat(), wallet, *cancel_ids))

            # Re-read after any cancellation: a cancelled target row
            # must not be extended — activation starts fresh.
            existing = conn.execute(
                "SELECT id, period_end FROM platform_subscriptions "
                "WHERE wallet=? AND plan_id=? AND status='active'",
                (wallet, plan_id),
            ).fetchone()
            if existing:
                sub_id = existing["id"]
                try:
                    prev_end = datetime.fromisoformat(
                        existing["period_end"].replace("Z", "+00:00"))
                    base = prev_end if prev_end > now else now
                except ValueError:
                    base = now
                period_end = (base + timedelta(days=period_days)).isoformat()
                conn.execute(
                    """UPDATE platform_subscriptions SET period_end=?,
                       last_tx_hash=?, last_payment_id=?,
                       email=COALESCE(NULLIF(?, ''), email), updated_at=?,
                       status='active'
                       WHERE id=?""",
                    (period_end, tx_hash, payment_id, email,
                     now.isoformat(), sub_id),
                )
            else:
                sub_id = f"sub_{secrets.token_hex(8)}"
                conn.execute(
                    """INSERT INTO platform_subscriptions
                       (id, wallet, email, plan_id, product_name, token,
                        status, period_start, period_end, last_tx_hash,
                        last_payment_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
                    (
                        sub_id, wallet, email, plan_id, product_name, token,
                        period_start, period_end, tx_hash, payment_id,
                        now.isoformat(), now.isoformat(),
                    ),
                )
            conn.execute(
                "INSERT INTO platform_activation_journal "
                "(payment_id, wallet, plan_id, subscription_id, "
                " period_end, applied_at) VALUES (?,?,?,?,?,?)",
                (payment_id, wallet, plan_id, sub_id, period_end,
                 now.isoformat()))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

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


def expire_stale_checkouts(now: datetime | None = None) -> int:
    """Mark pending/verifying/awaiting-finality checkouts past expires_at as expired."""
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE platform_checkouts
               SET status='expired'
               WHERE status IN ('pending', 'verifying', 'awaiting_finality')
                 AND expires_at IS NOT NULL
                 AND expires_at < ?""",
            (stamp,),
        )
        conn.commit()
        return cur.rowcount


def _load_checkout(payment_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM platform_checkouts WHERE payment_id=?", (payment_id,)
        ).fetchone()
    return dict(row) if row else None


def _set_checkout_status(
    payment_id: str,
    status: str,
    *,
    tx_hash: str | None = None,
    payer_wallet: str | None = None,
    only_from: tuple[str, ...] = (),
) -> int:
    """Set a checkout's status; returns the number of rows updated."""
    sets = ["status=?"]
    params: list[Any] = [status]
    if tx_hash is not None:
        sets.append("tx_hash=?")
        params.append(tx_hash)
    if payer_wallet is not None:
        sets.append("payer_wallet=?")
        params.append(payer_wallet)
    params.append(payment_id)
    where = "WHERE payment_id=?"
    if only_from:
        where += " AND status IN (%s)" % ",".join("?" * len(only_from))
        params.extend(only_from)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE platform_checkouts SET {', '.join(sets)} {where}", params
        )
        conn.commit()
        return cur.rowcount


def _order_id_for_payment(payment_id: str) -> str:
    """Deterministic order id, stable across finality re-polls."""
    return f"TOKEN-ORD-{payment_id}"


def _checkout_result_payload(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Full verify-result payload built from a checkout row."""
    meta = {}
    try:
        meta = json.loads(row.get("metadata") or "{}")
    except (ValueError, TypeError):
        pass
    payload = {
        "payment_id": row["payment_id"],
        "order_id": _order_id_for_payment(row["payment_id"]),
        "tx_hash": row.get("tx_hash"),
        "payer_wallet": row.get("payer_wallet"),
        "product_name": row["product_name"],
        "plan_id": row["plan_id"],
        "token": row["token"],
        "amount_atomic": row["amount_atomic"],
        "usd_reference": row["usd_reference"],
        "order_type": meta.get("order_type", "generic"),
        "billing": meta.get("billing", "once"),
        "customer_email": row.get("customer_email") or "",
    }
    payload.update(extra)
    return payload


def _maybe_finalize(
    payment_id: str, row: dict[str, Any], vr: dict[str, Any], tx_hash: str
) -> dict[str, Any]:
    """Receipt is valid: enforce the confirmation-depth gate, then OFAC-screen.

    Below depth -> stays 'awaiting_finality' (never provisioned). At/above
    depth -> sanctions screen, then 'finalized' (safe to provision once).
    """
    conf = int(vr.get("confirmations") or 0)
    depth = int(vr.get("required_confirmations") or finality_confirmations())
    base = _checkout_result_payload(
        row,
        tx_hash=tx_hash,
        payer_wallet=vr.get("payer_wallet"),
        amount_display=vr.get("amount_display"),
        confirmations=conf,
        required_confirmations=depth,
    )
    # W-113 (defense in depth): never record a registry claim without a
    # payer. Callers reach here either through the proof-gated
    # verify_checkout (payer proven by EIP-191 signature) or through
    # _advance_finality, which verifies against the checkout's bound
    # wallet; an empty payer here means neither happened.
    if not str(vr.get("payer_wallet") or "").strip():
        _set_checkout_status(
            payment_id, "pending",
            only_from=("verifying", "awaiting_finality"),
        )
        _audit_payment_event(
            payment_id, "payer_proof_refused", tx_hash,
            "registry claim refused: no proven payer")
        return {"ok": False, "error": "payer_wallet_required", **base}
    # W-113 (defense in depth): when the checkout carries a bound payer,
    # the verified payer must BE that payer. A verification result naming
    # anyone else (e.g. a merely chain-recovered payer for a row bound to
    # a different wallet) never finalizes and never claims.
    bound_payer = str(row.get("payer_wallet") or "").strip().lower()
    if bound_payer and \
            str(vr.get("payer_wallet") or "").strip().lower() != bound_payer:
        _audit_payment_event(
            payment_id, "payer_proof_refused", tx_hash,
            "registry claim refused: verified payer contradicts the "
            "checkout's bound payer")
        return {"ok": False, "error": "payer_wallet_mismatch", **base}
    # D2: claim the tx in the shared consumed_onchain_txs registry BEFORE
    # any finality/fulfillment transition, so the same on-chain transfer
    # cannot also fulfill an x402 challenge or a rail settle under a
    # different entitlement. The claim is taken even while below depth
    # (awaiting_finality): the tx is reserved to THIS checkout until it
    # finalizes or is reverted (revert releases the claim, see
    # _advance_finality). Same-payment re-polls re-claim idempotently.
    claim = claim_consumed_onchain_tx(
        tx_hash, claim_system="platform", claim_ref=payment_id,
        payer_wallet=str(vr.get("payer_wallet") or ""))
    if not claim.get("ok"):
        _set_checkout_status(
            payment_id, "pending",
            only_from=("verifying", "awaiting_finality"),
        )
        _audit_payment_event(
            payment_id, "consumed_tx_conflict", tx_hash,
            f"held by {claim.get('consumed_by_system')}:"
            f"{claim.get('consumed_by_ref')}")
        return {"ok": False, "error": "tx_already_used",
                "consumed_by_system": claim.get("consumed_by_system"),
                "consumed_by_ref": claim.get("consumed_by_ref"), **base}
    if conf < depth:
        _set_checkout_status(
            payment_id,
            "awaiting_finality",
            tx_hash=tx_hash,
            payer_wallet=vr.get("payer_wallet"),
            only_from=("verifying", "awaiting_finality", "finalized"),
        )
        return {"ok": True, "status": "awaiting_finality", **base}

    # Depth reached — B2: screen the recovered payer against OFAC BEFORE
    # anything is finalized. A sanctions HIT always blocks; an unavailable
    # service blocks in strict mode (default) and is audit-flagged otherwise.
    payer = vr.get("payer_wallet") or ""
    screen = screen_payer_sanctions(payer)
    verdict = screen["verdict"]
    if verdict == "hit":
        _set_checkout_status(
            payment_id, "blocked", tx_hash=tx_hash, payer_wallet=payer,
            only_from=("verifying", "awaiting_finality"),
        )
        _audit_payment_event(payment_id, "ofac_hit", tx_hash, screen["detail"])
        return {
            "ok": False, "error": "sanctions_hit", "status": "blocked",
            "ofac_verdict": verdict, "ofac_detail": screen["detail"], **base,
        }
    if verdict == "unavailable" and ofac_strict_mode():
        _set_checkout_status(
            payment_id, "blocked", tx_hash=tx_hash, payer_wallet=payer,
            only_from=("verifying", "awaiting_finality"),
        )
        _audit_payment_event(
            payment_id, "ofac_unavailable_blocked", tx_hash, screen["detail"]
        )
        return {
            "ok": False, "error": "ofac_screening_unavailable",
            "status": "blocked", "ofac_verdict": verdict,
            "ofac_detail": screen["detail"], **base,
        }
    if verdict == "unavailable":
        _audit_payment_event(
            payment_id, "ofac_unavailable_failopen", tx_hash,
            screen["detail"] + "; PLATFORM_OFAC_STRICT=false",
        )

    try:
        _set_checkout_status(
            payment_id, "finalized", tx_hash=tx_hash, payer_wallet=payer,
            only_from=("verifying", "awaiting_finality"),
        )
    except sqlite3.IntegrityError:
        # Another checkout already finalized/fulfilled this tx_hash (partial
        # unique index) — same tx can't pay twice. Send this checkout back to
        # 'pending' so the payer can submit a different tx.
        _set_checkout_status(
            payment_id, "pending", only_from=("verifying", "awaiting_finality")
        )
        return {"ok": False, "error": "tx_already_used", **base}
    _audit_payment_event(
        payment_id, "finalized", tx_hash,
        f"confirmations={conf} required={depth} ofac={verdict}",
    )

    # Platform-fee accounting at finality (best-effort; never blocks).
    try:
        from sincor2.treasury_settlement import record_platform_fee_inflow

        usd = float(row["usd_reference"] or 0)
        fee = usd * 0.05 if usd > 0 else 0.0
        if fee > 0:
            record_platform_fee_inflow(
                fee_amount=fee,
                asset=row["token"],
                source="human_checkout",
                tx_hash=tx_hash,
                task_id=payment_id,
                note=f"checkout {row['plan_id']} {row['product_name']}",
            )
    except Exception:
        pass

    return {
        "ok": True, "status": "finalized", "ofac_verdict": verdict, **base,
    }


def _advance_finality(payment_id: str) -> dict[str, Any]:
    """Re-check chain state for a checkout in 'awaiting_finality'/'finalized'.

    - receipt gone from chain        -> 'reverted' + audit (reorg/drop)
    - receipt failed                 -> 'reverted' + audit
    - depth not yet reached          -> stays 'awaiting_finality'
    - depth reached                  -> '_maybe_finalize' (OFAC + finalize)
    Transient RPC trouble never reverts: the checkout stays put and the
    error surfaces to the caller.
    """
    row = _load_checkout(payment_id)
    if not row or row["status"] not in ("awaiting_finality", "finalized"):
        return {"ok": False, "error": "not_awaiting_finality",
                "payment_id": payment_id}
    tx_hash = row["tx_hash"] or ""
    if not tx_hash:
        return {"ok": False, "error": "tx_hash_missing", "payment_id": payment_id}
    if not str(row["payer_wallet"] or "").strip():
        # W-113: a legacy row can sit in 'awaiting_finality' with NO
        # bound payer (first verify predates the payer-proof gate).
        # Re-verifying it would hand back the chain-recovered payer and
        # finalize on a payer nobody ever proved — the squat through
        # the recheck door. Refuse and leave the row exactly as found;
        # it can only advance once a payer is bound by a proven verify.
        _audit_payment_event(
            payment_id, "payer_proof_refused", tx_hash,
            "finality recheck refused: checkout has no bound payer")
        return {"ok": False, "error": "payer_wallet_required",
                "status": row["status"], "payment_id": payment_id,
                "tx_hash": tx_hash}
    vr = verify_treasury_transfer(
        tx_hash,
        token=row["token"],
        expected_atomic=int(row["amount_atomic"]),
        payer_wallet=row["payer_wallet"] or "",
    )
    if not vr.get("ok"):
        if vr.get("error") == "tx_pending":
            # We previously saw a valid receipt for this tx. It is gone now:
            # dropped from the mempool or reorged out. Pre-fulfillment, so the
            # order must NOT stay fulfilled — move to 'reverted' with an audit
            # record, never silently.
            _set_checkout_status(
                payment_id, "reverted",
                only_from=("awaiting_finality", "finalized"),
            )
            # D2: no entitlement was granted — release the registry
            # claim so the reverted claim does not linger.
            release_consumed_onchain_tx(tx_hash, claim_ref=payment_id)
            _audit_payment_event(
                payment_id, "reorg_tx_missing", tx_hash,
                "receipt no longer present on chain (reorg or drop)",
            )
            return {"ok": False, "error": "reorg_detected", "status": "reverted",
                    "payment_id": payment_id, "tx_hash": tx_hash}
        if vr.get("error") == "tx_failed":
            _set_checkout_status(
                payment_id, "reverted",
                only_from=("awaiting_finality", "finalized"),
            )
            release_consumed_onchain_tx(tx_hash, claim_ref=payment_id)
            _audit_payment_event(
                payment_id, "tx_failed_on_recheck", tx_hash,
                "receipt now reports failure",
            )
            return {"ok": False, "error": "tx_failed", "status": "reverted",
                    "payment_id": payment_id, "tx_hash": tx_hash}
        # Transient (RPC/head/amount issues): stay put, surface the error.
        base = _checkout_result_payload(row, tx_hash=tx_hash)
        return {**vr, "status": row["status"], **base}

    if row["status"] == "finalized":
        # Already finalized; receipt still good — no re-screen, just confirm.
        base = _checkout_result_payload(
            row, tx_hash=tx_hash, payer_wallet=vr.get("payer_wallet"),
            confirmations=vr.get("confirmations"),
            required_confirmations=vr.get("required_confirmations"),
        )
        return {"ok": True, "status": "finalized", **base}
    return _maybe_finalize(payment_id, row, vr, tx_hash)


def recheck_finality(payment_id: str) -> dict[str, Any]:
    """Reorg/finality sweeper for one checkout. Safe to call repeatedly.

    Covers pre-fulfillment states (advance or revert) and post-fulfillment
    states (deep-reorg watch -> 'disputed'). Returns a status dict; never
    raises for chain issues.
    """
    row = _load_checkout(payment_id)
    if not row:
        return {"ok": False, "error": "checkout_not_found"}
    status = row["status"]
    if status in ("awaiting_finality", "finalized"):
        return _advance_finality(payment_id)
    if status in ("fulfilled", "completed"):
        tx_hash = row["tx_hash"] or ""
        if not tx_hash:
            return {"ok": True, "status": "fulfilled", "reorg": False,
                    "payment_id": payment_id}
        try:
            receipt = _rpc_call("eth_getTransactionReceipt", [tx_hash])
        except Exception as e:
            return {"ok": False, "error": "rpc_failed", "detail": str(e),
                    "status": status, "payment_id": payment_id}
        if not receipt or receipt.get("status") != "0x1":
            # Deep reorg AFTER fulfillment: entitlements were already granted.
            # The order must never silently stay fulfilled -> 'disputed' with
            # an audit record so a human remediates (e.g. clawback/revoke).
            _set_checkout_status(
                payment_id, "disputed", only_from=("fulfilled", "completed")
            )
            _audit_payment_event(
                payment_id, "deep_reorg_after_fulfillment", tx_hash,
                "tx missing/failed on chain after fulfillment; "
                "manual remediation required",
            )
            return {"ok": True, "status": "disputed", "reorg": True,
                    "payment_id": payment_id, "tx_hash": tx_hash}
        return {"ok": True, "status": "fulfilled", "reorg": False,
                "payment_id": payment_id, "tx_hash": tx_hash}
    return {"ok": False, "error": "not_finality_pending", "status": status,
            "payment_id": payment_id}


def mark_checkout_fulfilled(payment_id: str) -> bool:
    """Record provisioning: 'finalized' -> 'fulfilled'. Returns True on success."""
    return _set_checkout_status(payment_id, "fulfilled",
                                only_from=("finalized",)) == 1


# --- W-113: platform checkout payer binding (EIP-191 ownership proof) ---
# Before W-113, verify_checkout accepted a bare (payment_id, tx_hash):
# verify_treasury_transfer rejected only a SUPPLIED payer_wallet that
# mismatched the chain payer, so a caller supplying NO wallet finalized a
# checkout against ANY observed treasury transfer and claimed the tx in
# the shared consumed_onchain_txs registry under the chain-recovered
# payer — recorded as if proven. Anyone watching the treasury could
# squat a victim's payment: the attacker's checkout finalized and
# fulfilled on the victim's transfer, and the victim's own rail/x402
# settle then failed tx_hash_already_used (hunt-31 PoC,
# poc31_d2_platform_squat.py; reproduced 2026-10-28).
#
# The rail/x402 legs already require an EIP-191-bound challenge
# (hunt-29 B1: bind_challenge_payer + require_bound_challenge). The
# platform leg now requires the same class of proof at verify:
#   * the checkout's payer is its BOUND wallet — the wallet stored at
#     creation, or, for checkouts created wallet-less, the wallet proven
#     at first verify (the binding is then written to the row);
#   * verify must present payer_wallet == the bound payer AND an EIP-191
#     personal_sign signature from that wallet over
#     platform_bind_message(payment_id, payer_wallet);
#   * the proven wallet is passed into verify_treasury_transfer, so the
#     on-chain payer must equal it.
# A registry claim is therefore only ever recorded for a payer the
# caller proved it controls — never one merely recovered from chain.

#: EIP-191 personal_sign domain for platform-checkout payer binding.
PLATFORM_BIND_DOMAIN = "SINCOR-PLATFORM-CHECKOUT-BIND"


def platform_bind_message(payment_id: str, payer_wallet: str) -> str:
    """Canonical message the payer signs to prove wallet ownership for a
    platform checkout verify. Public so clients/SDKs can build
    byte-identical messages; the server always rebuilds it from its own
    payment_id and the claimed wallet, so a signature obtained for one
    checkout or wallet cannot be replayed for another."""
    return "|".join(
        [
            PLATFORM_BIND_DOMAIN,
            str(payment_id),
            str(payer_wallet).strip().lower(),
        ]
    )


def _recover_platform_bind_signer(message: str, signature: str) -> str:
    """Recover the signer of a platform-bind signature.

    Mirrors x402_payments._recover_bind_signer: require_low_s
    canonicality first (high-s twins are rejected even though they
    recover to the same address — the codebase standard), then
    eth_account EIP-191 recovery. Raises ValueError on any failure;
    callers fail closed.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from sincor2.sig_canonical import require_low_s
    except Exception as exc:
        raise ValueError("signature verification unavailable") from exc
    try:
        raw = require_low_s(signature)
        return str(
            Account.recover_message(encode_defunct(text=message), signature=raw)
        )
    except Exception as exc:
        raise ValueError("bad signature") from exc


# Accepted risk (Wren PR 11, 2026-10-30): the bind signature covers
# (payment_id, wallet) only — deliberately NOT tx_hash. That is safe
# because the signature never authorizes a specific payment: the
# proven wallet is passed into verify_treasury_transfer, which
# requires the presented tx's on-chain payer to BE that wallet, so a
# valid signature cannot spend another wallet's transfer (pinned by
# tests/pytest/test_w113_tx_payer_binding.py). Binding tx_hash was
# rejected on UX grounds: the tx does not exist until after the buyer
# pays, so it would force a second signature round-trip at checkout.


def _payer_proof_gate(
    payment_id: str,
    row: dict[str, Any],
    payer_wallet: str,
    payer_signature: str,
) -> tuple[str, dict[str, Any] | None]:
    """W-113 payer-proof gate for verify_checkout.

    Returns (proven_wallet, None) when the caller proved control of the
    checkout's payer wallet, else ("", error_dict). Enforces, in order:
      1. an effective payer exists — the checkout's bound wallet, or the
         supplied wallet for a wallet-less checkout (bound at verify);
      2. a supplied wallet contradicting the bound wallet is refused;
      3. an EIP-191 signature from the effective wallet over
         platform_bind_message(payment_id, wallet) recovers to it.
    """
    bound = str(row.get("payer_wallet") or "").strip().lower()
    supplied = str(payer_wallet or "").strip().lower()
    if bound and supplied and supplied != bound:
        return "", {"ok": False, "error": "payer_wallet_mismatch",
                    "payment_id": payment_id}
    wallet = bound or supplied
    if not wallet:
        return "", {"ok": False, "error": "payer_wallet_required",
                    "payment_id": payment_id}
    if not payer_signature:
        return "", {"ok": False, "error": "ownership_signature_required",
                    "payment_id": payment_id}
    message = platform_bind_message(payment_id, wallet)
    try:
        recovered = _recover_platform_bind_signer(message, payer_signature)
    except ValueError:
        return "", {"ok": False, "error": "ownership_proof_failed",
                    "payment_id": payment_id}
    if recovered.lower() != wallet:
        return "", {"ok": False, "error": "ownership_proof_failed",
                    "payment_id": payment_id}
    return wallet, None


def verify_checkout(
    payment_id: str,
    tx_hash: str,
    *,
    customer_email: str = "",
    payer_wallet: str = "",
    payer_signature: str = "",
) -> dict[str, Any]:
    """Verify an onchain treasury payment for a checkout.

    B1: a valid receipt alone never provisions. The checkout moves to
    'awaiting_finality' until `finality_confirmations()` blocks bury the tx,
    then (after the B2 OFAC screen) to 'finalized'. Only 'finalized' may be
    provisioned, via `mark_checkout_fulfilled`. Re-polling an
    'awaiting_finality' checkout re-checks the chain: a vanished tx means a
    reorg/drop and the order goes to 'reverted' with an audit record.

    W-113: the pending->verify transition additionally requires payer
    proof (see _payer_proof_gate): the caller must prove control of the
    checkout's bound payer wallet with an EIP-191 signature, and the
    on-chain payer must equal that proven wallet. A bare
    (payment_id, tx_hash) pair proves nothing and is refused before any
    chain call or registry claim.
    """
    if not payment_id or not tx_hash:
        return {"ok": False, "error": "payment_id_and_tx_hash_required"}
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return {"ok": False, "error": "invalid_tx_hash"}

    expire_stale_checkouts()
    now = datetime.now(timezone.utc)

    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM platform_checkouts WHERE payment_id=?", (payment_id,)
        ).fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "checkout_not_found"}
        status = row["status"]
        if status in ("fulfilled", "completed"):
            # Legacy 'completed' rows are 'fulfilled' by another name.
            if status == "completed":
                conn.execute(
                    "UPDATE platform_checkouts SET status='fulfilled' "
                    "WHERE payment_id=?",
                    (payment_id,),
                )
                conn.commit()
            else:
                conn.rollback()
            base = _checkout_result_payload(dict(row))
            return {"ok": True, "status": "already_fulfilled", **base}
        if status in ("awaiting_finality", "finalized"):
            if (row["tx_hash"] or "") != tx_hash:
                conn.rollback()
                return {
                    "ok": False, "error": "tx_hash_mismatch",
                    "detail": "checkout is already bound to a different tx_hash",
                    "payment_id": payment_id,
                }
            conn.rollback()
            return _advance_finality(payment_id)
        if status in ("blocked", "reverted", "disputed"):
            conn.rollback()
            return {"ok": False, "error": f"checkout_{status}",
                    "status": status, "payment_id": payment_id}
        if status == "expired":
            conn.rollback()
            return {"ok": False, "error": "checkout_expired"}
        if status == "verifying":
            conn.rollback()
            return {"ok": False, "error": "verify_in_progress"}

        expires = row["expires_at"]
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < now:
            conn.execute(
                "UPDATE platform_checkouts SET status='expired' WHERE payment_id=?",
                (payment_id,),
            )
            conn.commit()
            return {"ok": False, "error": "checkout_expired"}

        dup = conn.execute(
            "SELECT payment_id FROM platform_checkouts "
            "WHERE tx_hash=? AND status IN ('finalized', 'fulfilled', 'completed')",
            (tx_hash,),
        ).fetchone()
        if dup:
            conn.rollback()
            return {"ok": False, "error": "tx_already_used"}

        claimed = conn.execute(
            "UPDATE platform_checkouts SET status='verifying' "
            "WHERE payment_id=? AND status='pending'",
            (payment_id,),
        )
        if claimed.rowcount != 1:
            conn.rollback()
            return {"ok": False, "error": "verify_in_progress"}
        conn.commit()
        row = dict(row)
    finally:
        conn.close()

    # W-113: payer proof BEFORE any chain call or registry claim. Only
    # the pending->verify transition passes through here; re-polls of an
    # 'awaiting_finality'/'finalized' checkout returned above via
    # _advance_finality, which re-verifies against the wallet bound (and
    # proven) by this first verify.
    proven_wallet, proof_error = _payer_proof_gate(
        payment_id, row, payer_wallet, payer_signature)
    if proof_error is not None:
        with _conn() as conn:
            conn.execute(
                "UPDATE platform_checkouts SET status='pending' "
                "WHERE payment_id=? AND status='verifying'",
                (payment_id,),
            )
            conn.commit()
        _audit_payment_event(
            payment_id, "payer_proof_refused", tx_hash,
            str(proof_error.get("error") or ""))
        return proof_error

    token = row["token"]
    expected = int(row["amount_atomic"])
    vr = verify_treasury_transfer(
        tx_hash, token=token, expected_atomic=expected,
        payer_wallet=proven_wallet
    )
    if not vr.get("ok"):
        with _conn() as conn:
            conn.execute(
                "UPDATE platform_checkouts SET status='pending' "
                "WHERE payment_id=? AND status='verifying'",
                (payment_id,),
            )
            conn.commit()
        base = _checkout_result_payload(row, tx_hash=tx_hash)
        return {**vr, **base}

    if not str(row.get("payer_wallet") or "").strip():
        # W-113: the first proven verify binds a wallet-less checkout to
        # the proven payer, so every later finality advance re-verifies
        # against this wallet (never a merely chain-recovered one).
        with _conn() as conn:
            conn.execute(
                "UPDATE platform_checkouts SET payer_wallet=? "
                "WHERE payment_id=? "
                "AND (payer_wallet IS NULL OR payer_wallet='')",
                (proven_wallet, payment_id),
            )
            conn.commit()
        row["payer_wallet"] = proven_wallet

    if customer_email:
        with _conn() as conn:
            conn.execute(
                "UPDATE platform_checkouts SET customer_email=? "
                "WHERE payment_id=? AND (customer_email IS NULL OR customer_email='')",
                (customer_email, payment_id),
            )
            conn.commit()

    return _maybe_finalize(payment_id, row, vr, tx_hash)
