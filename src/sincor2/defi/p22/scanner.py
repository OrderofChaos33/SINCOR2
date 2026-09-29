"""P22 scanner: 300 s poll loop, quote normalization, TWAP overlay.

Each scan polls every allowlisted adapter for a spot quote, runs the
stable_only gate, and feeds accepted quotes into the rolling TWAP store. Any
venue failure (revert, stale quote, RPC error) degrades to ``venue_skipped``
— the position is unchanged and an alert is emitted. Zero uncaught
exceptions: every adapter call is wrapped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List

from . import PARAMS
from .gate import filter_quotes
from .interfaces import PoolQuote, StableVenueAdapter
from .twap import TwapQuote, TwapStore

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    ts: float
    accepted: List[PoolQuote] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)  # venue -> reason
    alerts: List[str] = field(default_factory=list)


class Scanner:
    """Poll adapters -> gate -> TWAP. Pure except for adapter quote() calls."""

    def __init__(
        self,
        adapters: List[StableVenueAdapter],
        twap_store: TwapStore | None = None,
    ) -> None:
        self.adapters = list(adapters)
        self.twap_store = twap_store or TwapStore()

    def scan(self, now: float) -> ScanResult:
        result = ScanResult(ts=now)
        raw: List[PoolQuote] = []
        for adapter in self.adapters:
            venue = getattr(adapter, "venue_id", "?")
            try:
                quote = adapter.quote()
            except Exception as exc:  # noqa: BLE001 — degrade, never crash
                result.skipped[venue] = f"quote failed: {exc}"
                result.alerts.append(f"venue_skipped: {venue}")
                logger.warning("venue_skipped %s: %s", venue, exc)
                continue
            # Stale quotes are discarded, never guessed.
            if now - quote.ts > PARAMS["quote_staleness_s"]:
                result.skipped[venue] = "stale quote"
                result.alerts.append(f"venue_stale: {venue}")
                continue
            raw.append(quote)

        accepted, rejected = filter_quotes(raw, entrypoint="scan")
        for event in rejected:
            result.skipped[event.venue_id] = "stable_only gate rejection"
            result.alerts.append(f"stable_only_violation: {event.venue_id}")

        for q in accepted:
            self.twap_store.add(
                q.venue_id, q.ts, q.net_apr, q.asset, q.depth_usd, q.is_morpho
            )
        result.accepted = accepted
        return result

    def twap_quotes(self, now: float) -> List[TwapQuote]:
        """Optimizer input: 4-hour TWAPs only, never spot quotes."""
        return self.twap_store.all_twaps(now)
