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
duplicate platform-payment / treasury-inflow records for one tx; and
the two external stores dedupe on the tx/payment key, so a crash
after the appends but before the flag commit cannot duplicate them
on retry either (R1). The platform mirror carries separate claim /
done flags plus an OS sync lock, so a crash mid-sync is repairable
by the next duplicate ack instead of silently stuck (R2), and a sync
failure never flips an already-committed settle to failure (R5).
Pre-W-105 ledger rows are repaired from the subscription row's
last_challenge_id evidence (R3) and seeded as already-synced at
migration when extended=1 (R4).

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

import contextlib
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
    canceled_at       TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rail_sub_challenges (
    challenge_id TEXT PRIMARY KEY,
    wallet       TEXT NOT NULL,
    plan_id      TEXT NOT NULL,
    issued_at    TEXT NOT NULL,
    voided_at    TEXT NOT NULL DEFAULT ''
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
    platform_sync_done   INTEGER NOT NULL DEFAULT 0,
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
    "platform_sync_done": "INTEGER NOT NULL DEFAULT 0",
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
    # Migrations for ledgers created before the W-106 columns existed.
    sub_cols = {r[1] for r in
                conn.execute("PRAGMA table_info(rail_subscriptions)")}
    if "canceled_at" not in sub_cols:
        conn.execute(
            "ALTER TABLE rail_subscriptions "
            "ADD COLUMN canceled_at TEXT NOT NULL DEFAULT ''")
    ch_cols = {r[1] for r in
               conn.execute("PRAGMA table_info(rail_sub_challenges)")}
    if "voided_at" not in ch_cols:
        conn.execute(
            "ALTER TABLE rail_sub_challenges "
            "ADD COLUMN voided_at TEXT NOT NULL DEFAULT ''")
    conn.commit()
    existing = {r[1] for r in conn.execute(
        "PRAGMA table_info(rail_sub_settles)")}
    newly_added = set()
    for col, decl in _SETTLE_EXTRA_COLUMNS.items():
        if col not in existing:
            conn.execute(
                f"ALTER TABLE rail_sub_settles ADD COLUMN {col} {decl}")
            newly_added.add(col)
    conn.commit()
    if newly_added:
        _migrate_legacy_settles(conn, newly_added)
    _repair_legacy_applied_stamps(conn)
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_legacy_settles(conn: sqlite3.Connection,
                            newly_added: set) -> None:
    """Seed sync state for settle rows written before the done marker.

    Runs ONLY at the migration moment (a column was just added), never
    on steady-state connects — a live row with extended=1 and a sync
    still pending must not be marked done.

    R4: code predating the platform_synced / platform_sync_done
    columns synced the platform record inside _apply_period, so every
    pre-existing row with extended=1 WAS already synced. Seed both
    flags so the first post-upgrade duplicate ack does not re-run the
    sync and double-extend the platform mirror.

    NEW-5: seed ONLY for that genuinely pre-W-105 ledger — one where
    platform_synced was ALSO just added. A c2de0901-era ledger already
    carries platform_synced (and the applied stamps) but no
    platform_sync_done column; there, an extended=1 row with
    platform_synced=0 is a legitimately pending sync that reconcile
    must still drive, not an already-synced row.
    """
    if "platform_sync_done" not in newly_added:
        return
    if "platform_synced" not in newly_added:
        return
    conn.execute(
        "UPDATE rail_sub_settles SET platform_synced=1, "
        "platform_sync_done=1 WHERE extended=1")
    conn.commit()


def _repair_legacy_applied_stamps(conn: sqlite3.Connection) -> None:
    """Seed the applied-period stamp for pre-W-105 crash victims (R3).

    Old code committed the period application and only afterwards
    wrote extended=1 in a separate transaction; a crash in between
    left a settle indistinguishable from never-applied — except for
    the evidence the old _apply_period DID commit: the subscription
    row's last_challenge_id / last_tx_hash point at exactly the
    challenge whose period was applied. A verified+finalized settle
    whose challenge_id matches its subscription row's
    last_challenge_id therefore already applied its period: stamp it
    from the subscription row so _apply_period acks already_renewed
    and never re-applies. The challenge-match makes this safe to run
    on every connect: an in-flight new-code settle cannot match (its
    challenge is not the subscription's last applied challenge until
    the atomic apply itself stamps it).
    """
    candidate = conn.execute(
        "SELECT s.settle_id FROM rail_sub_settles s "
        "JOIN rail_subscriptions sub "
        "  ON sub.wallet = s.wallet "
        " AND sub.last_challenge_id = s.challenge_id "
        "WHERE s.extended=0 AND s.verified=1 AND s.finalized=1 "
        "  AND s.applied_period_end='' AND s.challenge_id<>'' "
        "LIMIT 1").fetchone()
    if candidate is None:
        return
    conn.execute(
        "UPDATE rail_sub_settles SET "
        "applied_period_end = (SELECT period_end FROM "
        "  rail_subscriptions WHERE wallet = rail_sub_settles.wallet), "
        "applied_period_index = (SELECT period_index FROM "
        "  rail_subscriptions WHERE wallet = rail_sub_settles.wallet), "
        "applied_action = CASE WHEN applied_action='' THEN 'renewed' "
        "  ELSE applied_action END "
        "WHERE extended=0 AND verified=1 AND finalized=1 "
        "  AND applied_period_end='' AND challenge_id<>'' "
        "  AND EXISTS (SELECT 1 FROM rail_subscriptions sub "
        "    WHERE sub.wallet = rail_sub_settles.wallet "
        "      AND sub.last_challenge_id = rail_sub_settles.challenge_id)")
    conn.commit()


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
        updates: Dict[str, Any] = {"status": derived}
        if derived == "canceled":
            # W-106: stamp when the cancel took effect so settle can
            # distinguish pre-cancel challenges from fresh purchases.
            updates["canceled_at"] = _utc_now_iso()
        _update_row(w, **updates)
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

    # Hunt-29: bind the x402 challenge row to the issued wallet at
    # issuance, so subscription settle can require a bound challenge
    # (require_bound_challenge=True) consistently with the pack rail.
    # Server-issued binding is sound here — and only here — because the
    # wallet is required at issuance, recorded in rail_sub_challenges
    # below, and settle extends ONLY that wallet's subscription: no
    # settler can redirect the benefit to themselves. (Pack challenges
    # are never bound this way; their fulfillment goes to the settler,
    # so they require the EIP-191 ownership bind instead.)
    bound = x402.bind_server_issued_challenge(challenge_id, w)
    if not bound.get("ok"):
        logger.error("[RAIL-SUB] challenge bind failed for plan %s: %s",
                     plan, bound.get("error"))
        raise RuntimeError("challenge_bind_failed")

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


def _claim_consumed_tx_for_settle(*, tx_hash: str, challenge_id: str,
                                  wallet: str = "") -> Dict[str, Any]:
    """D2 item 1: claim the settle tx in the shared consumed_onchain_txs
    registry (platform_payments, orders DB).

    rail_sub_settles itself carries tx_hash as plain TEXT with no
    uniqueness, so the registry is this ledger's cross-system guard.
    The claim_ref is the challenge_id: a subscription settle rides on
    exactly one x402 challenge, and verify_challenge claims the same tx
    under that same challenge_id — the two claims are ONE entitlement,
    so the registry treats them as idempotent. A tx held under any
    other claim_ref (a platform payment_id or a different challenge)
    is refused here and the period MUST NOT be extended.
    """
    from sincor2 import platform_payments as pp

    return pp.claim_consumed_onchain_tx(
        tx_hash, claim_system="rail_sub", claim_ref=challenge_id,
        payer_wallet=wallet)


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
    concurrent settles of one challenge cannot both run the finalize.
    Crash safety for the appends themselves lives in the stores (R1):
    record_platform_payment dedupes on (tx_hash, payment_id) and
    record_inflow on (tx_hash, source, note, amount), so if this
    process dies after the appends but before the flag commit, the
    retry's finalize re-call appends nothing twice — each external
    record happens at most once per tx. Returns True when THIS caller
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


# --- W-106 void/cancel check (fresh read, shared by settle + apply) ------------
def _is_canceled_status(stored: Any) -> bool:
    """Normalized canceled comparison: case/whitespace-insensitive."""
    return (stored or "").strip().lower() == "canceled"


def _challenge_voided_by_cancel(challenge_id: str, wallet: str, *,
                                already_extended: bool = False) -> bool:
    """Fresh-read W-106 check: is this challenge dead because of a cancel?

    Re-reads the challenge row and the subscription row at call time —
    never values loaded earlier in a settle. True when the challenge
    was voided by cancel_subscription(), or when the subscription is
    stored canceled and this challenge predates that cancel (a
    challenge issued AFTER the cancel is a genuinely fresh purchase
    and is not voided by it). The already-extended carve-out is
    ack-only: a settle that already applied its period keeps its
    duplicate ack and applies nothing new, so it is never refused.
    """
    if already_extended:
        return False
    issued = _get_issued(challenge_id)
    if issued is None:
        return False
    if (issued.get("voided_at") or "").strip():
        logger.warning(
            "[RAIL-SUB] settle refused: challenge %s voided by cancel",
            challenge_id)
        return True
    sub_row = _get_row(wallet)
    if sub_row and _is_canceled_status(sub_row.get("status")):
        canceled_raw = (sub_row.get("canceled_at")
                        or sub_row.get("updated_at") or "")
        try:
            fresh_purchase = (
                _parse_iso(issued.get("issued_at") or "")
                > _parse_iso(canceled_raw))
        except ValueError:
            fresh_purchase = False  # unparseable clock — fail closed
        if not fresh_purchase:
            logger.warning(
                "[RAIL-SUB] settle refused: challenge %s predates the "
                "cancel of wallet %s", challenge_id, wallet)
            return True
    return False


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
    # W-106 defense-in-depth: last-moment fresh re-validation inside the
    # apply step itself, so a cancel that landed after settle_renewal's
    # pre-apply re-check (below) still cannot be overwritten to active.
    # Nothing external (no RPC, no gate, no finalize) runs between this
    # read and the upsert below — only local period math on SQLite rows.
    if _challenge_voided_by_cancel(challenge_id, wallet):
        return {
            "ok": False,
            "error": "challenge_voided_by_cancel",
            "detail": ("challenge voided by a cancel that landed before "
                       "the period was applied"),
        }
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

            # W-106 atomic final check: BEGIN IMMEDIATE holds the DB
            # write lock, so a concurrent cancel either committed fully
            # before this transaction (its void/cancel is seen on THIS
            # connection here — abort) or waits for the upsert to
            # commit and then re-cancels the row (final state canceled
            # either way). Re-reads use THIS connection only — opening
            # another _connect() while holding the write lock would
            # self-block on its schema script. This runs after the
            # already-applied ack above: a settle that already applied
            # its period applies nothing new, so a later cancel never
            # refuses its ack (the W-106 already-extended carve-out).
            ch_row = conn.execute(
                "SELECT voided_at, issued_at FROM rail_sub_challenges "
                "WHERE challenge_id=?", (challenge_id,)).fetchone()
            atomic_voided = bool(
                ch_row is not None and (ch_row["voided_at"] or "").strip())
            if not atomic_voided and ch_row is not None:
                sub_now = conn.execute(
                    "SELECT status, canceled_at, updated_at FROM "
                    "rail_subscriptions WHERE wallet=?",
                    (wallet.lower(),)).fetchone()
                if sub_now is not None and _is_canceled_status(
                        sub_now["status"]):
                    canceled_raw = (sub_now["canceled_at"]
                                    or sub_now["updated_at"] or "")
                    try:
                        atomic_fresh = (
                            _parse_iso(ch_row["issued_at"] or "")
                            > _parse_iso(canceled_raw))
                    except ValueError:
                        atomic_fresh = False  # unparseable — fail closed
                    atomic_voided = not atomic_fresh
            if atomic_voided:
                conn.rollback()
                return {
                    "ok": False,
                    "error": "challenge_voided_by_cancel",
                    "detail": ("challenge voided by a cancel that landed "
                               "before the period was applied"),
                }
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
                " canceled_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(wallet) DO UPDATE SET "
                "plan_id=excluded.plan_id, status='active', "
                "period_index=excluded.period_index, "
                "period_start=excluded.period_start, "
                "period_end=excluded.period_end, "
                "grace_until=excluded.grace_until, "
                "last_tx_hash=excluded.last_tx_hash, "
                "last_challenge_id=excluded.last_challenge_id, "
                "reminder_count=0, last_reminder_at='', canceled_at='', "
                "updated_at=excluded.updated_at",
                (wallet, plan_id, "active", period_index,
                 period_start.isoformat(), period_end.isoformat(),
                 grace_until, tx_hash, challenge_id, 0, "", "", stamp, stamp))
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


