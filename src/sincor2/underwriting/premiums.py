"""SEAL underwriting premiums (Path 9).

A *premium* is the price of underwriting cover, charged **at envelope
issuance** — distinct from the pre-existing settlement fee in
``engine.settle`` (``underwrite_bps``, charged on settled amounts).
Before this module, no premium was priced or collected anywhere in the
underwriting package.

Pricing formula (``seal-premium.v1``, deterministic, Decimal-only):

    premium_usd = coverage_usd
                  * (premium_base_bps / 10_000)
                  * (duration_seconds / premium_reference_ttl_seconds)
                  * (1 + risk_score)

Inputs — all already produced by the issuance flow:

- ``coverage_usd``     granted envelope amount (``SpendEnvelope.amount_usd``
                       on the runtime path; the approved requested amount on
                       the /v1 engine path);
- ``duration_seconds`` envelope TTL (``SpendEnvelope.ttl_seconds``; on the
                       engine path, the mandate window when supplied, else
                       the reference window);
- ``risk_score``       TOA risk in [0, 1] (``SpendEnvelope.toa_risk`` =
                       1 - safety; on the engine path, 1 - underwrite score).

Defaults (overridable via ``config/underwriting_fees.json``):
``premium_base_bps = 100`` (1.00% of coverage per reference window),
``premium_reference_ttl_seconds = 2700`` (the baseline 45-minute envelope
TTL), ``premium_min_usd = 0.01`` (floor so any positive coverage prices
above zero — no silent free underwriting).

Properties: the quote is a pure function of its inputs (deterministic),
and non-decreasing in coverage, duration, and risk. Zero/negative
coverage or duration is *explicitly* unpriced: premium 0 with status
``not_charged`` — denied envelopes are not underwriting and never bear a
premium.

Collection: recorded exactly once per envelope in the store's
``premiums`` collection (append-only JSONL, same ledger pattern as
``receipts``), payable in full to treasury. **Premiums are revenue and
are never burned** — every record carries ``burn_usd = 0`` and
``treasury_usd == premium_usd``. Collection here is a ledger recording,
consistent with the package's ``ledger_sim`` tap: no funds move.

Refunds: revoking an envelope that was never drawn on (remaining ==
granted amount) refunds its premium exactly once. Once any spend has
been debited, the premium is earned and no refund is issued. Claims are
out of scope — this package has no claims/payout mechanism; the
envelope debit *is* the covered spend.

Non-interference: nothing in this module is consulted by ``Policy``,
``Facilitator``, or any signature/mandate verification. Premium state
(paid, unpaid, refunded) can never change an authorization or
verification outcome.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .types import iso, money, money_str, new_id, utcnow

PREMIUM_FORMULA_VERSION = "seal-premium.v1"
PREMIUMS_COLLECTION = "premiums"


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "underwriting_fees.json"


def _load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "treasury": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
        "premium_base_bps": 100,
        "premium_reference_ttl_seconds": 2700,
        "premium_min_usd": "0.01",
    }
    try:
        with _config_path().open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            cfg.update(raw)
    except Exception:
        pass
    return cfg


_CFG = _load_config()
TREASURY = str(_CFG["treasury"])
PREMIUM_BASE_BPS = int(_CFG["premium_base_bps"])
PREMIUM_REFERENCE_TTL_SECONDS = int(_CFG["premium_reference_ttl_seconds"])
PREMIUM_MIN_USD = Decimal(str(_CFG["premium_min_usd"]))


def risk_class_for(risk_score: float) -> str:
    if risk_score < 0.30:
        return "standard"
    if risk_score < 0.70:
        return "elevated"
    return "high"


@dataclass
class PremiumQuote:
    premium_id: str
    coverage_usd: str
    duration_seconds: int
    risk_score: float
    risk_class: str
    base_bps: int
    premium_usd: str
    status: str  # "quoted" | "not_charged"
    formula_version: str = PREMIUM_FORMULA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def quote_premium(
    *,
    coverage_usd: str | Decimal,
    duration_seconds: int,
    risk_score: float | None,
) -> PremiumQuote:
    """Pure, deterministic premium quote. See module docstring for formula."""
    coverage = money(coverage_usd)
    risk = min(1.0, max(0.0, float(risk_score or 0.0)))
    duration = int(duration_seconds or 0)
    if coverage <= 0 or duration <= 0:
        return PremiumQuote(
            premium_id=new_id(),
            coverage_usd=money_str(coverage),
            duration_seconds=duration,
            risk_score=risk,
            risk_class=risk_class_for(risk),
            base_bps=PREMIUM_BASE_BPS,
            premium_usd="0.000000",
            status="not_charged",
        )
    raw = (
        coverage
        * (Decimal(PREMIUM_BASE_BPS) / Decimal(10_000))
        * (Decimal(duration) / Decimal(PREMIUM_REFERENCE_TTL_SECONDS))
        * (Decimal(1) + Decimal(str(risk)))
    )
    premium = max(raw, PREMIUM_MIN_USD)
    return PremiumQuote(
        premium_id=new_id(),
        coverage_usd=money_str(coverage),
        duration_seconds=duration,
        risk_score=risk,
        risk_class=risk_class_for(risk),
        base_bps=PREMIUM_BASE_BPS,
        premium_usd=money_str(premium),
        status="quoted",
    )


class PremiumLedger:
    """Exactly-once premium collection / refund over the underwrite store."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def _records(self, envelope_id: str) -> list[dict[str, Any]]:
        return [
            r for r in self.store.list(PREMIUMS_COLLECTION)
            if r.get("envelope_id") == envelope_id
        ]

    def collected_for(self, envelope_id: str) -> dict[str, Any] | None:
        for rec in self._records(envelope_id):
            if rec.get("status") == "collected":
                return rec
        return None

    def get(self, envelope_id: str) -> dict[str, Any] | None:
        rows = self._records(envelope_id)
        return rows[-1] if rows else None

    def collect(
        self,
        quote: PremiumQuote,
        *,
        envelope_id: str,
        agent_id: str | None,
        mandate_id: str | None = None,
        payer: str | None = None,
    ) -> dict[str, Any]:
        """Record collection of ``quote`` for ``envelope_id`` exactly once.

        Re-collecting the same envelope returns the original record.
        A ``not_charged`` quote is a no-op that returns an explicit
        not-charged record (never persisted as revenue).
        """
        if quote.status != "quoted" or money(quote.premium_usd) <= 0:
            return {
                "premium_id": quote.premium_id,
                "envelope_id": envelope_id,
                "status": "not_charged",
                "premium_usd": "0.000000",
            }
        existing = self.collected_for(envelope_id)
        if existing is not None:
            return existing
        record = {
            "premium_id": quote.premium_id,
            "envelope_id": envelope_id,
            "mandate_id": mandate_id,
            "agent_id": agent_id,
            "payer": payer or agent_id,
            "payee": TREASURY,
            "coverage_usd": quote.coverage_usd,
            "duration_seconds": quote.duration_seconds,
            "risk_score": quote.risk_score,
            "risk_class": quote.risk_class,
            "base_bps": quote.base_bps,
            "premium_usd": quote.premium_usd,
            "treasury_usd": quote.premium_usd,
            "burn_usd": "0.000000",
            "formula_version": quote.formula_version,
            "status": "collected",
            "created_at": iso(utcnow()),
        }
        self.store.append(PREMIUMS_COLLECTION, record)
        self.store.audit(
            "premium.collected",
            agent_id or "unknown",
            {"premium_usd": quote.premium_usd, "payee": TREASURY},
            mandate_id=mandate_id,
            envelope_id=envelope_id,
        )
        return record

    def refund(self, envelope_id: str, *, reason: str) -> dict[str, Any] | None:
        """Refund a collected premium exactly once; None if not refundable."""
        collected = self.collected_for(envelope_id)
        if collected is None:
            return None
        for rec in self._records(envelope_id):
            if rec.get("status") == "refunded":
                return None
        record = {
            "premium_id": collected["premium_id"],
            "envelope_id": envelope_id,
            "mandate_id": collected.get("mandate_id"),
            "agent_id": collected.get("agent_id"),
            "payee": collected.get("payer"),
            "premium_usd": collected["premium_usd"],
            "treasury_usd": "0.000000",
            "burn_usd": "0.000000",
            "status": "refunded",
            "reason": reason,
            "created_at": iso(utcnow()),
        }
        self.store.append(PREMIUMS_COLLECTION, record)
        self.store.audit(
            "premium.refunded",
            collected.get("agent_id") or "unknown",
            {"premium_usd": collected["premium_usd"], "reason": reason},
            mandate_id=collected.get("mandate_id"),
            envelope_id=envelope_id,
        )
        return record
