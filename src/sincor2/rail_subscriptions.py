"""x402/USDC rail subscriptions — periodic re-auth, no pull payments.

DESIGN
------
x402 V1 has no pull-payment / subscription primitive: the server can never
charge a subscriber. A subscription is therefore a chain of explicit
per-period re-authorizations — each billing period the subscriber settles
one fresh x402 challenge for the plan's server-derived price (USDC on
Base into the treasury). The server sends renewal reminders carrying a
pay link; dunning never charges. This is option (a) from the design pass.

  Rejected (b) x402 V2 session tokens: the repo's x402 implementation is
  homegrown V1 — ``MANDATES_API_WATCH`` in x402_payments.py is an explicit
  design note with zero code. Assuming V2 would invent protocol surface.
  Rejected (c) Coinbase CDP Mandates / spend permissions: requires live
  CDP keys plus network calls to Coinbase; not hermetically testable and
  violates the no-invented-endpoints TDD constraint. Revisit only when CDP
  is a connected, keyed dependency — until then the fail-closed posture
  (no mandate => no pull) is exactly what periodic re-auth implements.

State machine (derived on read, persisted lazily — no cron needed):
  active      now <= period_end. Full access.
  past_due    period_end < now <= period_end + 3d. Dunning: reminders go
              out, access continues, nothing is charged automatically.
  grace       period_end + 3d < now <= period_end + 7d. Last-chance window,
              access continues.
  canceled    past grace, or explicit cancel_subscription(). Access
              revoked: effective_plan() -> "none", enforced through
              plan_gate.allow()'s existing rank checks via gate().

Wallet <-> plan binding: issue_renewal_challenge() records the challenge
against the wallet; settle_renewal() fails closed unless the settling
wallet matches the issued wallet AND the on-chain transfer's payer
matches (x402's own payer check). One subscription row per wallet
(wallet TEXT PRIMARY KEY — the UNIQUE wallet constraint ported from the
parked branch's customer-id semantics).

Prices: SUB_PLANS derives price_cents from plan_gate.plan_price_cents()
(the parked branch's server-side canon table) — the client never supplies
a price. /rail/checkout cross-checks the rail-sub-<plan> yaml resource
and fails closed on drift, mirroring the pack rail.

Idempotency: settle claims (settle_id = "railsub-<challenge_id>") are
INSERT OR IGNORE. Double-settling one challenge returns a duplicate ack;
the period is extended exactly once. A retry of a claimed-but-incomplete
settle reuses the verified claim (tx_hash/wallet pinned) instead of
re-verifying.

Atomic extension (W-105): the period application and the settle row's
extended flag commit in ONE transaction on the shared ledger DB (BEGIN
IMMEDIATE), and the same transaction stamps the applied period_end /
period_index / action onto the settle row. There is no window in which
the extension is committed but the flag is not, so neither a crash nor
a concurrent settle can apply a second period for one payment; a retry
that finds the applied stamp repairs the flag and acks already_renewed.
The payment finalize is likewise claimed under the same write lock
(finalized flag flipped in the transaction that holds the lock across
finalize_challenge_payment), so concurrent settles cannot append
duplicate platform-payment / treasury-inflow records for one tx.

Proration (pinned in tests; period math mirrors
platform_payments.activate_subscription exactly so the rail record and
the platform record can never diverge):
  - same-plan renewal: period extends from max(period_end, now) — renewing
    early never loses time; after lapse there is no backfill.
  - plan change (upgrade OR downgrade, whenever permitted): a fresh
    30-day period starts at payment time — no credit for unused time in
    either direction. The platform record is cancel-then-reactivated so
    both stores agree.
  - downgrade: permitted only inside the 7-day renewal window before
    period_end, or after expiry. A mid-period downgrade payment is
    refused with downgrade_at_renewal (money already moved onchain is
    recorded; re-settling the same challenge once the window opens
    completes it — no second payment).

Money gate: the treasury kill switch gates the period extension
(fail closed). A denial records a subscription_renewal_gate_denied alert;
the payment stays finalized (money moved onchain) and the retry path
completes the extension once the halt clears.

STRIPE BOUNDARY: this module never touches stripe_routes.py or
stripe_checkout.py.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from sincor2 import plan_gate as pg

logger = logging.getLogger("sincor.rail_subscriptions")

# --- Plans (server-side; prices from plan_gate's canon table) ----------------
PERIOD_DAYS = 30
PAST_DUE_DAYS = 3
GRACE_DAYS = 7
RENEWAL_WINDOW_DAYS = 7

SUB_PLANS: Dict[str, Dict[str, Any]] = {
    plan_id: {
        "name": pg.PLAN_DISPLAY_NAMES[plan_id],
        "price_cents": pg.plan_price_cents(plan_id),
        "period_days": PERIOD_DAYS,
    }
    for plan_id in ("starter", "professional", "enterprise")
}

# x402 resource naming: each plan is registered as rail-sub-<plan_id> in
# config/x402_pricing.yaml with amount_usdc == price_cents / 100.
RAIL_SUB_RESOURCE_PREFIX = "rail-sub-"

# Settle statuses that mean "already done" — a re-settle is acknowledged
# without re-extending the period.
_FULFILLED_STATUSES = ("renewed", "upgraded", "downgraded")

_WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat((ts or "").replace("Z", "+00:00"))


# --- Durable subscription state ----------------------------------------------
_SUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS rail_subscriptions (
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
CREATE TABLE IF NOT EXISTS rail_sub_challenges (
    challenge_id TEXT PRIMARY KEY,
    wallet       TEXT NOT NULL,
    plan_id      TEXT NOT NULL,
    issued_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rail_sub_settles (
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

# Columns added after the original schema shipped (W-105 atomic
# extension). _connect() backfills them on pre-existing ledger files.
_SETTLE_EXTRA_COLUMNS = {
    "applied_period_end": "TEXT NOT NULL DEFAULT ''",
    "applied_period_index": "INTEGER NOT NULL DEFAULT 0",
    "applied_action": "TEXT NOT NULL DEFAULT ''",
    "platform_synced": "INTEGER NOT NULL DEFAULT 0",
}


def _db_path() -> Path:
    """Ledger file holding rail subscription state.

    Shares the revenue ledger file (SINCOR_REVENUE_DB) with the pack rail
    so operators keep one place to look.
    """
    return Path(os.environ.get("SINCOR_REVENUE_DB")
                or "data/revenue/revenue_ledger.db")


def _connect() -> sqlite3.Connection:
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.executescript(_SUB_SCHEMA)
    conn.commit()
    existing = {r[1] for r in conn.execute(
        "PRAGMA table_info(rail_sub_settles)")}
    for col, decl in _SETTLE_EXTRA_COLUMNS.items():
        if col not in existing:
            conn.execute(
                f"ALTER TABLE rail_sub_settles ADD COLUMN {col} {decl}")
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _resource_id_for(plan_id: str) -> str:
    return f"{RAIL_SUB_RESOURCE_PREFIX}{plan_id}"


def _plan_for_resource(resource_id: str) -> Optional[str]:
    if not resource_id.startswith(RAIL_SUB_RESOURCE_PREFIX):
        return None
    plan_id = resource_id[len(RAIL_SUB_RESOURCE_PREFIX):]
    return plan_id if plan_id in SUB_PLANS else None


def _get_row(wallet: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM rail_subscriptions WHERE wallet=?",
            (wallet.lower(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _update_row(wallet: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _utc_now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE rail_subscriptions SET {sets} WHERE wallet=?",
            (*fields.values(), wallet.lower()))
        conn.commit()
    finally:
        conn.close()


# --- Status derivation --------------------------------------------------------
def _derive_status(row: Dict[str, Any], now: datetime) -> str:
    """Derive the current status from the period clock (no cron needed)."""
    if row.get("status") == "canceled":
        return "canceled"  # explicit cancel sticks until a new payment
    try:
        period_end = _parse_iso(row["period_end"])
    except ValueError:
        return "canceled"  # corrupt clock — fail closed
    if now <= period_end:
        return "active"
    if now <= period_end + timedelta(days=PAST_DUE_DAYS):
        return "past_due"
    try:
        grace_until = _parse_iso(row["grace_until"])
    except ValueError:
        grace_until = period_end + timedelta(days=GRACE_DAYS)
    if now <= grace_until:
        return "grace"
    return "canceled"


def _access_statuses() -> Tuple[str, ...]:
    return ("active", "past_due", "grace")


def subscription_status(wallet: str) -> Dict[str, Any]:
    """Current subscription view for a wallet; persists lazy transitions."""
    w = (wallet or "").strip().lower()
    row = _get_row(w)
    now = _utc_now()
    if not row:
        return {
            "wallet": w, "plan_id": None, "status": "none",
            "effective_plan": "none", "period_index": 0,
            "period_start": None, "period_end": None, "grace_until": None,
            "reminder_count": 0,
        }
    derived = _derive_status(row, now)
    if derived != row["status"]:
        _update_row(w, status=derived)
        row = _get_row(w) or row
    return {
        "wallet": w,
        "plan_id": row["plan_id"],
        "status": derived,
        "effective_plan": row["plan_id"]
        if derived in _access_statuses() else "none",
        "period_index": row["period_index"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "grace_until": row["grace_until"],
        "reminder_count": row["reminder_count"],
    }


def effective_plan(wallet: str) -> str:
    """Plan string to feed plan_gate rank checks. "none" when lapsed."""
    return subscription_status(wallet)["effective_plan"]


def gate(wallet: str, feature: str):
    """plan_gate.allow() against the wallet's effective plan.

    The hook into plan_gate.py's existing rank checks: a lapsed or
    never-subscribed wallet resolves to "none" (rank 0, fail closed).
    """
    return pg.allow(effective_plan(wallet), feature)


# --- Challenge issuance -------------------------------------------------------
def issue_renewal_challenge(wallet: str, plan_id: str) -> Dict[str, Any]:
    """Mint an x402 challenge for one billing period of ``plan_id``.

    Raises ValueError on unknown plan / bad wallet (fail closed, no
    challenge minted); RuntimeError when the server-side resource or
    price config is missing or drifted (never mis-charge).
    """
    from sincor2 import x402_payments as x402

    plan = pg.normalize_plan(plan_id)
    if plan not in SUB_PLANS:
        raise ValueError(f"unknown_plan: {plan_id}")
    w = (wallet or "").strip().lower()
    if not _WALLET_RE.match(w):
        raise ValueError(f"invalid_wallet: {wallet}")

    # Price cross-check: the x402 resource must agree with SUB_PLANS.
    resource_id = _resource_id_for(plan)
    resource = x402.get_resource(resource_id)
    if not resource:
        logger.error("[RAIL-SUB] x402 resource missing for plan %s", plan)
        raise RuntimeError("subscription_resource_not_configured")
    expected_usdc = SUB_PLANS[plan]["price_cents"] / 100
    if abs(float(resource["amount_usdc"]) - expected_usdc) > 0.001:
        logger.critical(
            "[RAIL-SUB] price config drift for %s: SUB_PLANS=%s vs yaml=%s",
            plan, expected_usdc, resource["amount_usdc"])
        raise RuntimeError("subscription_price_mismatch")

    challenge = x402.create_challenge(resource_id)
    challenge_id = challenge.get("challenge_id")
    if not challenge_id:
        raise RuntimeError("challenge_mint_failed")

    # Wallet <-> challenge binding: only the issued wallet may settle it.
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO rail_sub_challenges "
            "(challenge_id, wallet, plan_id, issued_at) VALUES (?,?,?,?)",
            (challenge_id, w, plan, _utc_now_iso()))
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": challenge.get("ok", False),
        "plan_id": plan,
        "plan_name": SUB_PLANS[plan]["name"],
        "amount_usdc": expected_usdc,
        "challenge_id": challenge_id,
        "resource_id": resource_id,
        "expires_at": challenge.get("expires_at"),
        "payment": challenge.get("payment"),
        "bind": challenge.get("bind"),
        "message": challenge.get("message"),
    }


def _get_issued(challenge_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM rail_sub_challenges WHERE challenge_id=?",
            (challenge_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- Settle claim (idempotency) -------------------------------------------------
def _get_sub_settle(settle_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM rail_sub_settles WHERE settle_id=?",
            (settle_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _claim_sub_settle(*, settle_id: str, challenge_id: str, wallet: str,
                      plan_id: str, tx_hash: str,
                      amount_usdc: float) -> Tuple[Dict[str, Any], bool]:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO rail_sub_settles "
            "(settle_id, challenge_id, wallet, plan_id, tx_hash, "
            " amount_usdc, created_at) VALUES (?,?,?,?,?,?,?)",
            (settle_id, challenge_id, wallet, plan_id, tx_hash,
             amount_usdc, _utc_now_iso()))
        is_new = cur.rowcount == 1
        conn.commit()
        row = conn.execute(
            "SELECT * FROM rail_sub_settles WHERE settle_id=?",
            (settle_id,)).fetchone()
        return dict(row), is_new
    finally:
        conn.close()


def _update_sub_settle(settle_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE rail_sub_settles SET {sets} WHERE settle_id=?",
            (*fields.values(), settle_id))
        conn.commit()
    finally:
        conn.close()


def _claim_sub_settle_flag(settle_id: str, column: str) -> bool:
    """Atomically flip a settle-row flag 0 -> 1; True for the one winner.

    ``column`` is whitelisted — it names a flag, never caller data.
    """
    if column not in ("finalized", "platform_synced"):
        raise ValueError(f"not a claimable flag: {column}")
    conn = _connect()
    try:
        cur = conn.execute(
            f"UPDATE rail_sub_settles SET {column}=1 "
            f"WHERE settle_id=? AND {column}=0",
            (settle_id,))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _finalize_sub_settle_once(settle_id: str, build_result) -> bool:
    """Finalize the payment for a settle exactly once (W-105 corollary).

    The write lock (BEGIN IMMEDIATE) is held across the finalized-flag
    re-check, finalize_challenge_payment(), and the flag write, so two
    concurrent settles of one challenge cannot both run the finalize:
    finalize appends to the platform-payment JSONL and the treasury
    inflow ledger (both append-only, no dedupe), which would otherwise
    double-record revenue for one tx. Returns True when THIS caller
    performed the finalize. Neither append touches this DB, so holding
    its write lock across them cannot deadlock.
    """
    from sincor2 import x402_payments as x402

    conn = _connect()
    conn.isolation_level = None  # explicit transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM rail_sub_settles WHERE settle_id=?",
                (settle_id,)).fetchone()
            if row is None or row["finalized"]:
                conn.rollback()
                return False
            result = build_result(dict(row))
            x402.finalize_challenge_payment(result)
            conn.execute(
                "UPDATE rail_sub_settles SET finalized=1, tx_hash=? "
                "WHERE settle_id=?",
                (str(result.get("tx_hash") or row["tx_hash"]), settle_id))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


# --- Money gate -----------------------------------------------------------------
def _sub_money_gate_allows(*, settle_id: str, challenge_id: str,
                           plan_id: str, amount_usdc: float,
                           tx_hash: str, wallet: str) -> bool:
    """Treasury kill switch gates the period extension (fail closed)."""
    try:
        from sincor2.governance.money_gate import (
            check_money_effect,
            treasury_halt_tripped,
        )
    except ImportError as exc:
        logger.critical(
            "[RAIL-SUB] money gate unavailable (%s) — settle %s "
            "DENIED (fail closed)", exc, settle_id)
        return False
    allowed, gate_reason = check_money_effect(
        effect_type="payment.transfer",
        payload={"event": "rail.subscription.settle",
                 "amount_usdc": amount_usdc,
                 "challenge_id": challenge_id,
                 "tx_hash": tx_hash,
                 "plan_id": plan_id,
                 "wallet": wallet},
        agent_id="rail_subscription_renewal",
        risk_tier="high",
        idempotency_key=f"rail-sub-{settle_id}",
        target="rail_subscription_provisioning",
        estimated_cost=float(amount_usdc or 0.0),
        kill_switch_tripped=treasury_halt_tripped(),
    )
    if not allowed:
        logger.critical(
            "[RAIL-SUB] money gate denied settle %s: %s", settle_id,
            gate_reason)
    return allowed


def _record_gate_denial(settle_id: str, plan_id: str) -> None:
    try:
        from sincor2.payment_ledger import record_alert
        record_alert(
            _db_path(),
            settle_id,
            "subscription_renewal_gate_denied",
            "Subscription renewal gate-denied for settle %s (%s)"
            % (settle_id, plan_id),
            details={"plan_id": plan_id, "rail": True,
                     "subscription": True},
        )
    except Exception as exc:
        logger.error("[RAIL-SUB] could not record gate-denial alert: %s",
                     exc)


# --- Period application ---------------------------------------------------------
def _apply_period(*, settle_id: str, wallet: str, plan_id: str,
                  tx_hash: str, challenge_id: str) -> Dict[str, Any]:
    """Apply one paid period per the proration rules — atomically (W-105).

    The subscription upsert and the settle row's extended flag (plus the
    applied-period stamp: period_end / period_index / action) commit in
    ONE BEGIN IMMEDIATE transaction on the shared ledger DB. A crash can
    therefore never leave the extension committed with the flag unwritten
    (the deterministic double-extend), and a concurrent settle blocks on
    the write lock, then sees the applied stamp and acks instead of
    re-applying. The conditional flag UPDATE (``extended=0 AND
    applied_period_end=''``) is a second, independent guard: exactly one
    caller can ever win it for a given settle row.

    Returns {"ok": True, "action": ..., "plan_changed": ...}, or
    {"ok": True, "already": True, ...} when this settle's period was
    already applied, or
    {"ok": False, "error": "downgrade_at_renewal", ...} (nothing applied
    and nothing stamped — a later re-settle can still complete it).
    """
    now = _utc_now()
    new_rank = pg.rank(plan_id)
    period_delta = timedelta(days=PERIOD_DAYS)

    conn = _connect()
    conn.isolation_level = None  # explicit transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            settle_row = conn.execute(
                "SELECT * FROM rail_sub_settles WHERE settle_id=?",
                (settle_id,)).fetchone()
            if settle_row is not None and (
                    settle_row["extended"]
                    or settle_row["applied_period_end"]):
                # This settle already applied its period in an earlier
                # atomic commit. Repair the flag/stamp pair if one half
                # was cleared (the crash-window simulation), then ack —
                # NEVER re-apply.
                sub_row = conn.execute(
                    "SELECT * FROM rail_subscriptions WHERE wallet=?",
                    (wallet,)).fetchone()
                applied_end = settle_row["applied_period_end"] or (
                    sub_row["period_end"] if sub_row else "")
                applied_index = settle_row["applied_period_index"] or (
                    int(sub_row["period_index"]) if sub_row else 0)
                conn.execute(
                    "UPDATE rail_sub_settles SET extended=1, "
                    "applied_period_end=?, applied_period_index=? "
                    "WHERE settle_id=?",
                    (applied_end, applied_index, settle_id))
                conn.commit()
                return {"ok": True, "already": True,
                        "action": "already_renewed",
                        "period_end": applied_end,
                        "period_index": applied_index}

            row_raw = conn.execute(
                "SELECT * FROM rail_subscriptions WHERE wallet=?",
                (wallet,)).fetchone()
            row = dict(row_raw) if row_raw is not None else None

            if row is None:
                period_start, period_end = now, now + period_delta
                period_index = 1
                action = "renewed"
                plan_changed = False
            else:
                current = _derive_status(row, now)
                old_rank = pg.rank(row["plan_id"])
                try:
                    old_end = _parse_iso(row["period_end"])
                except ValueError:
                    old_end = now
                if new_rank > old_rank:
                    # upgrade: fresh period from payment, no credit for
                    # unused time
                    period_start, period_end = now, now + period_delta
                    action = "upgraded"
                elif new_rank < old_rank:
                    in_window = (
                        current != "active" or
                        now >= old_end - timedelta(
                            days=RENEWAL_WINDOW_DAYS))
                    if not in_window:
                        conn.rollback()
                        return {
                            "ok": False,
                            "error": "downgrade_at_renewal",
                            "current_plan": row["plan_id"],
                            "period_end": row["period_end"],
                            "detail": (
                                "downgrades take effect at renewal: "
                                "re-settle this challenge within 7 days of "
                                "period_end or after expiry; no second "
                                "payment is needed"),
                        }
                    # downgrade at renewal: fresh period from payment
                    # (same rule as upgrades — no proration credit in
                    # either direction)
                    period_start, period_end = now, now + period_delta
                    action = "downgraded"
                else:
                    # same-plan renewal: extend from max(period_end, now)
                    # — the exact rule
                    # platform_payments.activate_subscription uses, so
                    # the two stores can never diverge.
                    base = old_end if old_end > now else now
                    period_start = _parse_iso(row["period_start"]) \
                        if current == "active" else now
                    period_end = base + period_delta
                    action = "renewed"
                period_index = int(row["period_index"]) + 1
                plan_changed = row["plan_id"] != plan_id

            grace_until = (period_end
                           + timedelta(days=GRACE_DAYS)).isoformat()
            stamp = _utc_now_iso()
            conn.execute(
                "INSERT INTO rail_subscriptions "
                "(wallet, plan_id, status, period_index, period_start, "
                " period_end, grace_until, last_tx_hash, "
                " last_challenge_id, reminder_count, last_reminder_at, "
                " created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(wallet) DO UPDATE SET "
                "plan_id=excluded.plan_id, status='active', "
                "period_index=excluded.period_index, "
                "period_start=excluded.period_start, "
                "period_end=excluded.period_end, "
                "grace_until=excluded.grace_until, "
                "last_tx_hash=excluded.last_tx_hash, "
                "last_challenge_id=excluded.last_challenge_id, "
                "reminder_count=0, last_reminder_at='', "
                "updated_at=excluded.updated_at",
                (wallet, plan_id, "active", period_index,
                 period_start.isoformat(), period_end.isoformat(),
                 grace_until, tx_hash, challenge_id, 0, "", stamp, stamp))
            cur = conn.execute(
                "UPDATE rail_sub_settles SET extended=1, "
                "applied_period_end=?, applied_period_index=?, "
                "applied_action=?, note='' "
                "WHERE settle_id=? AND extended=0 "
                "AND applied_period_end=''",
                (period_end.isoformat(), period_index, action, settle_id))
            if cur.rowcount != 1:
                # Lost the claim race inside the transaction — another
                # settle applied this period. Roll EVERYTHING back.
                conn.rollback()
                return {"ok": True, "already": True,
                        "action": "already_renewed"}
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    logger.info("[RAIL-SUB] %s: wallet=%s plan=%s period_end=%s",
                action, wallet, plan_id, period_end.isoformat())
    return {"ok": True, "action": action, "plan_changed": plan_changed,
            "period_end": period_end.isoformat(),
            "period_index": period_index}


def _sync_platform_once(*, settle_id: str, wallet: str, plan_id: str,
                        tx_hash: str, challenge_id: str,
                        plan_changed: bool) -> bool:
    """Mirror the applied period into platform_payments exactly once.

    The platform store lives in a different DB, so it cannot join the
    atomic extension transaction; instead the platform_synced flag is
    claimed atomically and only the claim winner syncs. A failed sync
    releases the claim so a later re-settle can reconcile (see
    _reconcile_platform_sync). Returns True when this caller synced.
    """
    if not _claim_sub_settle_flag(settle_id, "platform_synced"):
        return False
    try:
        _sync_platform(wallet=wallet, plan_id=plan_id, tx_hash=tx_hash,
                       challenge_id=challenge_id, plan_changed=plan_changed)
    except Exception:
        _update_sub_settle(settle_id, platform_synced=0)
        raise
    return True


def _reconcile_platform_sync(settle: Dict[str, Any]) -> None:
    """Best-effort completion of a platform sync lost to a crash.

    If the rail period was applied (extended / applied stamp present)
    but the platform mirror never ran — the crash window between the
    atomic extension commit and the sync — a duplicate ack completes it
    here, exactly once via the same claim. Never raises: the ack itself
    must not fail because the mirror store hiccuped.
    """
    try:
        if not settle or settle.get("platform_synced"):
            return
        if not (settle.get("extended") or settle.get("applied_period_end")):
            return
        _sync_platform_once(
            settle_id=settle["settle_id"],
            wallet=settle.get("wallet") or "",
            plan_id=settle.get("plan_id") or "",
            tx_hash=settle.get("tx_hash") or "",
            challenge_id=settle.get("challenge_id") or "",
            plan_changed=settle.get("applied_action")
            in ("upgraded", "downgraded"))
    except Exception as exc:
        logger.error("[RAIL-SUB] platform sync reconcile failed for %s: %s",
                     settle.get("settle_id") if settle else "?", exc)


def _sync_platform(*, wallet: str, plan_id: str, tx_hash: str,
                   challenge_id: str, plan_changed: bool) -> None:
    """Mirror the period into platform_payments' subscription record.

    Same-plan renewals stack via activate_subscription's own
    max(period_end, now) rule (identical to _apply_period's); plan
    changes cancel-then-reactivate so both stores agree on a fresh
    period starting at payment time.
    """
    import sincor2.platform_payments as pp

    if plan_changed:
        pp.cancel_wallet_subscriptions(wallet)
    pp.activate_subscription(
        wallet=wallet,
        plan_id=plan_id,
        product_name=SUB_PLANS[plan_id]["name"],
        token="USDC",
        tx_hash=tx_hash,
        payment_id=challenge_id,
        email="",
        period_days=PERIOD_DAYS,
    )


# --- Settle -----------------------------------------------------------------------
def _verify_fail(result: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "error": result.get("error", "verify_failed"),
        "detail": result.get("detail", ""),
    }
    if result.get("error") == "replay_auth_failed":
        out["replay_hint"] = (
            "challenge already fulfilled: re-settle with replay_signature + "
            "replay_ts_ms — an EIP-191 personal_sign of "
            "build_replay_message(challenge_id, ts_ms) by the payer wallet")
    return out


def _already_renewed(settle: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "status": "already_renewed",
        "duplicate": True,
        "plan_id": settle.get("plan_id"),
        "challenge_id": settle.get("challenge_id"),
        "settle_id": settle.get("settle_id"),
        "subscription": subscription_status(settle.get("wallet") or ""),
    }


def settle_renewal(challenge_id: str, tx_hash: str, wallet: str, *,
                   replay_signature: str = "",
                   replay_ts_ms: Any = None,
                   _preverified: Optional[Dict[str, Any]] = None
                   ) -> Dict[str, Any]:
    """Verify one period's on-chain payment and extend the subscription.

    claim (idempotent) -> verify onchain USDC transfer (fail-closed) ->
    finalize payment once -> money gate (fail-closed) -> extend exactly
    one period per the proration rules. A re-settle of an already-extended
    challenge is acknowledged without touching the chain.
    """
    from sincor2 import x402_payments as x402

    cid = (challenge_id or "").strip()
    tx = (tx_hash or "").strip()
    w = (wallet or "").strip().lower()
    if not cid or not _TX_HASH_RE.match(tx):
        return {"ok": False, "error": "challenge_id_and_tx_hash_required"}
    if not _WALLET_RE.match(w):
        return {"ok": False, "error": "invalid_wallet"}

    # Wallet <-> challenge binding: only the issued wallet may settle.
    issued = _get_issued(cid)
    if not issued:
        return {"ok": False, "error": "challenge_not_issued"}
    if issued["wallet"] != w:
        return {"ok": False, "error": "wallet_mismatch",
                "detail": "challenge was issued for a different wallet"}
    plan_id = issued["plan_id"]

    settle_id = f"railsub-{cid}"
    settle = _get_sub_settle(settle_id)
    verify_result: Optional[Dict[str, Any]] = None

    if settle is not None:
        if settle.get("extended"):
            logger.info("[RAIL-SUB] duplicate settle for %s — acknowledged",
                        settle_id)
            _reconcile_platform_sync(settle)
            return _already_renewed(settle)
        # Retry of a claimed-but-incomplete settle: pin tx_hash/wallet.
        if (settle.get("tx_hash") or "").lower() != tx.lower():
            return {"ok": False, "error": "tx_hash_mismatch",
                    "settle_id": settle_id}
        if (settle.get("wallet") or "") != w:
            return {"ok": False, "error": "wallet_mismatch",
                    "settle_id": settle_id}
        plan_id = settle.get("plan_id") or plan_id
        if not settle.get("verified"):
            verify = x402.verify_challenge(
                cid, tx, payer_wallet=w,
                replay_signature=replay_signature,
                replay_ts_ms=replay_ts_ms)
            if not verify.get("ok"):
                return _verify_fail(verify)
            _update_sub_settle(settle_id, verified=1)
            verify_result = verify
    else:
        verify = (_preverified if _preverified is not None
                  else x402.verify_challenge(
                      cid, tx, payer_wallet=w,
                      replay_signature=replay_signature,
                      replay_ts_ms=replay_ts_ms))
        if not verify.get("ok"):
            return _verify_fail(verify)
        # Defense in depth: the verified resource must be this plan's.
        resource_id = str(verify.get("resource_id") or "")
        if resource_id != _resource_id_for(plan_id):
            return {"ok": False, "error": "not_a_subscription_challenge",
                    "resource_id": resource_id, "settle_id": settle_id}
        settle, is_new = _claim_sub_settle(
            settle_id=settle_id, challenge_id=cid, wallet=w,
            plan_id=plan_id, tx_hash=tx,
            amount_usdc=SUB_PLANS[plan_id]["price_cents"] / 100)
        _update_sub_settle(settle_id, verified=1)
        verify_result = verify
        if not is_new:
            settle = _get_sub_settle(settle_id) or settle
            if settle.get("extended"):
                _reconcile_platform_sync(settle)
                return _already_renewed(settle)

    amount_usdc = SUB_PLANS[plan_id]["price_cents"] / 100

    try:
        # Record the platform payment + treasury inflow exactly once:
        # the finalized flag is claimed under the ledger write lock,
        # held across finalize_challenge_payment, so concurrent settles
        # cannot both append payment/inflow records for one tx.
        # NOTE: no automatic 5% fee split is booked (known gap; founder
        # decision owed) — payments are recorded faithfully.
        settle = _get_sub_settle(settle_id) or settle
        if not settle.get("finalized"):
            def _build_finalize_result(row, _verify=verify_result):
                if _verify and _verify.get("status") == "fulfilled":
                    return _verify
                resource = x402.get_resource(_resource_id_for(plan_id))
                if not resource:
                    raise RuntimeError(
                        "subscription resource missing for finalize")
                return {
                    "ok": True,
                    "status": "fulfilled",
                    "challenge_id": cid,
                    "resource_id": _resource_id_for(plan_id),
                    "tx_hash": row.get("tx_hash") or tx,
                    "payer_wallet": row.get("wallet") or w,
                    "amount_atomic": resource["amount_atomic"],
                    "amount_display": resource["amount_display"],
                }

            if _finalize_sub_settle_once(settle_id, _build_finalize_result):
                logger.info("[RAIL-SUB] finalized payment for settle %s",
                            settle_id)

        # Treasury kill-switch money gate (fail closed). A denial records
        # an alert and stops the extension; the claim stays non-extended
        # so an operator (or the halt clearing) can retry.
        if not _sub_money_gate_allows(
                settle_id=settle_id, challenge_id=cid, plan_id=plan_id,
                amount_usdc=amount_usdc, tx_hash=tx, wallet=w):
            _record_gate_denial(settle_id, plan_id)
            _update_sub_settle(settle_id, note="gate_denied")
            return {"ok": False, "error": "money_gate_denied",
                    "settle_id": settle_id}

        # Extend exactly one period per the proration rules. The
        # extension and the extended flag commit atomically inside
        # _apply_period (W-105); there is deliberately no separate
        # flag write after it returns.
        applied = _apply_period(settle_id=settle_id, wallet=w,
                                plan_id=plan_id, tx_hash=tx,
                                challenge_id=cid)
        if not applied.get("ok"):
            # downgrade_at_renewal: payment recorded, nothing applied —
            # the same challenge can complete it later (no second payment).
            _update_sub_settle(settle_id, note=str(applied.get("error")))
            return {"ok": False, "error": applied.get("error"),
                    "detail": applied.get("detail", ""),
                    "settle_id": settle_id,
                    "subscription": subscription_status(w)}
        if applied.get("already"):
            # A concurrent settle (or an earlier crash-interrupted one)
            # already applied this settle's one period.
            settle = _get_sub_settle(settle_id) or settle
            _reconcile_platform_sync(settle)
            return _already_renewed(settle)
        # Mirror into platform_payments exactly once (separate DB —
        # claimed via the platform_synced flag, not the extension txn).
        _sync_platform_once(
            settle_id=settle_id, wallet=w, plan_id=plan_id, tx_hash=tx,
            challenge_id=cid,
            plan_changed=bool(applied.get("plan_changed")))
        return {"ok": True, "status": applied["action"], "plan_id": plan_id,
                "settle_id": settle_id, "challenge_id": cid,
                "subscription": subscription_status(w)}
    except Exception as exc:
        logger.exception("[RAIL-SUB] settle pipeline failed for %s",
                         settle_id)
        _update_sub_settle(settle_id, note="settle_pipeline_failed")
        return {"ok": False, "error": "settle_pipeline_failed",
                "detail": str(exc), "settle_id": settle_id}


# --- Cancellation -------------------------------------------------------------------
def cancel_subscription(wallet: str) -> Dict[str, Any]:
    """Cancel immediately: access revoked, platform record cancelled."""
    import sincor2.platform_payments as pp

    w = (wallet or "").strip().lower()
    if _get_row(w):
        _update_row(w, status="canceled")
        try:
            pp.cancel_wallet_subscriptions(w)
        except Exception as exc:
            logger.error("[RAIL-SUB] platform cancel failed for %s: %s",
                         w, exc)
    return subscription_status(w)


# --- Renewal reminders (dunning without charging) ---------------------------------------
def record_renewal_reminder(wallet: str) -> Optional[Dict[str, Any]]:
    """Record that a renewal reminder was sent; return the pay payload.

    A reminder NEVER charges — it carries a pay link (POST /rail/checkout
    with the server-derived price) for the subscriber to complete.
    Returns None when the wallet has no subscription row.
    """
    w = (wallet or "").strip().lower()
    row = _get_row(w)
    if not row:
        return None
    status = subscription_status(w)
    plan_id = row["plan_id"]
    reminder_no = int(row["reminder_count"]) + 1
    _update_row(w, reminder_count=reminder_no,
                last_reminder_at=_utc_now_iso())
    return {
        "wallet": w,
        "plan_id": plan_id,
        "plan_name": SUB_PLANS.get(plan_id, {}).get("name", plan_id),
        "amount_usdc": (SUB_PLANS.get(plan_id, {}).get("price_cents", 0)
                        / 100),
        "period_end": row["period_end"],
        "status": status["status"],
        "reminder_no": reminder_no,
        "pay": {
            "method": "POST",
            "route": "/rail/checkout",
            "body": {"subscription_plan": plan_id, "payer_wallet": w},
        },
    }


def subscriptions_needing_reminder(now: Optional[datetime] = None
                                   ) -> list:
    """Wallets due a renewal reminder (for a cron to call).

    Due = status active/past_due/grace, period_end within the renewal
    window, and no reminder in the last 24h.
    """
    now = now or _utc_now()
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM rail_subscriptions").fetchall()
    finally:
        conn.close()
    due = []
    for r in rows:
        row = dict(r)
        if _derive_status(row, now) not in _access_statuses():
            continue
        try:
            period_end = _parse_iso(row["period_end"])
        except ValueError:
            continue
        if period_end - now > timedelta(days=RENEWAL_WINDOW_DAYS):
            continue
        last = (row.get("last_reminder_at") or "").strip()
        if last:
            try:
                if now - _parse_iso(last) < timedelta(hours=24):
                    continue
            except ValueError:
                pass
        due.append(row["wallet"])
    return due
