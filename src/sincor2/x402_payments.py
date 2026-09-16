"""HTTP 402 micropayments in SINC (spec §5.4)."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from sincor2.agent_billing import record_platform_payment
from sincor2.platform_payments import (
    CHAIN_ID,
    atomic_to_display,
    display_to_atomic,
    token_address,
    token_decimals,
    TREASURY,
    verify_treasury_transfer,
)
from sincor2.treasury_inflow import record_inflow

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG = _ROOT / "config" / "x402_pricing.yaml"


def _db_path() -> Path:
    from sincor2.data_paths import orders_db_path

    return orders_db_path()


def init_x402_db() -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS x402_challenges (
            challenge_id TEXT PRIMARY KEY,
            resource_id TEXT NOT NULL,
            amount_atomic TEXT NOT NULL,
            amount_display REAL NOT NULL,
            payer_wallet TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            tx_hash TEXT,
            access_token TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            fulfilled_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def _conn() -> sqlite3.Connection:
    init_x402_db()
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def load_pricing() -> dict[str, Any]:
    if not _CONFIG.is_file():
        return {"resources": {}, "defaults": {}}
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}


def get_resource(resource_id: str) -> dict[str, Any] | None:
    cfg = load_pricing()
    res = (cfg.get("resources") or {}).get(resource_id)
    if not res:
        return None
    defaults = cfg.get("defaults") or {}
    token = str(res.get("token") or defaults.get("token") or "SINC").upper()
    if token == "AXIOM":
        token = "AXM"
    amount = float(
        res.get(f"amount_{token.lower()}")
        or res.get("amount")
        or res.get("amount_display")
        or res.get("amount_sinc")
        or 1
    )
    ttl = int(defaults.get("challenge_ttl_seconds", 900))
    resource = {
        "id": resource_id,
        "label": res.get("label", resource_id),
        "description": res.get("description", ""),
        "amount_display": amount,
        "amount_atomic": str(display_to_atomic(amount, token)),
        "token": token,
        "token_address": token_address(token),
        "token_decimals": token_decimals(token),
        "treasury": res.get("treasury", defaults.get("treasury", TREASURY)),
        "chain_id": int(res.get("chain_id", defaults.get("chain_id", CHAIN_ID))),
        "ttl_seconds": ttl,
        "skill_id": str(res.get("skill_id") or ""),
    }
    if token == "SINC":
        resource["amount_sinc"] = amount
    if token == "USDC":
        resource["amount_usdc"] = amount
    return resource


def list_resources() -> list[dict[str, Any]]:
    cfg = load_pricing()
    return [
        get_resource(rid)  # type: ignore
        for rid in (cfg.get("resources") or {}).keys()
    ]


def create_challenge(resource_id: str, *, payer_wallet: str = "") -> dict[str, Any]:
    resource = get_resource(resource_id)
    if not resource:
        return {"ok": False, "error": "unknown_resource"}

    now = datetime.now(timezone.utc)
    challenge_id = f"x402-{secrets.token_hex(8)}"
    expires = now + timedelta(seconds=resource["ttl_seconds"])

    with _conn() as conn:
        conn.execute(
            """INSERT INTO x402_challenges
               (challenge_id, resource_id, amount_atomic, amount_display, payer_wallet,
                status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                challenge_id,
                resource_id,
                resource["amount_atomic"],
                resource["amount_display"],
                payer_wallet.lower() if payer_wallet else "",
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        conn.commit()

    payload = {
        "x402Version": 1,
        "scheme": "exact",
        "network": "base",
        "chainId": resource["chain_id"],
        "maxAmountRequired": resource["amount_atomic"],
        "resource": f"/x402/{resource_id}",
        "description": resource["description"],
        "payTo": resource["treasury"],
        "asset": resource["token_address"],
        "extra": {
            "challenge_id": challenge_id,
            "token": resource["token"],
            "amount_display": resource["amount_display"],
            "decimals": resource["token_decimals"],
            "skill_id": resource.get("skill_id", ""),
        },
    }

    return {
        "ok": False,
        "http_status": 402,
        "error": "payment_required",
        "challenge_id": challenge_id,
        "expires_at": expires.isoformat(),
        "payment": payload,
        "message": (
            f"Send {resource['amount_display']} {resource['token']} "
            f"to {resource['treasury']} on Base"
        ),
    }


def verify_challenge(
    challenge_id: str,
    tx_hash: str,
    *,
    payer_wallet: str = "",
) -> dict[str, Any]:
    if not challenge_id or not tx_hash:
        return {"ok": False, "error": "challenge_id_and_tx_hash_required"}

    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM x402_challenges WHERE challenge_id=?", (challenge_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "challenge_not_found"}
        if row["status"] == "fulfilled":
            return {
                "ok": True,
                "status": "already_fulfilled",
                "access_token": row["access_token"],
                "resource_id": row["resource_id"],
            }

        expires = row["expires_at"]
        if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) < datetime.now(timezone.utc):
            return {"ok": False, "error": "challenge_expired"}

    resource = get_resource(str(row["resource_id"]))
    vr = verify_treasury_transfer(
        tx_hash,
        token=str((resource or {}).get("token", "SINC")),
        expected_atomic=int(row["amount_atomic"]),
        treasury=(resource or {}).get("treasury"),
        payer_wallet=payer_wallet,
    )
    if not vr.get("ok"):
        return vr

    access_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()

    with _conn() as conn:
        conn.execute(
            """UPDATE x402_challenges SET status='fulfilled', tx_hash=?, payer_wallet=?,
               access_token=?, fulfilled_at=? WHERE challenge_id=?""",
            (tx_hash, vr["payer_wallet"], access_token, now, challenge_id),
        )
        conn.commit()

    return {
        "ok": True,
        "status": "fulfilled",
        "challenge_id": challenge_id,
        "resource_id": row["resource_id"],
        "access_token": access_token,
        "tx_hash": tx_hash,
        "payer_wallet": vr["payer_wallet"],
    }


