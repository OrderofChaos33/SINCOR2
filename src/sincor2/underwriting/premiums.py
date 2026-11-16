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

Dynamic adjustment clause (founder-ratified 2026-11-16): the 100 bps base
is a launch *floor*, not a constant. ``resolve_effective_base_bps`` runs
on every quote and may raise the effective base toward the researched
market-median *cap* of 222.5 bps — never below the floor, never above
the cap — under two triggers, both computed from records already in the
underwrite store plus one operator-maintained config figure:

- **GTV trigger** — trailing-30-day settled volume (``amount_usd`` summed
  over the store's ``receipts`` collection, i.e. settlements recorded by
  ``engine.settle``) reaches ``premium_gtv_threshold_usd`` (default
  $1,000,000): the founder's launch discount expires and the effective
  base is the cap.
- **Runway trigger** — the underwriting reserve's runway,
  ``underwriting_reserve_usd / trailing-30-day burn rate``, falls below
  ``premium_runway_min_months`` (default 6): the base steps up in
  ``premium_step_bps`` increments (default +50) until the *projected*
  runway at the stepped rate reaches the minimum or the cap is hit.
  The trailing burn rate is the burn the store's records actually
  carry: settlement burn fees (``receipts.burn_fee_usd``) plus the
  premium records' burn share (``premiums.burn_usd`` — structurally
  zero today, since premiums are never burned; it is summed anyway so
  the rate tracks the field if that ever changes). The projection
  credits only the *incremental* premium income a stepped rate would
  have collected on the window's actual issuance (each collected
  premium rescaled by ``candidate_bps / base_bps`` recorded on it,
  refunds held constant): projected drain = burn rate − incremental
  income, and projected runway = reserve / projected drain (infinite
  when the drain is not positive). A zero trailing burn rate means an
  infinite runway — no step-up, and no division by zero.

Input sources are explicit — nothing is invented: settled volume and
burn come only from ``receipts``/``premiums`` records inside the
trailing 30 days (records without a parseable ``created_at`` are
excluded — their age cannot be proven), and the reserve balance comes
only from ``underwriting_reserve_usd`` in
``config/underwriting_fees.json`` (default ``"0"`` — an operator
maintains it; no live balance feeds it yet). With no history and the
default reserve, the zero-burn rule yields an infinite runway and every
quote resolves to the 100 bps floor: the not-yet-live default state.

Every quote whose effective base differs from the floor appends one
record to the store's ``premium_adjustments`` collection (append-only
JSONL alongside ``premiums``): timestamp, effective bps, which trigger
fired, and the exact inputs used. Quotes at the floor append nothing.

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
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .types import iso, money, money_str, new_id, parse_iso, utcnow

PREMIUM_FORMULA_VERSION = "seal-premium.v1"
PREMIUMS_COLLECTION = "premiums"
ADJUSTMENTS_COLLECTION = "premium_adjustments"
RECEIPTS_COLLECTION = "receipts"
TRAILING_WINDOW_DAYS = 30


def _config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "underwriting_fees.json"


def _load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "treasury": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
        "premium_base_bps": 100,
        "premium_reference_ttl_seconds": 2700,
        "premium_min_usd": "0.01",
        # Dynamic adjustment clause knobs (see module docstring).
        "premium_base_bps_cap": "222.5",
        "premium_step_bps": 50,
        "premium_gtv_threshold_usd": "1000000",
        "premium_runway_min_months": 6,
        # Operator-maintained underwriting reserve balance (USD). No live
        # balance feeds this yet; "0" is the honest not-yet-live default.
        "underwriting_reserve_usd": "0",
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
# Dynamic adjustment clause constants. The floor is PREMIUM_BASE_BPS (the
# ratified launch base); the cap is the researched market median.
PREMIUM_BASE_BPS_CAP = Decimal(str(_CFG["premium_base_bps_cap"]))
PREMIUM_STEP_BPS = Decimal(str(_CFG["premium_step_bps"]))
PREMIUM_GTV_THRESHOLD_USD = money(_CFG["premium_gtv_threshold_usd"])
PREMIUM_RUNWAY_MIN_MONTHS = Decimal(str(_CFG["premium_runway_min_months"]))
UNDERWRITING_RESERVE_USD = money(_CFG["underwriting_reserve_usd"])


def risk_class_for(risk_score: float) -> str:
    if risk_score < 0.30:
        return "standard"
    if risk_score < 0.70:
        return "elevated"
    return "high"


# ---------------------------------------------------------------------------
# Dynamic adjustment clause — effective base-rate resolver
#
# Input sources (explicit; nothing is read from anywhere else):
#   * settled volume / burn — the underwrite store's own ``receipts`` and
#     ``premiums`` collections, restricted to the trailing 30 days by each
#     record's ``created_at`` (records whose timestamp does not parse are
#     excluded: their age cannot be proven);
#   * reserve balance — ``underwriting_reserve_usd`` from
#     config/underwriting_fees.json (UNDERWRITING_RESERVE_USD), an
#     operator-maintained figure, default "0".
# With no store (or an empty one) every history total is zero, the
# zero-burn rule makes the runway infinite, and the resolver returns the
# floor — the not-yet-live default state.
# ---------------------------------------------------------------------------