@contextlib.contextmanager
def _platform_sync_lock():
    """OS file lock serializing platform syncs for this ledger.

    Held across the claim-check + sync + done-write in
    _sync_platform_once. The OS releases it automatically when the
    holder process dies, which is what makes a crashed sync claim
    distinguishable from a live one: a claimed-but-not-done row whose
    lock can be acquired has no live syncer and is safe to re-drive.
    Degrades to a no-op where flock is unavailable (the DB claim flag
    still guards the common path).
    """
    lock_path = str(_db_path()) + ".platform-sync.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield
        return
    locked = False
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        except (ImportError, OSError):
            locked = False
        yield
    finally:
        if locked:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        os.close(fd)


def _sync_platform_once(*, settle_id: str, wallet: str, plan_id: str,
                        tx_hash: str, challenge_id: str,
                        plan_changed: bool) -> bool:
    """Mirror the applied period into platform_payments exactly once.

    The platform store lives in a different DB, so it cannot join the
    atomic extension transaction. Claim/done are separate flags
    (R2): platform_synced is the claim, platform_sync_done is written
    only AFTER a successful sync. The sync lock is acquired BEFORE
    the claim is inspected, so a claimed-but-not-done row observed
    under the lock belongs to a caller that died mid-sync (a live
    syncer would hold the lock) and is re-driven here — never
    silently stuck. A failed sync releases the claim (done stays 0)
    and re-raises so a later re-settle can reconcile. Returns True
    when this caller synced.
    """
    with _platform_sync_lock():
        settle = _get_sub_settle(settle_id)
        if settle is None or settle.get("platform_sync_done"):
            return False
        if not settle.get("platform_synced"):
            if not _claim_sub_settle_flag(settle_id, "platform_synced"):
                settle = _get_sub_settle(settle_id) or {}
                if settle.get("platform_sync_done"):
                    return False
                if not settle.get("platform_synced"):
                    return False
                # Lost the claim race to a caller that has already
                # died (we hold the sync lock): fall through and
                # re-drive the claimed-but-not-done sync.
        try:
            _sync_platform(wallet=wallet, plan_id=plan_id,
                           tx_hash=tx_hash, challenge_id=challenge_id,
                           plan_changed=plan_changed)
        except Exception:
            _update_sub_settle(settle_id, platform_synced=0)
            raise
        _update_sub_settle(settle_id, platform_sync_done=1)
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
        if not settle or settle.get("platform_sync_done"):
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