def finalize_challenge_payment(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok") or result.get("status") != "fulfilled":
        return {}
    resource = get_resource(str(result.get("resource_id") or ""))
    if not resource:
        return {}
    token = resource["token"]
    amount_display = float(result.get("amount_display") or resource["amount_display"])
    payment = record_platform_payment(
        tx_hash=str(result.get("tx_hash") or ""),
        payer_wallet=str(result.get("payer_wallet") or ""),
        token=token,
        amount_atomic=int(result.get("amount_atomic") or resource["amount_atomic"]),
        product_name=f"x402:{resource['id']}",
        plan_id="x402",
        payment_id=str(result.get("challenge_id") or ""),
    )
    inflow = record_inflow(
        amount_display,
        asset=token,
        source="x402_payment",
        usd_estimate=amount_display,
        tx_hash=str(result.get("tx_hash") or ""),
        note=f"x402:{resource['id']}",
        projected=False,
    )
    return {
        "payment_log": payment,
        "treasury_inflow": inflow.to_dict() if hasattr(inflow, "to_dict") else {},
    }


def execute_paid_resource(resource_id: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    resource = get_resource(resource_id)
    if not resource:
        return 404, {"ok": False, "error": "unknown_resource"}
    if resource.get("skill_id"):
        from verticals.loader import instantiate_vertical_agents
        from sincor2.vertical_dispatch import dispatch_vertical_task

        input_payload = payload or {}
        input_text = json.dumps({"payload": input_payload})
        dispatched = dispatch_vertical_task(
            resource["skill_id"],
            input_text,
            {"vertical_agents": instantiate_vertical_agents()},
        )
        if not dispatched:
            return 503, {
                "ok": False,
                "error": "skill_unavailable",
                "resource": resource_id,
                "skill_id": resource["skill_id"],
            }
        output, error = dispatched
        if error:
            return 502, {
                "ok": False,
                "error": "skill_execution_failed",
                "resource": resource_id,
                "skill_id": resource["skill_id"],
                "detail": error,
            }
        try:
            execution = json.loads(output)
        except json.JSONDecodeError:
            execution = {"status": "success", "result": {"raw": output}}
        return 200, {
            "ok": True,
            "resource": resource_id,
            "skill_id": resource["skill_id"],
            "execution": execution,
        }
    return 200, {
        "ok": True,
        "resource": resource_id,
        "message": "Access granted. Resource handler may be extended per config/x402_pricing.yaml.",
    }


def access_granted(access_token: str, resource_id: str) -> bool:
    if not access_token:
        return False
    with _conn() as conn:
        row = conn.execute(
            """SELECT challenge_id FROM x402_challenges
               WHERE access_token=? AND resource_id=? AND status='fulfilled'""",
            (access_token, resource_id),
        ).fetchone()
    return bool(row)