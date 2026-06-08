from __future__ import annotations

"""Liquidity and market microstructure monitoring for the SINC ecosystem."""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class VenueType(Enum):
    """Supported venue types for SINC liquidity analysis."""

    DEX = 'dex'
    CEX = 'cex'
    AMM_V2 = 'amm_v2'
    AMM_V3 = 'amm_v3'


@dataclass
class OrderBookLevel:
    """A single order book price level."""

    price: float
    size: float


@dataclass
class LiquidityVenue:
    """A venue-specific liquidity snapshot."""

    venue_id: str
    name: str
    venue_type: VenueType
    token_pair: str
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    volume_24h: float
    lp_reward_emission: float
    last_updated: str


@dataclass
class LiquiditySnapshot:
    """Aggregated liquidity state across venues."""

    snapshot_id: str
    timestamp: str
    venues: List[LiquidityVenue]
    total_tvl: float


class LiquidityMonitor:
    """Evaluates SINC liquidity depth, fragmentation, and emissions."""

    def compute_slippage(self, venue: LiquidityVenue, order_size: float, side: str = 'buy') -> float:
        """Walk the order book to estimate execution slippage."""

        if order_size <= 0:
            return 0.0
        if side == 'sell':
            book = sorted(venue.bids, key=lambda level: level.price, reverse=True)
        else:
            book = sorted(venue.asks, key=lambda level: level.price)
        if not book:
            return 0.0

        remaining = order_size
        notional = 0.0
        for level in book:
            if remaining <= 0:
                break
            fill_size = min(level.size, remaining)
            notional += fill_size * level.price
            remaining -= fill_size
        if remaining > 0:
            return 1.0

        best_price = book[0].price
        if best_price <= 0:
            return 0.0
        vwap = notional / order_size
        if side == 'sell':
            return round(max(1 - (vwap / best_price), 0.0), 6)
        return round(max((vwap / best_price) - 1, 0.0), 6)

    def compute_spread(self, venue: LiquidityVenue) -> float:
        """Compute the relative spread between the best bid and best ask."""

        if not venue.bids or not venue.asks:
            return 0.0
        best_bid = max(level.price for level in venue.bids)
        best_ask = min(level.price for level in venue.asks)
        mid_price = (best_bid + best_ask) / 2
        if mid_price <= 0:
            return 0.0
        return round((best_ask - best_bid) / mid_price, 6)

    def assess_fragmentation(self, venues: List[LiquidityVenue]) -> Dict[str, object]:
        """Assess venue concentration and liquidity fragmentation."""

        tvl_by_venue = {venue.venue_id: self._estimate_venue_tvl(venue) for venue in venues}
        total_tvl = sum(tvl_by_venue.values())
        if total_tvl <= 0:
            return {
                'total_tvl': 0.0,
                'tvl_share_by_venue': {},
                'top_venue': None,
                'concentration_risk': 0.0,
                'fragmentation_alert': False,
            }

        shares = {venue_id: value / total_tvl for venue_id, value in tvl_by_venue.items()}
        top_venue = max(shares, key=shares.get)
        concentration_risk = sum(share ** 2 for share in shares.values())
        fragmentation_alert = any(share > 0.7 for share in shares.values())
        return {
            'total_tvl': round(total_tvl, 4),
            'tvl_share_by_venue': {venue_id: round(share, 4) for venue_id, share in shares.items()},
            'top_venue': top_venue,
            'concentration_risk': round(concentration_risk, 4),
            'fragmentation_alert': fragmentation_alert,
        }

    def optimize_pool_emissions(self, venues: List[LiquidityVenue], target_competitor_yield: float) -> List[Dict[str, float | str]]:
        """Recommend LP reward changes to match a target market yield."""

        recommendations = []
        for venue in venues:
            venue_tvl = max(self._estimate_venue_tvl(venue), 1.0)
            current_yield = venue.lp_reward_emission / venue_tvl
            recommended_emission = target_competitor_yield * venue_tvl
            recommendations.append({
                'venue_id': venue.venue_id,
                'current_yield': round(current_yield, 6),
                'recommended_emission': round(recommended_emission, 4),
                'delta': round(recommended_emission - venue.lp_reward_emission, 4),
            })
        return recommendations

    def _estimate_venue_tvl(self, venue: LiquidityVenue) -> float:
        """Estimate venue TVL from visible book depth and fallback volume proxies."""

        bid_depth = sum(level.price * level.size for level in venue.bids)
        ask_depth = sum(level.price * level.size for level in venue.asks)
        visible_depth = (bid_depth + ask_depth) / 2
        return max(visible_depth, venue.volume_24h * 0.05, 0.0)


__all__ = ['LiquidityMonitor', 'LiquiditySnapshot', 'LiquidityVenue', 'OrderBookLevel', 'VenueType']
