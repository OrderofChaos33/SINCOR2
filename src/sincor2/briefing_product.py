"""Path 48 — Governance & Treasury Risk Briefings (productization).

Turns the existing treasury-DAO reference swarm
(``src.sincor2.defi.treasury_dao``) and the central governance gate
(``sincor2.governance``) into a sellable briefing product:

* :func:`snapshot_from_swarm` / the snapshot contract below — the input.
* :func:`render_briefing` — deterministic rendering from a snapshot.
* Subscriber + delivery persistence (SQLite, same orders DB and style
  as ``platform_payments``).
* Delivery is generate + store + subscriber-retrievable only. This
  module never sends email or anything else outbound; actual outbound
  sending stays founder-gated.

Billing: NO new prices and NO new payment rail. Tiers are keys into the
existing :data:`sincor2.platform_payments.PLATFORM_PLANS`:

* ``report`` — one-time report plan ($49 reference in PLATFORM_PLANS)
  -> the standard briefing (summary, treasury risk, yield/fees).
* ``intel`` — monthly intelligence plan ($149/mo in PLATFORM_PLANS)
  -> the full briefing (standard sections + governance activity,
  per-hold detail, and risk flags).

Prices are read from PLATFORM_PLANS at call time, never restated here.
Entitlement/payment itself rides the existing plan/checkout flow in
``platform_payments`` (fiat OFF by default; there is deliberately no
``/checkout`` route added by this module).

Snapshot contract (plain JSON-able dict)::

    {
      "as_of": float,                      # epoch seconds
      "data_fresh": bool,                  # False -> status "unknown"
      "treasury_status": {...},            # treasury_dao.status_payload()
      "bands": [{"venue": str, "min_bps": int, "max_bps": int}],
      "holds": [{"hold_id": str, "allocations_bps": {venue: bps},
                 "created_by": str, "created_at": float,
                 "reviewed": bool, "review": dict | None}],
      "yield": {"realized_yield_wei": int, "fee_owed_wei": int,
                "fee_bps": int},
      "governance_decisions": [            # governance DECISION_LOG shape
          {"action": str, "risk_tier": str, "domain": str,
           "policy_result": "allow" | "deny", "ts": str}],
    }
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Optional

# Tier -> sections. Keys MUST stay a subset of PLATFORM_PLANS keys.
TIER_SECTIONS: dict[str, list[str]] = {
    "report": ["executive_summary", "treasury_risk", "yield_and_fees"],
    "intel": [
        "executive_summary",
        "treasury_risk",
        "yield_and_fees",
        "governance_activity",
        "hold_detail",
        "risk_flags",
    ],
}

VALID_STATUSES = ("active", "paused", "cancelled")
# Allowed status transitions (cancelled is terminal).
_TRANSITIONS = {
    "active": {"paused", "cancelled"},
    "paused": {"active", "cancelled"},
    "cancelled": set(),
}

HOLD_BACKLOG_SECONDS = 72 * 3600  # mirrors treasury_dao


def briefing_plans() -> dict[str, dict[str, Any]]:
    """Tier view sourced entirely from PLATFORM_PLANS (no local prices)."""
    from sincor2.platform_payments import PLATFORM_PLANS

    out: dict[str, dict[str, Any]] = {}
    for plan_id, sections in TIER_SECTIONS.items():
        plan = PLATFORM_PLANS[plan_id]
        out[plan_id] = {
            "plan_id": plan_id,
            "label": plan["label"],
            "product_name": plan["product_name"],
            "usd_reference": plan["usd_reference"],
            "billing": plan["billing"],
            "token": plan["token"],
            "sections": list(sections),
        }
    return out


# --------------------------------------------------------------------------
# Snapshot building from the real modules
# --------------------------------------------------------------------------
def snapshot_from_swarm(swarm: Any, decisions: Optional[list] = None,
                        data_fresh: bool = True,
                        now: Optional[float] = None) -> dict[str, Any]:
    """Build a snapshot dict from a live ``treasury_dao.TreasurySwarm``.

    ``decisions`` defaults to the governance gate's own DECISION_LOG.
    """
    from src.sincor2.defi import treasury_dao

    if decisions is None:
        try:
            from sincor2.governance import decision_log

            decisions = decision_log()
        except Exception:
            decisions = []
    bands = [
        {"venue": b.venue, "min_bps": b.min_bps, "max_bps": b.max_bps}
        for b in swarm.risk.bands.values()
    ]
    holds = [
        {
            "hold_id": h.hold_id,
            "allocations_bps": dict(h.allocations_bps),
            "created_by": h.created_by,
            "created_at": h.created_at,
            "reviewed": bool(h.reviewed),
            "review": h.review,
        }
        for h in swarm.holds
    ]
    return {
        "as_of": now if now is not None else time.time(),
        "data_fresh": bool(data_fresh),
        "treasury_status": treasury_dao.status_payload(
            swarm, data_fresh=data_fresh),
        "bands": bands,
        "holds": holds,
        "yield": {
            "realized_yield_wei": swarm.yield_ledger.realized_yield_wei,
            "fee_owed_wei": swarm.yield_ledger.fee_owed_wei,
            "fee_bps": treasury_dao.FEE_BPS,
        },
        "governance_decisions": list(decisions or []),
    }


# --------------------------------------------------------------------------
# Rendering (deterministic: sorted venues/holds, no wall-clock fields
# beyond the snapshot's own as_of)
# --------------------------------------------------------------------------
def _band_violations(snapshot: dict[str, Any]) -> list[str]:
    bands = {b["venue"]: b for b in snapshot.get("bands") or []}
    out: list[str] = []
    for hold in snapshot.get("holds") or []:
        for venue, bps in sorted((hold.get("allocations_bps") or {}).items()):
            if bps > 3000:
                out.append(f"{hold['hold_id'][:8]}: {venue} {bps}bps over 30% cap")
            band = bands.get(venue)
            if band and not (band["min_bps"] <= bps <= band["max_bps"]):
                out.append(
                    f"{hold['hold_id'][:8]}: {venue} {bps}bps outside band "
                    f"[{band['min_bps']},{band['max_bps']}]")
    return out


def render_briefing(snapshot: dict[str, Any], plan_id: str) -> dict[str, Any]:
    plan_id = (plan_id or "").strip().lower()
    plans = briefing_plans()
    if plan_id not in plans:
        raise ValueError(f"unknown briefing plan: {plan_id!r}")
    sections_wanted = TIER_SECTIONS[plan_id]
    as_of = float(snapshot.get("as_of") or 0)
    holds = sorted(snapshot.get("holds") or [],
                   key=lambda h: (h.get("created_at") or 0, h.get("hold_id") or ""))
    reviewed = [h for h in holds if h.get("reviewed")]
    backlog = [h for h in holds if not h.get("reviewed")
               and as_of - float(h.get("created_at") or as_of) > HOLD_BACKLOG_SECONDS]
    violations = _band_violations(snapshot)
    status = (snapshot.get("treasury_status") or {}).get("status") or (
        "ok" if snapshot.get("data_fresh", True) else "unknown")
    latest_alloc: dict[str, int] = holds[-1]["allocations_bps"] if holds else {}
    max_venue_bps = max(latest_alloc.values()) if latest_alloc else 0
    y = snapshot.get("yield") or {}

    metrics = {
        "holds_total": len(holds),
        "holds_reviewed": len(reviewed),
        "holds_backlog_over_72h": len(backlog),
        "band_violations": len(violations),
        "max_venue_bps_latest_hold": max_venue_bps,
        "realized_yield_wei": int(y.get("realized_yield_wei") or 0),
        "fee_owed_wei": int(y.get("fee_owed_wei") or 0),
        "fee_bps": int(y.get("fee_bps") or 0),
        "treasury_status": status,
    }

    sections: dict[str, Any] = {}
    if "executive_summary" in sections_wanted:
        sections["executive_summary"] = {
            "treasury_status": status,
            "holds_total": metrics["holds_total"],
            "holds_reviewed": metrics["holds_reviewed"],
            "backlog_over_72h": metrics["holds_backlog_over_72h"],
            "headline": (
                f"Treasury status {status}; {len(holds)} hold(s), "
                f"{len(reviewed)} reviewed, {len(backlog)} in >72h backlog, "
                f"{len(violations)} band violation(s)."),
        }
    if "treasury_risk" in sections_wanted:
        sections["treasury_risk"] = {
            "latest_allocations_bps": dict(sorted(latest_alloc.items())),
            "max_venue_bps": max_venue_bps,
            "per_venue_cap_bps": 3000,
            "band_violations": violations,
        }
    if "yield_and_fees" in sections_wanted:
        sections["yield_and_fees"] = {
            "realized_yield_wei": metrics["realized_yield_wei"],
            "fee_owed_wei": metrics["fee_owed_wei"],
            "fee_bps": metrics["fee_bps"],
        }
    if "governance_activity" in sections_wanted:
        decisions = snapshot.get("governance_decisions") or []
        denied = [d for d in decisions if d.get("policy_result") == "deny"]
        sections["governance_activity"] = {
            "decisions_total": len(decisions),
            "allowed": len(decisions) - len(denied),
            "denied": len(denied),
            "high_or_critical": sum(
                1 for d in decisions
                if d.get("risk_tier") in ("high", "critical")),
            "by_domain": _counts(d.get("domain") for d in decisions),
        }
    if "hold_detail" in sections_wanted:
        sections["hold_detail"] = {"holds": holds}
    if "risk_flags" in sections_wanted:
        flags: list[str] = []
        if status != "ok":
            flags.append(f"treasury status is {status} (upstream data not fresh)")
        flags += [f"band violation: {v}" for v in violations]
        flags += [f"backlog: hold {h['hold_id'][:8]} unreviewed >72h"
                  for h in backlog]
        if metrics["fee_owed_wei"] > 0:
            flags.append(f"unclaimed treasury fees: {metrics['fee_owed_wei']} wei")
        sections["risk_flags"] = {"flags": flags}

    lines = [f"# Governance & Treasury Risk Briefing ({plans[plan_id]['label']})",
             f"As of: {datetime.fromtimestamp(as_of, tz=timezone.utc).isoformat()}",
             f"Plan: {plan_id} ({plans[plan_id]['billing']})", ""]
    for name in sections_wanted:
        lines.append(f"## {name}")
        lines.append(json.dumps(sections[name], sort_keys=True))
        lines.append("")
    return {
        "plan_id": plan_id,
        "tier_sections": sections_wanted,
        "as_of": as_of,
        "metrics": metrics,
        "sections": sections,
        "markdown": "\n".join(lines),
    }


def _counts(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = str(v or "unknown")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------
# Persistence (SQLite in the shared orders DB, platform_payments style)
# --------------------------------------------------------------------------
def _db_path():
    from sincor2.data_paths import orders_db_path

    return orders_db_path()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS briefing_subscribers (
            id TEXT PRIMARY KEY, wallet TEXT NOT NULL, email TEXT,
            plan_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            token_hash TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS briefing_deliveries (
            id TEXT PRIMARY KEY, subscriber_id TEXT NOT NULL,
            plan_id TEXT NOT NULL, briefing_json TEXT NOT NULL,
            markdown TEXT NOT NULL, created_at TEXT NOT NULL)""")
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def create_subscriber(*, wallet: str, plan_id: str,
                      email: str = "") -> dict[str, Any]:
    """Create a subscriber record. Returns the record + one-time token.

    This records entitlement intent only; payment/provisioning rides the
    existing platform plan flow (see module docstring).
    """
    plan_id = (plan_id or "").strip().lower()
    if plan_id not in briefing_plans():
        return {"ok": False, "error": "unknown_plan"}
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return {"ok": False, "error": "wallet_required"}
    sub_id = f"bsub_{secrets.token_hex(8)}"
    token = secrets.token_urlsafe(32)
    now = _now_iso()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO briefing_subscribers (id, wallet, email, plan_id,"
            " status, token_hash, created_at, updated_at)"
            " VALUES (?,?,?,?, 'active', ?, ?, ?)",
            (sub_id, wallet, email or "", plan_id, _hash_token(token), now, now))
        conn.commit()
    return {"ok": True, "subscriber_id": sub_id, "wallet": wallet,
            "email": email or "", "plan_id": plan_id, "status": "active",
            "token": token}


