"""Trading and prediction market agents."""

from .openclaw_agent import OpenClawTradingAgent, PositionManager, TradeSignal
from .polymarket_agent import PolymarketAgent

__all__ = [
    'OpenClawTradingAgent',
    'PolymarketAgent',
    'PositionManager',
    'TradeSignal',
]