@dataclass
class BaseRateResolution:
    effective_bps: Decimal
    trigger: str | None  # None | "gtv" | "runway" | "both"
    inputs: dict[str, Any] = field(default_factory=dict)


def _bps_json(value: Decimal) -> int | float:
    """JSON-friendly bps: integral rates stay ints (100), the cap is 222.5."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _in_window(record: dict[str, Any], since: datetime) -> bool:
    raw = record.get("created_at")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        return parse_iso(raw) >= since
    except Exception:
        return False


def _window_totals(store: Any, since: datetime) -> dict[str, Any]:
    """Trailing-window totals from the store's own records.

    - ``settled_volume``: ``amount_usd`` over ``receipts`` (settlements
      recorded by ``engine.settle``) — the GTV measure.
    - ``burn_rate``: ``burn_fee_usd`` over ``receipts`` (the settlement
      fee's burn share) plus ``burn_usd`` over ``premiums`` (structurally
      zero today — premiums are never burned — summed so the rate tracks
      the field if that changes).
    - ``collected``: (premium_usd, base_bps) per collected premium record,
      the actual issuance the runway projection rescales.
    - ``refunded``: premium_usd over refunded premium records (held
      constant by the projection — a future rate does not un-refund).
    """
    settled_volume = Decimal(0)
    burn_rate = Decimal(0)
    collected: list[tuple[Decimal, Decimal]] = []
    refunded = Decimal(0)
    for rec in store.list(RECEIPTS_COLLECTION):
        if not _in_window(rec, since):
            continue
        settled_volume += money(rec.get("amount_usd") or 0)
        burn_rate += money(rec.get("burn_fee_usd") or 0)
    for rec in store.list(PREMIUMS_COLLECTION):
        if not _in_window(rec, since):
            continue
        burn_rate += money(rec.get("burn_usd") or 0)
        status = rec.get("status")
        if status == "collected":
            base = money(rec.get("base_bps") or PREMIUM_BASE_BPS)
            if base <= 0:
                base = Decimal(PREMIUM_BASE_BPS)
            collected.append((money(rec.get("premium_usd") or 0), base))
        elif status == "refunded":
            refunded += money(rec.get("premium_usd") or 0)
    return {
        "settled_volume": settled_volume,
        "burn_rate": burn_rate,
        "collected": collected,
        "refunded": refunded,
    }


def resolve_effective_base_bps(
    store: Any | None = None,
    *,
    now: datetime | None = None,
    reserve_usd: Decimal | str | None = None,
) -> BaseRateResolution:
    """Resolve the effective premium base rate under the adjustment clause.

    Pure with respect to its inputs: it reads (never writes) the store.
    The caller (``quote_premium``) owns the adjustments-log write.
    """
    floor = Decimal(PREMIUM_BASE_BPS)
    cap = PREMIUM_BASE_BPS_CAP
    reserve = (
        money(reserve_usd) if reserve_usd is not None else UNDERWRITING_RESERVE_USD
    )
    moment = now or utcnow()
    since = moment - timedelta(days=TRAILING_WINDOW_DAYS)
    if store is not None:
        totals = _window_totals(store, since)
    else:
        totals = {
            "settled_volume": Decimal(0),
            "burn_rate": Decimal(0),
            "collected": [],
            "refunded": Decimal(0),
        }
    settled_volume: Decimal = totals["settled_volume"]
    burn_rate: Decimal = totals["burn_rate"]
    collected: list[tuple[Decimal, Decimal]] = totals["collected"]
    collected_total = sum((p for p, _ in collected), Decimal(0))
    net_income = collected_total - totals["refunded"]

    # Trigger 1 — GTV: trailing settled volume at/over the threshold
    # expires the launch discount outright (effective base = cap).
    gtv_fired = settled_volume >= PREMIUM_GTV_THRESHOLD_USD

    # Trigger 2 — runway: reserve / trailing burn rate. A zero burn rate
    # is an infinite runway (no step-up, no division by zero).
    runway_months: Decimal | None = None
    runway_fired = False
    if burn_rate > 0:
        runway_months = reserve / burn_rate
        runway_fired = runway_months < PREMIUM_RUNWAY_MIN_MONTHS

    def projected_runway(candidate: Decimal) -> Decimal | None:
        """Runway if the base had been ``candidate`` over the window.

        Only the *incremental* premium income is credited: each collected
        premium is rescaled by candidate / (base it was recorded at), and
        the drain is the burn rate minus that increment. A non-positive
        drain is an infinite runway (returned as None).
        """
        projected = sum(
            (premium * candidate / base for premium, base in collected),
            Decimal(0),
        )
        drain = burn_rate - (projected - collected_total)
        if drain <= 0:
            return None
        return reserve / drain

    stepped = floor
    if runway_fired:
        candidate = floor
        while candidate < cap:
            projected = projected_runway(candidate)
            if projected is None or projected >= PREMIUM_RUNWAY_MIN_MONTHS:
                break
            candidate = min(candidate + PREMIUM_STEP_BPS, cap)
        stepped = candidate

    if gtv_fired:
        effective = cap
    elif runway_fired:
        effective = stepped
    else:
        effective = floor
    if gtv_fired and runway_fired:
        trigger: str | None = "both"
    elif gtv_fired:
        trigger = "gtv"
    elif runway_fired:
        trigger = "runway"
    else:
        trigger = None

    inputs = {
        "settled_volume_30d_usd": money_str(settled_volume),
        "gtv_threshold_usd": money_str(PREMIUM_GTV_THRESHOLD_USD),
        "burn_rate_30d_usd": money_str(burn_rate),
        "reserve_usd": money_str(reserve),
        "runway_months": (
            None
            if runway_months is None
            else str(runway_months.quantize(Decimal("0.0001")))
        ),
        "runway_min_months": _bps_json(PREMIUM_RUNWAY_MIN_MONTHS),
        "premium_income_30d_usd": money_str(net_income),
        "window_days": TRAILING_WINDOW_DAYS,
    }
    return BaseRateResolution(effective_bps=effective, trigger=trigger, inputs=inputs)


def _log_adjustment(store: Any, resolution: BaseRateResolution, now: datetime) -> None:
    """Append one adjustments-log record for an adjusted quote.

    Called by ``quote_premium`` exactly when the resolved base differs
    from the floor (equivalently, when a trigger fired). Quotes at the
    floor write nothing.
    """
    store.append(
        ADJUSTMENTS_COLLECTION,
        {
            "timestamp": iso(now),
            "effective_bps": _bps_json(resolution.effective_bps),
            "trigger": resolution.trigger,
            "inputs": resolution.inputs,
        },
    )


@dataclass
class PremiumQuote:
    premium_id: str
    coverage_usd: str
    duration_seconds: int
    risk_score: float
    risk_class: str
    base_bps: int | float  # the effective base actually applied
    premium_usd: str
    status: str  # "quoted" | "not_charged"
    formula_version: str = PREMIUM_FORMULA_VERSION
    # Dynamic adjustment clause (additive; see resolver above).
    base_bps_effective: int | float = PREMIUM_BASE_BPS
    base_bps_floor: int = PREMIUM_BASE_BPS
    base_bps_cap: int | float = 222.5
    adjustment_trigger: str | None = None  # None | "gtv" | "runway" | "both"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def quote_premium(
    *,
    coverage_usd: str | Decimal,
    duration_seconds: int,
    risk_score: float | None,
    store: Any | None = None,
    now: datetime | None = None,
) -> PremiumQuote:
    """Premium quote at the effective base rate. See module docstring.

    With no ``store`` there is no history to resolve against, so the
    quote lands on the 100 bps floor and nothing is logged — identical
    to the pre-clause behaviour. With a store, the adjustment clause
    resolves the effective base from the store's records, and an
    adjusted quote appends one record to the adjustments log.
    """
    moment = now or utcnow()
    resolution = resolve_effective_base_bps(store, now=moment)
    effective = resolution.effective_bps
    rate_fields = {
        "base_bps": _bps_json(effective),
        "base_bps_effective": _bps_json(effective),
        "base_bps_floor": PREMIUM_BASE_BPS,
        "base_bps_cap": _bps_json(PREMIUM_BASE_BPS_CAP),
        "adjustment_trigger": resolution.trigger,
    }
    coverage = money(coverage_usd)
    risk = min(1.0, max(0.0, float(risk_score or 0.0)))
    duration = int(duration_seconds or 0)
    if coverage <= 0 or duration <= 0:
        quote = PremiumQuote(
            premium_id=new_id(),
            coverage_usd=money_str(coverage),
            duration_seconds=duration,
            risk_score=risk,
            risk_class=risk_class_for(risk),
            premium_usd="0.000000",
            status="not_charged",
            **rate_fields,
        )
    else:
        raw = (
            coverage
            * (effective / Decimal(10_000))
            * (Decimal(duration) / Decimal(PREMIUM_REFERENCE_TTL_SECONDS))
            * (Decimal(1) + Decimal(str(risk)))
        )
        premium = max(raw, PREMIUM_MIN_USD)
        quote = PremiumQuote(
            premium_id=new_id(),
            coverage_usd=money_str(coverage),
            duration_seconds=duration,
            risk_score=risk,
            risk_class=risk_class_for(risk),
            premium_usd=money_str(premium),
            status="quoted",
            **rate_fields,
        )
    if store is not None and resolution.trigger is not None:
        _log_adjustment(store, resolution, moment)
    return quote


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
            "base_bps_effective": quote.base_bps_effective,
            "adjustment_trigger": quote.adjustment_trigger,
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
