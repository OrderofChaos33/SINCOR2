"""Deterministic underwriting score + settlement engine for /v1 endpoints."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from .store import UnderwriteStore
from .types import iso, utcnow

_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_BURN_ADDRESSES = {"0x0000000000000000000000000000000000000000"}


def _fee_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "underwriting_fees.json"


def _load_fee_config() -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "chain_id": 8453,
        "asset": "AXM",
        "axm": "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a",
        "treasury": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
        "erc8004_identity": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
        "verify_fee_axm": 2,
        "underwrite_bps": 50,
        "burn_share": 0.5,
    }
    try:
        with _fee_config_path().open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            defaults.update(raw)
    except Exception:
        pass
    return defaults


_FEES = _load_fee_config()
CHAIN_ID = int(_FEES["chain_id"])
AXM = str(_FEES["axm"])
TREASURY = str(_FEES["treasury"])
ERC8004_IDENTITY = str(_FEES["erc8004_identity"])
VERIFY_FEE_AXM = int(_FEES["verify_fee_axm"])
UNDERWRITE_BPS = int(_FEES["underwrite_bps"])
BURN_SHARE = float(_FEES["burn_share"])


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _valid_address(value: Any) -> bool:
    return isinstance(value, str) and bool(_ADDR_RE.fullmatch(value))


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_agent_revoked(agent_id: Any, store: UnderwriteStore | None) -> bool:
    if not store or not isinstance(agent_id, str) or not agent_id.strip():
        return False
    agent_id = agent_id.strip()
    latest_revoke: datetime | None = None
    latest_register: datetime | None = None
    for row in store.iter("revokes"):
        if row.get("agent_id") != agent_id:
            continue
        ts = _parse_time(row.get("created_at"))
        if ts and (latest_revoke is None or ts > latest_revoke):
            latest_revoke = ts
    for row in store.iter("agents"):
        if row.get("agent_id") != agent_id:
            continue
        ts = _parse_time(row.get("created_at"))
        if ts and (latest_register is None or ts > latest_register):
            latest_register = ts
    return latest_revoke is not None and (latest_register is None or latest_revoke >= latest_register)


def score_mandate(payload: Dict[str, Any], store: UnderwriteStore | None = None) -> Dict[str, Any]:
    now = utcnow()
    requested = _as_float(payload.get("requested_usd") or payload.get("notional_usd"))
    cap = _as_float(payload.get("cap_usd") or payload.get("max_notional_usd") or requested)
    principal = payload.get("principal_wallet")
    agent_wallet = payload.get("agent_wallet")
    revoked = bool(payload.get("revoked")) or _is_agent_revoked(payload.get("agent_id"), store)
    deny_reasons: list[str] = []

    if revoked:
        deny_reasons.append("revoked")
    if not _valid_address(principal):
        deny_reasons.append("invalid principal")
    if not _valid_address(agent_wallet):
        deny_reasons.append("invalid agent wallet")
    if isinstance(principal, str) and principal.lower() in _BURN_ADDRESSES:
        deny_reasons.append("burn address")
    if isinstance(agent_wallet, str) and agent_wallet.lower() in _BURN_ADDRESSES:
        deny_reasons.append("burn address")

    starts_at = _parse_time(payload.get("starts_at"))
    expires_at = _parse_time(payload.get("expires_at"))
    if starts_at and starts_at > now:
        deny_reasons.append("outside window")
    if expires_at and expires_at < now:
        deny_reasons.append("outside window")

    if requested <= 0:
        deny_reasons.append("invalid notional")
    if cap > 0 and requested > cap:
        deny_reasons.append("exceeds cap")

    if store and principal and requested > 0:
        wash = 0
        for row in store.iter("receipts"):
            if row.get("payer") == principal and row.get("payee") == TREASURY:
                wash += 1
        if wash >= 20:
            deny_reasons.append("wash loop")

    score = 1.0
    if not payload.get("erc8004_registered"):
        score -= 0.1
    if deny_reasons:
        score = 0.0
    decision = "deny" if deny_reasons else "allow"
    return {"decision": decision, "score": round(max(score, 0.0), 4), "deny_reasons": deny_reasons}


class UnderwritingEngine:
    def __init__(self, store: Optional[UnderwriteStore] = None) -> None:
        self.store = store or UnderwriteStore()

    def register_agent(self, body: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = str(body.get("agent_id") or body.get("agentId") or "").strip()
        if not agent_id:
            raise ValueError("agent_id required")
        rec = {
            "agent_id": agent_id,
            "agent_wallet": body.get("agent_wallet"),
            "principal_wallet": body.get("principal_wallet"),
            "erc8004_registered": bool(body.get("erc8004_registered")),
            "created_at": iso(utcnow()),
            "revoked": False,
        }
        self.store.append("agents", rec)
        return rec

    def underwrite(self, body: Dict[str, Any]) -> Dict[str, Any]:
        scored = score_mandate(body, self.store)
        envelope_id = str(uuid4())
        record = {
            "envelope_id": envelope_id,
            "agent_id": body.get("agent_id"),
            "principal_wallet": body.get("principal_wallet"),
            "agent_wallet": body.get("agent_wallet"),
            "requested_usd": _as_float(body.get("requested_usd") or body.get("notional_usd")),
            "cap_usd": _as_float(body.get("cap_usd") or body.get("max_notional_usd")),
            "decision": scored["decision"],
            "score": scored["score"],
            "deny_reasons": scored["deny_reasons"],
            "created_at": iso(utcnow()),
        }
        self.store.append("underwrites", record)
        return record

    def settle(self, envelope_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        mandate = self.store.find_one("underwrites", "envelope_id", envelope_id)
        if not mandate:
            raise KeyError(envelope_id)
        if mandate.get("decision") != "allow":
            raise PermissionError("mandate denied")

        amount_usd = _as_float(body.get("amount_usd") or mandate.get("requested_usd"))
        if amount_usd <= 0:
            raise ValueError("amount_usd must be > 0")
        cap_usd = _as_float(mandate.get("cap_usd") or mandate.get("requested_usd"))
        if cap_usd > 0 and amount_usd > cap_usd:
            raise PermissionError("settlement exceeds underwritten cap")

        underwrite_fee = amount_usd * (UNDERWRITE_BPS / 10_000.0)
        burn_fee = underwrite_fee * BURN_SHARE
        treasury_fee = underwrite_fee - burn_fee

        receipt = {
            "receipt_id": str(uuid4()),
            "envelope_id": envelope_id,
            "agent_id": mandate.get("agent_id"),
            "payer": body.get("payer") or mandate.get("principal_wallet"),
            "payee": body.get("payee") or TREASURY,
            "amount_usd": round(amount_usd, 6),
            "verify_fee_axm": VERIFY_FEE_AXM,
            "underwrite_fee_usd": round(underwrite_fee, 6),
            "burn_fee_usd": round(burn_fee, 6),
            "treasury_fee_usd": round(treasury_fee, 6),
            "tx_hash": body.get("tx_hash"),
            "created_at": iso(utcnow()),
        }
        self.store.append("receipts", receipt)
        return receipt

    def revoke(self, agent_id: str, reason: str) -> Dict[str, Any]:
        if not agent_id:
            raise ValueError("agent_id required")
        rec = {
            "agent_id": agent_id,
            "reason": reason,
            "created_at": iso(utcnow()),
        }
        self.store.append("revokes", rec)
        return rec

    def receipt(self, receipt_id: str) -> Dict[str, Any] | None:
        return self.store.find_one("receipts", "receipt_id", receipt_id)

    def list_receipts(self) -> list[Dict[str, Any]]:
        return self.store.list("receipts")