def authenticate(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM briefing_subscribers WHERE token_hash=?",
            (_hash_token(token),)).fetchone()
    return dict(row) if row else None


def get_subscriber(subscriber_id: str) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM briefing_subscribers WHERE id=?",
            (subscriber_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out.pop("token_hash", None)
    return out


def set_subscriber_status(subscriber_id: str, new_status: str) -> dict[str, Any]:
    new_status = (new_status or "").strip().lower()
    if new_status not in VALID_STATUSES:
        return {"ok": False, "error": "invalid_status"}
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM briefing_subscribers WHERE id=?",
            (subscriber_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}
        current = row["status"]
        if new_status != current and new_status not in _TRANSITIONS.get(current, set()):
            return {"ok": False, "error": "invalid_transition",
                    "from": current, "to": new_status}
        conn.execute(
            "UPDATE briefing_subscribers SET status=?, updated_at=? WHERE id=?",
            (new_status, _now_iso(), subscriber_id))
        conn.commit()
    return {"ok": True, "subscriber_id": subscriber_id, "status": new_status}


def generate_delivery(subscriber: dict[str, Any],
                      snapshot: dict[str, Any]) -> dict[str, Any]:
    """Generate + store a briefing for an ACTIVE subscriber."""
    if subscriber.get("status") != "active":
        return {"ok": False, "error": "subscriber_not_active",
                "status": subscriber.get("status")}
    briefing = render_briefing(snapshot, subscriber["plan_id"])
    delivery_id = f"bdel_{secrets.token_hex(8)}"
    with _conn() as conn:
        conn.execute(
            "INSERT INTO briefing_deliveries (id, subscriber_id, plan_id,"
            " briefing_json, markdown, created_at) VALUES (?,?,?,?,?,?)",
            (delivery_id, subscriber["id"], subscriber["plan_id"],
             json.dumps(briefing, sort_keys=True), briefing["markdown"],
             _now_iso()))
        conn.commit()
    return {"ok": True, "delivery_id": delivery_id,
            "subscriber_id": subscriber["id"], "briefing": briefing}


def get_delivery(delivery_id: str, subscriber_id: str) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM briefing_deliveries WHERE id=? AND subscriber_id=?",
            (delivery_id, subscriber_id)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["briefing"] = json.loads(out.pop("briefing_json"))
    return out


def latest_delivery(subscriber_id: str) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM briefing_deliveries WHERE subscriber_id=?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (subscriber_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["briefing"] = json.loads(out.pop("briefing_json"))
    return out
