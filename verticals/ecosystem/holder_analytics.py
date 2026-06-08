from __future__ import annotations

"""Holder concentration and retention analytics for the SINC ecosystem."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List


class HolderTier(Enum):
    """Balance tiers based on circulating supply ownership."""

    WHALE = 'whale'
    DOLPHIN = 'dolphin'
    FISH = 'fish'
    SHRIMP = 'shrimp'


@dataclass
class HolderSnapshot:
    """A point-in-time snapshot of the holder base."""

    snapshot_id: str
    timestamp: str
    total_holders: int
    balances: Dict[str, float]
    total_supply: float
    staking_participation_rate: float
    new_holders_24h: int


@dataclass
class HolderMetrics:
    """Core health metrics for token holder distribution."""

    concentration_risk: float
    whale_count: int
    dolphin_count: int
    fish_count: int
    shrimp_count: int
    average_hold_days: float
    retention_rate_90d: float
    wallet_to_exchange_ratio: float
    active_holder_pct: float


class HolderAnalyticsEngine:
    """Computes token holder concentration, retention, and health indicators."""

    def classify_holder(self, balance: float, total_supply: float) -> HolderTier:
        """Classify a wallet by share of total supply."""

        if total_supply <= 0:
            return HolderTier.SHRIMP
        supply_share = balance / total_supply
        if supply_share > 0.01:
            return HolderTier.WHALE
        if supply_share >= 0.001:
            return HolderTier.DOLPHIN
        if supply_share >= 0.0001:
            return HolderTier.FISH
        return HolderTier.SHRIMP

    def compute_metrics(self, snapshot: HolderSnapshot) -> HolderMetrics:
        """Compute point-in-time holder metrics from a snapshot."""

        positive_balances = [max(balance, 0.0) for balance in snapshot.balances.values()]
        total_balance = sum(positive_balances)
        holder_count = max(snapshot.total_holders, len(snapshot.balances), 1)
        concentration_risk = 0.0
        if total_balance > 0:
            concentration_risk = sum((balance / total_balance) ** 2 for balance in positive_balances if balance > 0)

        tier_counts = {
            HolderTier.WHALE: 0,
            HolderTier.DOLPHIN: 0,
            HolderTier.FISH: 0,
            HolderTier.SHRIMP: 0,
        }
        for balance in positive_balances:
            tier_counts[self.classify_holder(balance, snapshot.total_supply)] += 1

        active_holders = sum(1 for balance in positive_balances if balance > 0)
        new_holder_ratio = min(snapshot.new_holders_24h / holder_count, 1.0)
        retention_rate_90d = max(0.0, min(1.0, (1.0 - new_holder_ratio) * (0.55 + (0.45 * snapshot.staking_participation_rate))))
        average_hold_days = max(1.0, round((30 + (snapshot.staking_participation_rate * 180)) * (1.0 - (new_holder_ratio * 0.5)), 2))
        wallet_to_exchange_ratio = round((holder_count - tier_counts[HolderTier.WHALE]) / max(tier_counts[HolderTier.WHALE], 1), 4)
        active_holder_pct = round(active_holders / holder_count, 4)
        return HolderMetrics(
            concentration_risk=round(concentration_risk, 4),
            whale_count=tier_counts[HolderTier.WHALE],
            dolphin_count=tier_counts[HolderTier.DOLPHIN],
            fish_count=tier_counts[HolderTier.FISH],
            shrimp_count=tier_counts[HolderTier.SHRIMP],
            average_hold_days=average_hold_days,
            retention_rate_90d=round(retention_rate_90d, 4),
            wallet_to_exchange_ratio=wallet_to_exchange_ratio,
            active_holder_pct=active_holder_pct,
        )

    def compute_retention_rate(self, snapshots: List[HolderSnapshot]) -> float:
        """Compute address retention between the earliest and latest snapshots."""

        if len(snapshots) < 2:
            return 1.0
        earliest_addresses = set(snapshots[0].balances)
        latest_addresses = set(snapshots[-1].balances)
        if not latest_addresses:
            return 1.0
        retained_addresses = latest_addresses & earliest_addresses
        return round(len(retained_addresses) / len(latest_addresses), 4)

    def detect_distribution_shift(self, prev: HolderSnapshot, curr: HolderSnapshot, threshold: float = 0.05) -> bool:
        """Detect material concentration changes between two snapshots."""

        prev_hhi = self.compute_metrics(prev).concentration_risk
        curr_hhi = self.compute_metrics(curr).concentration_risk
        return abs(curr_hhi - prev_hhi) > threshold

    def generate_health_report(self, snapshot: HolderSnapshot, snapshots_history: List[HolderSnapshot]) -> Dict[str, object]:
        """Generate a consolidated holder health report."""

        metrics = self.compute_metrics(snapshot)
        combined_history = list(snapshots_history)
        if not combined_history or combined_history[-1].snapshot_id != snapshot.snapshot_id:
            combined_history.append(snapshot)
        distribution_shift_alert = False
        if snapshots_history:
            distribution_shift_alert = self.detect_distribution_shift(snapshots_history[-1], snapshot)
        return {
            'snapshot_id': snapshot.snapshot_id,
            'metrics': asdict(metrics),
            'retention_rate': self.compute_retention_rate(combined_history),
            'distribution_shift_alert': distribution_shift_alert,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }


__all__ = ['HolderAnalyticsEngine', 'HolderMetrics', 'HolderSnapshot', 'HolderTier']
