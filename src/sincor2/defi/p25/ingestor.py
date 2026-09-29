"""P25 weight ingestor: TOA swarm signals -> normalized portfolio weights.

Every feed carries a timestamp. Any feed older than ``feed_staleness_s``
(3600 s) makes the ingestor emit HOLD: no rebalance may proceed, while reads
keep serving the last-good weights. P26 (the meta ranker) is excluded from the
signal universe by construction.

Signal per protocol: ``signal = max(toa_score, 0) * (1 - risk_score)``.
Eligibility (risk_score > 0.60 excluded) is read from the live catalog at
ingest time, never from a hardcoded list.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from ..catalog import PROTOCOL_BY_ID
from . import PARAMS

STATUS_OK = "OK"
STATUS_HOLD = "HOLD"

# Cash sleeve: the P22 stable leg. Risk 0, always eligible.
CASH_ID = "CASH_USDC"


@dataclass(frozen=True)
class FeedSignal:
    protocol_id: str
    toa_score: float
    ts: float  # feed timestamp (unix seconds)


@dataclass
class IngestResult:
    status: str                      # OK | HOLD
    weights: Dict[str, float]        # protocol_id -> weight, sums to 1.0
    as_of: float
    feed_hash: str                   # provenance hash of the ingested feeds
    stale_feeds: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)  # risk-excluded protocol ids
    alert: str = ""


def _risk_of(protocol_id: str) -> float:
    if protocol_id == CASH_ID:
        return 0.0
    spec = PROTOCOL_BY_ID.get(protocol_id)
    if spec is None:
        raise KeyError(f"unknown protocol_id: {protocol_id}")
    return spec.risk_score


def _feed_hash(signals: List[FeedSignal]) -> str:
    payload = sorted(
        (s.protocol_id, round(s.toa_score, 9), round(s.ts, 3)) for s in signals
    )
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


class Ingestor:
    """Stateful ingestor: remembers last-good weights for HOLD behavior."""

    def __init__(self) -> None:
        self.last_good_weights: Dict[str, float] = {CASH_ID: 1.0}
        self.last_good_hash: str = ""
        self.last_good_as_of: float = 0.0

    def ingest(
        self,
        signals: List[FeedSignal],
        now: float,
        min_cash_weight: float = 0.0,
    ) -> IngestResult:
        stale = [
            s.protocol_id
            for s in signals
            if now - s.ts > PARAMS["feed_staleness_s"]
        ]
        if stale:
            return IngestResult(
                status=STATUS_HOLD,
                weights=dict(self.last_good_weights),
                as_of=self.last_good_as_of,
                feed_hash=self.last_good_hash,
                stale_feeds=sorted(stale),
                alert=(
                    f"stale weight feed ({len(stale)} stale): HOLD — no rebalance, "
                    "serving last-good weights"
                ),
            )

        raw: Dict[str, float] = {}
        excluded: List[str] = []
        for s in signals:
            if s.protocol_id == "P26_DEFI_OS":
                # P26 is the meta ranker, never an allocation target.
                continue
            try:
                risk = _risk_of(s.protocol_id)
            except KeyError:
                excluded.append(s.protocol_id)
                continue
            if risk > PARAMS["exclusion_risk"]:
                excluded.append(s.protocol_id)
                continue
            val = max(float(s.toa_score), 0.0) * (1.0 - risk)
            if val > 0:
                raw[s.protocol_id] = raw.get(s.protocol_id, 0.0) + val

        total = sum(raw.values())
        weights: Dict[str, float]
        if total <= 0:
            weights = {CASH_ID: 1.0}
        else:
            weights = {pid: v / total for pid, v in raw.items()}

        if min_cash_weight > 0:
            # Reserve a minimum cash sleeve by scaling signal weights down.
            keep = 1.0 - min_cash_weight
            weights = {pid: w * keep for pid, w in weights.items()}
            weights[CASH_ID] = weights.get(CASH_ID, 0.0) + min_cash_weight

        # Exact normalization: fix float dust on the largest weight.
        wsum = sum(weights.values())
        if wsum > 0:
            biggest = max(weights, key=weights.get)
            weights[biggest] += 1.0 - wsum

        self.last_good_weights = dict(weights)
        self.last_good_hash = _feed_hash(signals)
        self.last_good_as_of = now
        return IngestResult(
            status=STATUS_OK,
            weights=weights,
            as_of=now,
            feed_hash=self.last_good_hash,
            stale_feeds=[],
            excluded=sorted(set(excluded)),
        )
