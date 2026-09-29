"""P26 fee/ROI telemetry ingestor.

Records realized observations per protocol — fee inflow actually measured
and realized ROI actually observed. No forecasts, no invented numbers: when
no telemetry exists for a protocol, the ranker says so explicitly and falls
back to catalog-only figures with confidence "unproven".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FeeObservation:
    protocol_id: str
    ts: float
    fee_usd: float       # realized fee inflow measured in the window
    aum_usd: float        # AUM the fee was earned on


@dataclass
class RoiObservation:
    protocol_id: str
    ts: float
    roi: float           # realized ROI for the window (fraction, can be < 0)
    window_s: float


class Telemetry:
    def __init__(self) -> None:
        self._fees: Dict[str, List[FeeObservation]] = {}
        self._rois: Dict[str, List[RoiObservation]] = {}

    def record_fee(self, protocol_id: str, fee_usd: float, aum_usd: float,
                   ts: Optional[float] = None) -> FeeObservation:
        if fee_usd < 0 or aum_usd < 0:
            raise ValueError("telemetry values cannot be negative")
        obs = FeeObservation(protocol_id, time.time() if ts is None else ts,
                             fee_usd, aum_usd)
        self._fees.setdefault(protocol_id, []).append(obs)
        return obs

    def record_roi(self, protocol_id: str, roi: float, window_s: float,
                   ts: Optional[float] = None) -> RoiObservation:
        if window_s <= 0:
            raise ValueError("window must be positive")
        obs = RoiObservation(protocol_id, time.time() if ts is None else ts,
                             roi, window_s)
        self._rois.setdefault(protocol_id, []).append(obs)
        return obs

    def latest_fee_rate(self, protocol_id: str) -> Optional[float]:
        """Latest realized annualized fee rate (fee_usd / aum_usd), or None."""
        obs = self._fees.get(protocol_id)
        if not obs:
            return None
        last = obs[-1]
        if last.aum_usd <= 0:
            return None
        return last.fee_usd / last.aum_usd

    def latest_roi(self, protocol_id: str) -> Optional[float]:
        obs = self._rois.get(protocol_id)
        return obs[-1].roi if obs else None

    def has_realized_data(self, protocol_id: str) -> bool:
        return bool(self._fees.get(protocol_id)) or bool(self._rois.get(protocol_id))
