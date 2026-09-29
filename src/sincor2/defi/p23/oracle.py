"""P23 NFT pricing oracle: 7-day TWAP floor from >= 2 independent sources.

Defenses (the BendDAO lesson set):
  - cross-sourced floor: blended from >= 2 independent marketplace feeds
  - staleness breaker: no fresh feed update within 24 h -> deposits freeze,
    withdrawals stay open, last-good-price is served (never bricks)
  - per-update clamp: a single oracle update cannot move the published floor
    more than 25% (blunts manipulation spikes)
  - divergence watch: source spread > 15% alerts; > 30% auto-freezes deposits

Prices are integer USDC-wei (6 decimals) per NFT floor unit. TWAPs are exact
rational means floored once at the end.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Deque, Dict, List, Optional, Tuple

from . import PARAMS


@dataclass
class FeedPoint:
    source: str
    price_wei: int
    ts: float


@dataclass
class FloorReading:
    collection: str
    floor_wei: int
    sources: int
    deposits_frozen: bool
    reason: str
    ts: float


class OracleError(Exception):
    """Oracle invariant violation."""


class NftPricingOracle:
    def __init__(
        self,
        twap_s: Optional[float] = None,
        staleness_s: Optional[float] = None,
        max_move: Optional[float] = None,
        min_sources: Optional[int] = None,
    ) -> None:
        self.twap_s = twap_s if twap_s is not None else PARAMS["oracle_twap_s"]
        self.staleness_s = (staleness_s if staleness_s is not None
                            else PARAMS["oracle_staleness_s"])
        self.max_move = max_move if max_move is not None else PARAMS["oracle_max_move"]
        self.min_sources = (min_sources if min_sources is not None
                            else PARAMS["oracle_min_sources"])
        self._feeds: Dict[str, Dict[str, Deque[FeedPoint]]] = {}
        self._last_good: Dict[str, int] = {}
        self._frozen: Dict[str, bool] = {}

    # -- feeds ----------------------------------------------------------
    def submit_feed(self, collection: str, source: str,
                    price_wei: int, ts: Optional[float] = None) -> None:
        if price_wei <= 0:
            raise OracleError("feed price must be positive")
        ts = time.time() if ts is None else ts
        by_source = self._feeds.setdefault(collection, {})
        dq = by_source.setdefault(source, deque())
        dq.append(FeedPoint(source, price_wei, ts))
        cutoff = ts - self.twap_s
        while dq and dq[0].ts < cutoff:
            dq.popleft()

    def _source_twap(self, dq: Deque[FeedPoint], now: float) -> Optional[Fraction]:
        pts = [p for p in dq if p.ts >= now - self.twap_s]
        if not pts:
            return None
        return sum((Fraction(p.price_wei) for p in pts), Fraction(0)) / len(pts)

    def _fresh_sources(self, collection: str, now: float) -> List[Tuple[str, Fraction]]:
        out = []
        for source, dq in self._feeds.get(collection, {}).items():
            if not dq:
                continue
            newest = max(p.ts for p in dq)
            if now - newest > self.staleness_s:
                continue
            tw = self._source_twap(dq, now)
            if tw is not None:
                out.append((source, tw))
        return out

    # -- reads ----------------------------------------------------------
    def floor(self, collection: str, now: Optional[float] = None) -> FloorReading:
        now = time.time() if now is None else now
        fresh = self._fresh_sources(collection, now)
        if len(fresh) < self.min_sources:
            # Staleness breaker: freeze deposits, serve last-good-price.
            self._frozen[collection] = True
            last = self._last_good.get(collection, 0)
            return FloorReading(collection, last, len(fresh), True,
                                "stale_or_single_source", now)
        twaps = [tw for _, tw in fresh]
        raw = sum(twaps, Fraction(0)) / len(twaps)
        # Divergence watch across sources.
        hi, lo = max(twaps), min(twaps)
        spread = float((hi - lo) / lo) if lo > 0 else 0.0
        frozen = False
        reason = "ok"
        if spread > 0.30:
            frozen, reason = True, f"source_spread_{spread:.2%}"
        elif spread > 0.15:
            reason = f"source_spread_alert_{spread:.2%}"
        # Per-update clamp against the last published floor.
        candidate = int(raw)  # floor once
        last = self._last_good.get(collection)
        if last and last > 0 and not frozen:
            cap_up = int(last * (1 + self.max_move))
            cap_down = int(last * (1 - self.max_move))
            if candidate > cap_up:
                candidate, reason = cap_up, "clamped_up_25pct"
            elif candidate < cap_down:
                candidate, reason = cap_down, "clamped_down_25pct"
        if not frozen:
            self._last_good[collection] = candidate
            self._frozen[collection] = False
        else:
            self._frozen[collection] = True
            candidate = self._last_good.get(collection, candidate)
        return FloorReading(collection, candidate, len(fresh), frozen, reason, now)

    def deposits_frozen(self, collection: str) -> bool:
        return self._frozen.get(collection, False)

    def last_good_price(self, collection: str) -> int:
        return self._last_good.get(collection, 0)
