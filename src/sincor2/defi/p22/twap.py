"""P22 TWAP store: the optimizer consumes 4-hour TWAPs, never spot quotes.

Utilization spikes can print a 12% supply APR for one block and collapse the
next; allocating on spot quotes would churn into spikes and burn gas. The
scanner keeps a rolling TWAP per venue; quotes with no TWAP history yet are
held out of the optimizer until they have at least one fresh sample.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

from . import PARAMS


@dataclass
class TwapQuote:
    venue_id: str
    asset: str
    net_apr_twap: float
    depth_usd: float
    is_morpho: bool
    samples: int
    newest_ts: float


class TwapStore:
    """Rolling per-venue TWAP of quoted net APR over twap_window_s."""

    def __init__(self, window_s: Optional[float] = None) -> None:
        self.window_s = window_s or PARAMS["twap_window_s"]
        self._samples: Dict[str, Deque[Tuple[float, float]]] = {}
        self._meta: Dict[str, dict] = {}

    def add(
        self,
        venue_id: str,
        ts: float,
        net_apr: float,
        asset: str,
        depth_usd: float,
        is_morpho: bool,
    ) -> None:
        dq = self._samples.setdefault(venue_id, deque())
        dq.append((ts, net_apr))
        cutoff = ts - self.window_s
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        self._meta[venue_id] = {
            "asset": asset, "depth_usd": depth_usd, "is_morpho": is_morpho,
        }

    def twap(self, venue_id: str, now: float) -> Optional[TwapQuote]:
        dq = self._samples.get(venue_id)
        if not dq:
            return None
        cutoff = now - self.window_s
        samples = [(ts, apr) for ts, apr in dq if ts >= cutoff]
        if not samples:
            return None
        # Time-weighted mean over the window.
        total_w = 0.0
        total_v = 0.0
        prev_ts = max(cutoff, samples[0][0])
        for ts, apr in samples:
            w = ts - prev_ts
            total_v += apr * w
            total_w += w
            prev_ts = ts
        # Tail: last sample holds until `now`.
        tail = now - prev_ts
        if tail > 0:
            total_v += samples[-1][1] * tail
            total_w += tail
        meta = self._meta[venue_id]
        return TwapQuote(
            venue_id=venue_id,
            asset=meta["asset"],
            net_apr_twap=(total_v / total_w) if total_w > 0 else samples[-1][1],
            depth_usd=meta["depth_usd"],
            is_morpho=meta["is_morpho"],
            samples=len(samples),
            newest_ts=samples[-1][0],
        )

    def all_twaps(self, now: float) -> List[TwapQuote]:
        out = []
        for venue_id in self._samples:
            q = self.twap(venue_id, now)
            if q is not None:
                out.append(q)
        return out
