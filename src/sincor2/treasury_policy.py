#!/usr/bin/env python3
"""
SINCOR Treasury Policy
Hard rule: Always convert AXM & SINC to USDC or WETH before treasury deposit.
Exception: Active trading wallets (Polyclaw, TOA-44, OpenClaw, etc.) may syphon profits in native tokens at defined intervals/thresholds.

This module centralizes the policy so every revenue path respects it.
"""

import os
from typing import List, Optional, Tuple
from decimal import Decimal

# Config from environment
TREASURY_CONVERSION_ENABLED = os.getenv("TREASURY_CONVERSION_ENABLED", "true").lower() == "true"
TREASURY_TARGET_ASSET = os.getenv("TREASURY_TARGET_ASSET", "USDC")  # or WETH
TREASURY_CONVERSION_SLIPPAGE_BPS = int(os.getenv("TREASURY_CONVERSION_SLIPPAGE_BPS", "50"))

TRADING_WALLET_ADDRESSES = [
    addr.strip() for addr in os.getenv("TRADING_WALLET_ADDRESSES", "").split(",") if addr.strip()
]
SYPHON_PROFIT_INTERVAL_TRADES = int(os.getenv("SYPHON_PROFIT_INTERVAL_TRADES", "50"))
SYPHON_PROFIT_THRESHOLD_USD = float(os.getenv("SYPHON_PROFIT_THRESHOLD_USD", "2500"))


class TreasuryPolicy:
    """Enforces treasury conversion rule with trading wallet exception."""

    def __init__(self):
        self.enabled = TREASURY_CONVERSION_ENABLED
        self.target_asset = TREASURY_TARGET_ASSET
        self.slippage_bps = TREASURY_CONVERSION_SLIPPAGE_BPS
        self.trading_wallets = set(TRADING_WALLET_ADDRESSES)
        self.syphon_interval = SYPHON_PROFIT_INTERVAL_TRADES
        self.syphon_threshold_usd = SYPHON_PROFIT_THRESHOLD_USD

        self.trade_count = 0  # Simple in-memory counter; persist in production
        self.native_profit_accumulated = 0.0

    def should_convert_before_treasury(self, from_token: str, receiving_wallet: str) -> bool:
        """
        Core rule: Convert AXM/SINC to target asset before treasury deposit
        unless the receiving wallet is an active trading wallet.
        """
        if not self.enabled:
            return False
        if from_token.upper() not in ("AXM", "AXIOM", "SINC"):
            return False
        if receiving_wallet in self.trading_wallets:
            return False  # Exception: let trading wallets keep/syphon native
        return True

    def should_syphon_native_profit(self, wallet_address: str, current_profit_usd: float) -> bool:
        """
        Allow active trading wallets to syphon/keep native token profits
        at defined intervals or thresholds.
        """
        if wallet_address not in self.trading_wallets:
            return False

        self.trade_count += 1
        self.native_profit_accumulated += current_profit_usd

        if self.trade_count >= self.syphon_interval:
            self.trade_count = 0
            return True

        if self.native_profit_accumulated >= self.syphon_threshold_usd:
            self.native_profit_accumulated = 0.0
            return True

        return False

    def get_conversion_params(self) -> dict:
        return {
            "target_asset": self.target_asset,
            "max_slippage_bps": self.slippage_bps,
            "via": os.getenv("TREASURY_CONVERT_VIA", "univ4|intent")
        }


# Singleton for easy import across the stack
treasury_policy = TreasuryPolicy()


def convert_before_treasury_if_needed(
    amount: float,
    from_token: str,
    receiving_wallet: str
) -> Tuple[float, str, bool]:
    """
    Helper used by revenue paths.
    Returns (adjusted_amount, target_asset, conversion_performed)
    """
    policy = treasury_policy
    if policy.should_convert_before_treasury(from_token, receiving_wallet):
        # In production: call actual swap (Uniswap V4, intent solver, etc.)
        # For now we just signal that conversion should happen
        return amount, policy.target_asset, True
    return amount, from_token, False