def _rail_row_canceled(wallet: str) -> bool:
    """Fresh read: is the rail subscription row stored canceled now?"""
    row = _get_row(wallet)
    return bool(row and _is_canceled_status(row.get("status")))


def _sync_platform(*, wallet: str, plan_id: str, tx_hash: str,
                   challenge_id: str, plan_changed: bool) -> None:
    """Mirror the period into platform_payments' subscription record.

    Same-plan renewals stack via activate_subscription's own
    max(period_end, now) rule (identical to _apply_period's); plan
    changes cancel-then-reactivate so both stores agree on a fresh
    period starting at payment time.

    The sync itself is idempotent per challenge (NEW-1/NEW-2): the
    platform store records which payment last activated a subscription
    (last_payment_id), and activate_subscription has no payment_id
    dedupe of its own — a second activation for the same challenge
    would stack another period. The claim/done flags alone cannot
    distinguish "claimed, sync never ran" from "claimed, sync already
    committed" (a crash — or a failed done-write — after the platform
    commit lands in exactly that state), so before activating we skip
    when this challenge already activated the target plan's row. The
    same check closes the legacy hole: a pre-W-105 victim whose
    old-code sync already ran carries last_payment_id == challenge on
    the platform row, so the repairing ack leaves the mirror alone.

    That single-slot check is only a legacy fallback, not the primary
    dedupe (R3-2): last_payment_id is overwritten by the NEXT
    legitimate purchase, so an interleaved late ack for an earlier
    payment no longer matches it. The primary dedupe is the
    per-payment activation journal inside
    platform_payments.activate_subscription_idempotent — written in
    the same transaction as the activation, keyed by payment_id, and
    never overwritten — which this sync now routes through.

    Plan changes are likewise derived from the platform store, not
    just the caller's stamp (NEW-3): a legacy applied stamp says
    'renewed' unconditionally, but if the platform store's active plan
    differs from the settle's plan, the mirror needs cancel-then-
    activate semantics or both plans end up active at once. The
    derivation is scoped to the RAIL plan family (R3-1): the platform
    store is shared and multi-plan by design (e.g. an `intel` row
    bought via the human checkout), so only active rows whose plan_id
    is in SUB_PLANS count as a different plan here, and a rail settle
    cancels ONLY rail-family rows — non-rail rows are never touched.

    W-106 mirror reconciliation: _apply_period's rail upsert has
    already committed when this runs, so a cancel can land before or
    during the sync — it cancels the platform row, and an activation
    afterwards would insert a NEW active platform row nothing else
    reconciles (the rail row and the gate stay correct; only the
    platform mirror diverges). Re-read the rail state immediately
    before activating (skip when canceled) and again after the sync
    completes (re-apply the platform cancel — the same mechanism
    cancel_subscription uses — when now canceled). A cancel landing
    after the post-sync read runs its own platform cancel after this
    activation, so every interleave ends with the mirror canceled.
    """
    import sincor2.platform_payments as pp

    if _rail_row_canceled(wallet):
        logger.warning(
            "[RAIL-SUB] platform sync skipped for %s: rail subscription "
            "canceled before the sync ran", wallet)
        return
    existing = pp.get_subscription(wallet, plan_id)
    if (existing is not None
            and existing.get("last_payment_id") == challenge_id):
        return  # this payment already synced — never extend twice
    if not plan_changed:
        active_rail_others = [
            s for s in pp.list_subscriptions(wallet)
            if s.get("status") == "active"
            and s.get("plan_id") in SUB_PLANS
            and s.get("plan_id") != plan_id]
        if active_rail_others:
            plan_changed = True
    pp.activate_subscription_idempotent(
        wallet=wallet,
        plan_id=plan_id,
        product_name=SUB_PLANS[plan_id]["name"],
        token="USDC",
        tx_hash=tx_hash,
        payment_id=challenge_id,
        email="",
        period_days=PERIOD_DAYS,
        # Plan change: cancel-then-activate, scoped to the rail plan
        # family, inside the activation transaction (and skipped
        # entirely when this payment is already journaled).
        cancel_plan_ids=tuple(SUB_PLANS) if plan_changed else (),
    )
    if _rail_row_canceled(wallet):
        logger.warning(
            "[RAIL-SUB] platform mirror reconciled for %s: cancel "
            "landed during the sync — re-applying platform cancel",
            wallet)
        pp.cancel_wallet_subscriptions(wallet)


