"""Clearing-volume ledger + volume step-up fee resolver (P03).

Founder ratification (2026-11-16): the Vickrey platform fee stays at the
penetration rate of 5 % (``BASE_FEE_BPS`` = 500) — a 50 % discount to the
researched market median — and steps up to the market median of 10 %
(``STEP_UP_FEE_BPS`` = 1000) once monthly clearing volume reaches
$1,000,000 for **2 consecutive months**. This module is the machinery for
that clause:

- ``fee_bps_for_volumes`` — the pure resolver. Given the current and
  previous calendar months' clearing volumes (USD), it returns 1000 bps
  only when BOTH are >= $1,000,000, else the 500 bps base rate.
- An append-only JSONL ledger recording every clearing (amount +
  timestamp) so monthly volumes are computed from records, never from
  estimates. Path is configurable via the
  ``SINCOR_VICKREY_CLEARING_LEDGER`` env var; the default lives next to
  the module in ``marketplace/contract_net/state/`` (gitignored runtime
  state, same pattern as ``liveness/state/``).

Amounts on the wire are integer micro-AXM (see ``types.MICRO_AXM``);
volumes are tracked in USD terms at 1 AXM = $1 face for threshold
purposes (the fee canon prices in AXM; the founder's threshold is stated
in dollars of clearing volume).

The rate applied to a clearing is resolved from the volume history
recorded *before* that clearing — a clearing never lifts its own rate.
Ledger I/O is best-effort: a missing/unreadable ledger reads as empty
history (base rate, the safe default) and a failed append never breaks
clearing, which stays pure settlement logic.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Tuple, Union

from .types import MICRO_AXM

BASE_FEE_BPS = 500  # penetration rate (founder ratification 2026-11-16)
STEP_UP_FEE_BPS = 1000  # researched market median
STEP_UP_VOLUME_THRESHOLD_USD = Decimal("1000000")  # $1M monthly clearing

LEDGER_ENV_VAR = "SINCOR_VICKREY_CLEARING_LEDGER"

VolumeLike = Union[int, float, str, Decimal]


def _as_decimal(value: VolumeLike) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid volume value: {value!r}") from exc


def fee_bps_for_volumes(
    monthly_clearing_volume_current: VolumeLike,
    monthly_clearing_volume_previous: VolumeLike,
) -> int:
    """Resolve the Vickrey platform fee rate (bps) from monthly volumes.

    Returns ``STEP_UP_FEE_BPS`` (1000) when the current AND previous
    calendar months both cleared >= $1,000,000; otherwise the base
    ``BASE_FEE_BPS`` (500). One strong month alone never steps the rate
    up — the ratified condition is two consecutive months at/above the
    threshold.
    """
    current = _as_decimal(monthly_clearing_volume_current)
    previous = _as_decimal(monthly_clearing_volume_previous)
    if current >= STEP_UP_VOLUME_THRESHOLD_USD and previous >= STEP_UP_VOLUME_THRESHOLD_USD:
        return STEP_UP_FEE_BPS
    return BASE_FEE_BPS


def default_ledger_path() -> Path:
    override = os.environ.get(LEDGER_ENV_VAR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "state" / "vickrey_clearings.jsonl"


def _month_key(moment: datetime) -> Tuple[int, int]:
    return (moment.year, moment.month)


def _previous_month_key(key: Tuple[int, int]) -> Tuple[int, int]:
    year, month = key
    return (year - 1, 12) if month == 1 else (year, month - 1)


def monthly_volumes_usd(
    ledger_path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> Tuple[Decimal, Decimal]:
    """Return (current_month, previous_month) clearing volumes in USD.

    Computed from the JSONL ledger records (calendar months, UTC).
    A missing or unreadable ledger reads as (0, 0) — empty history.
    Malformed lines are skipped, never fatal.
    """
    path = Path(ledger_path) if ledger_path is not None else default_ledger_path()
    moment = now or datetime.now(timezone.utc)
    current_key = _month_key(moment)
    previous_key = _previous_month_key(current_key)
    totals = {current_key: Decimal("0"), previous_key: Decimal("0")}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return totals[current_key], totals[previous_key]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            ts = datetime.fromisoformat(str(record["ts"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            key = _month_key(ts.astimezone(timezone.utc))
            if key in totals:
                totals[key] += _as_decimal(record["clearing_usd"])
        except (KeyError, TypeError, ValueError):
            continue
    return totals[current_key], totals[previous_key]


def resolve_fee_bps(
    ledger_path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> int:
    """Fee rate (bps) for the next clearing, from recorded volume history."""
    current, previous = monthly_volumes_usd(ledger_path, now=now)
    return fee_bps_for_volumes(current, previous)


def record_clearing(
    *,
    auction_id: str,
    task_id: str,
    clearing_price_micro_axm: int,
    fee_bps: int,
    cleared_at: Optional[datetime] = None,
    ledger_path: Optional[Path] = None,
) -> None:
    """Append one clearing record (amount + timestamp) to the JSONL ledger.

    Best-effort: an unwritable path never breaks clearing.
    """
    path = Path(ledger_path) if ledger_path is not None else default_ledger_path()
    moment = cleared_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    usd = (Decimal(clearing_price_micro_axm) / Decimal(MICRO_AXM)).quantize(Decimal("0.000001"))
    record = {
        "ts": moment.astimezone(timezone.utc).isoformat(),
        "auction_id": auction_id,
        "task_id": task_id,
        "clearing_price_micro_axm": int(clearing_price_micro_axm),
        "clearing_usd": str(usd),
        "fee_bps": int(fee_bps),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass
