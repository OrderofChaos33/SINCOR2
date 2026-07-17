from __future__ import annotations

"""Treasury and liquidity monitoring utilities."""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List

TREASURY_ADDRESS = os.getenv('TREASURY_ADDRESS', '0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac')

# Health thresholds — operators can tune these via environment variables.
_HEALTHY_AXIOM_FLOOR = Decimal(os.getenv('LIQUIDITY_HEALTHY_AXIOM', '10000'))
_HEALTHY_SINC_FLOOR  = Decimal(os.getenv('LIQUIDITY_HEALTHY_SINC',  '5000'))
_WATCH_AXIOM_FLOOR   = Decimal(os.getenv('LIQUIDITY_WATCH_AXIOM',   '1000'))


@dataclass
class LiquiditySnapshot:
    """Represents token balances and routing events at a point in time."""

    token_symbol: str
    balance: Decimal
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LiquidityManager:
    """Tracks treasury balances and health indicators for liquidity operations."""

    def __init__(self) -> None:
        self.balances: Dict[str, Decimal] = {
            'SINC': Decimal('0'),
            'AXIOM': Decimal('0'),
            'USD': Decimal('0'),
        }
        self.routing_events: List[Dict[str, str]] = []

    def get_treasury_balance(self, token_symbol: str = 'AXIOM') -> Dict[str, str]:
        """Return the current treasury balance for a token."""
        balance = self.balances.get(token_symbol.upper(), Decimal('0'))
        return {
            'treasury_address': TREASURY_ADDRESS,
            'token_symbol': token_symbol.upper(),
            'balance': str(balance.quantize(Decimal('0.0001'))),
        }

    def route_fees_to_treasury(self, amount: Decimal, token_symbol: str, source: str) -> Dict[str, str]:
        """Record a fee-routing event and update treasury balances."""
        token = token_symbol.upper()
        self.balances[token] = self.balances.get(token, Decimal('0')) + amount
        event = {
            'source': source,
            'token_symbol': token,
            'amount': str(amount.quantize(Decimal('0.0001'))),
            'treasury_address': TREASURY_ADDRESS,
            'routed_at': datetime.now(timezone.utc).isoformat(),
        }
        self.routing_events.append(event)
        return event

    def monitor_liquidity_health(self) -> Dict[str, object]:
        """Assess liquidity posture from treasury balances."""
        axiom_balance = self.balances.get('AXIOM', Decimal('0'))
        sinc_balance = self.balances.get('SINC', Decimal('0'))
        if axiom_balance > _HEALTHY_AXIOM_FLOOR and sinc_balance > _HEALTHY_SINC_FLOOR:
            status = 'healthy'
        elif axiom_balance > _WATCH_AXIOM_FLOOR:
            status = 'watch'
        else:
            status = 'critical'
        return {
            'status': status,
            'balances': {token: str(balance.quantize(Decimal('0.0001'))) for token, balance in self.balances.items()},
            'routing_event_count': len(self.routing_events),
        }

    def calculate_runway(self, monthly_burn_usd: Decimal) -> Dict[str, str]:
        """Estimate runway in months using the treasury USD-equivalent balance."""
        usd_balance = self.balances.get('USD', Decimal('0'))
        months = Decimal('0') if monthly_burn_usd <= 0 else (usd_balance / monthly_burn_usd)
        return {
            'monthly_burn_usd': str(monthly_burn_usd.quantize(Decimal('0.01'))),
            'treasury_usd_balance': str(usd_balance.quantize(Decimal('0.01'))),
            'estimated_runway_months': str(months.quantize(Decimal('0.01'))),
        }