# --- Settle -----------------------------------------------------------------------
def _verify_fail(result: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False,
        "error": result.get("error", "verify_failed"),
        "detail": result.get("detail", ""),
    }
    # D2 item 3: pass the finality/OFAC context through (see
    # rail_routes._verify_fail).
    for _k in ("confirmations", "required_confirmations",
               "ofac_verdict", "ofac_detail"):
        if result.get(_k) is not None:
            out[_k] = result[_k]
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

    # W-106: a canceled subscription's outstanding challenges are dead.
    # (a) cancel_subscription() voids them — a voided challenge can no
    # longer settle or resurrect the subscription. (b) Belt-and-braces:
    # even without a recorded void (legacy rows), a challenge issued
    # before the stored cancel took effect is refused; only a challenge
    # issued AFTER the cancel — a genuinely fresh purchase — may settle.
    # A challenge that already extended a period keeps its duplicate ack
    # (it applies nothing new), so the carve-out below is ack-only.
    # NOTE: this top check alone is NOT sufficient — a cancel can land
    # after it, while the settle is in flight (verify / finalize / money
    # gate). The same fresh check therefore runs again immediately
    # before _apply_period (see below) and inside _apply_period itself.
    already_extended = bool(settle and settle.get("extended"))
    if _challenge_voided_by_cancel(cid, w,
                                   already_extended=already_extended):
        return {"ok": False, "error": "challenge_voided_by_cancel",
                "settle_id": settle_id,
                "subscription": subscription_status(w)}

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
                require_bound_challenge=True,
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
                      require_bound_challenge=True,
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

    # D2 item 1 (defense in depth, localized to the subscription settle
    # claim path): claim the tx in the shared registry before finalizing
    # or extending. verify_challenge already claimed it under this same
    # challenge_id on the first-attempt path, so this is idempotent
    # there; on retry paths that reuse the verified settle row without
    # re-verifying, this is the guard that stops a tx consumed by a
    # DIFFERENT entitlement (e.g. a platform checkout) from extending
    # this subscription.
    _consumed = _claim_consumed_tx_for_settle(
        tx_hash=tx, challenge_id=cid, wallet=w)
    if not _consumed.get("ok"):
        logger.warning(
            "[RAIL-SUB] settle refused for %s: tx already consumed by "
            "%s:%s", settle_id, _consumed.get("consumed_by_system"),
            _consumed.get("consumed_by_ref"))
        return {"ok": False, "error": "tx_hash_already_used",
                "settle_id": settle_id,
                "consumed_by_system": _consumed.get("consumed_by_system"),
                "consumed_by_ref": _consumed.get("consumed_by_ref")}

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

        # W-106 TOCTOU re-check — the fix for the Builder-B rejection of
        # abb71aed: re-validate the void/cancel state IMMEDIATELY BEFORE
        # applying the period, after the money gate, as the last step
        # before _apply_period. The top-of-settle check runs before
        # verify_challenge (on-chain RPC), finalize, and the gate; a
        # cancel landing anywhere in that window has since recorded
        # voided_at / canceled in the DB, and this fresh re-read catches
        # it. Same error semantics as the top check. The
        # already-extended ack path is preserved: a settle that already
        # applied its period is acknowledged, never refused, and
        # applies nothing new.
        settle_fresh = _get_sub_settle(settle_id) or settle
        if settle_fresh and settle_fresh.get("extended"):
            return _already_renewed(settle_fresh)
        if _challenge_voided_by_cancel(cid, w):
            return {"ok": False, "error": "challenge_voided_by_cancel",
                    "settle_id": settle_id,
                    "subscription": subscription_status(w)}

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
        # R5: the payment and the period are already irrevocably
        # committed at this point, so a sync failure must NOT flip the
        # settle result to failure. The claim is released inside
        # _sync_platform_once (done stays 0), leaving the sync
        # repairable via _reconcile_platform_sync on a duplicate ack.
        try:
            _sync_platform_once(
                settle_id=settle_id, wallet=w, plan_id=plan_id, tx_hash=tx,
                challenge_id=cid,
                plan_changed=bool(applied.get("plan_changed")))
        except Exception as sync_exc:
            logger.error(
                "[RAIL-SUB] platform sync failed for settle %s after "
                "the period committed — settle stands, sync left "
                "repairable via reconcile: %s", settle_id, sync_exc)
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
    """Cancel immediately: access revoked, platform record cancelled.

    W-106: cancelling also VOIDS every outstanding renewal challenge for
    the wallet, so a challenge issued before the cancel can no longer
    settle afterwards and resurrect the subscription (settle_renewal
    refuses voided challenges, and refuses any pre-cancel challenge
    against a stored canceled row). A genuinely fresh purchase — a new
    challenge issued after the cancel — is unaffected and still settles.
    """
    import sincor2.platform_payments as pp

    w = (wallet or "").strip().lower()
    now_iso = _utc_now_iso()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE rail_sub_challenges SET voided_at=? "
            "WHERE wallet=? AND voided_at=''",
            (now_iso, w))
        conn.commit()
    finally:
        conn.close()
    if _get_row(w):
        _update_row(w, status="canceled", canceled_at=now_iso)
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
