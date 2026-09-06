from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

SCHEMA_MANDATE = "sincor.intent_mandate.v1"
SCHEMA_ENVELOPE = "sincor.spend_envelope.v1"
SCHEMA_AUDIT = "sincor.underwrite_audit.v1"

CHAIN_ID_BASE = 8453


class REASON:
    OK_BASELINE = "OK_BASELINE"
    OK_REDUCED_DRIFT = "OK_REDUCED_DRIFT"
    OK_REDUCED_COUNTERPARTY = "OK_REDUCED_COUNTERPARTY"
    DENY_NO_MANDATE = "DENY_NO_MANDATE"
    DENY_MANDATE_EXPIRED = "DENY_MANDATE_EXPIRED"
    DENY_KILL_SWITCH = "DENY_KILL_SWITCH"
    DENY_CRITIC_FAIL_RATE = "DENY_CRITIC_FAIL_RATE"
    DENY_PATH_UNSAFE = "DENY_PATH_UNSAFE"
    DENY_SANCTIONS = "DENY_SANCTIONS"
    DENY_CAP_EXCEEDED = "DENY_CAP_EXCEEDED"
    DENY_UNKNOWN_PAYEE = "DENY_UNKNOWN_PAYEE"
    DENY_TOA_NO_VIABLE_PATH = "DENY_TOA_NO_VIABLE_PATH"
    HOLD_HUMAN_REQUIRED = "HOLD_HUMAN_REQUIRED"


OK_CODES = {
    REASON.OK_BASELINE,
    REASON.OK_REDUCED_DRIFT,
    REASON.OK_REDUCED_COUNTERPARTY,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.000001"))


def money_str(x: Decimal) -> str:
    return format(money(x), "f")


def new_id() -> str:
    return str(uuid4())


@dataclass
class IntentMandate:
    mandate_id: str
    issued_at: str
    expires_at: str
    operator_id: str
    controller_wallet: str
    agent_id: str
    asset: str
    chain_id: int
    max_notional_usd: str
    max_single_tx_usd: str
    allowed_skills: list[str] = field(default_factory=list)
    allowed_payees: list[str] = field(default_factory=list)
    denied_payees: list[str] = field(default_factory=list)
    human_approval_above_usd: str = "25.00"
    legal_customer_id: str | None = None
    kill_enabled: bool = True
    killed: bool = False
    schema: str = SCHEMA_MANDATE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IntentMandate":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SpendEnvelope:
    envelope_id: str
    mandate_id: str
    agent_id: str
    issued_at: str
    expires_at: str
    status: str
    asset: str
    chain_id: int
    amount_usd: str
    remaining_usd: str
    ttl_seconds: int
    reason_codes: list[str]
    tap: str
    allowlist_payees: list[str] = field(default_factory=list)
    allowlist_skills: list[str] = field(default_factory=list)
    max_tx_usd: str = "0"
    toa_run_id: str | None = None
    toa_paths_considered: int = 0
    toa_paths_viable: int = 0
    toa_scenario_id: str | None = None
    toa_composite: float | None = None
    toa_risk: float | None = None
    toa_rationale: str = ""
    denied: bool = False
    schema: str = SCHEMA_ENVELOPE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SpendEnvelope":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class AuditEvent:
    event_id: str
    ts: str
    kind: str
    agent_id: str
    payload: dict[str, Any]
    mandate_id: str | None = None
    envelope_id: str | None = None
    schema: str = SCHEMA_AUDIT

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuthorizeRequest:
    envelope_id: str
    payee: str
    amount_usd: str
    skill_id: str


@dataclass
class AuthorizeResult:
    allowed: bool
    reason: str
    remaining_usd: str
    receipt_id: str | None = None
    envelope_status: str = ""
