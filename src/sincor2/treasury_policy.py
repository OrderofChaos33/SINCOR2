#!/usr/bin/env python3
"""
SINCOR Treasury Policy

Hard Rule:
    Always convert AXM & SINC to USDC or WETH before depositing to the main treasury.

Exception:
    Active trading wallets (Polyclaw, TOA-44 execution, OpenClaw, etc.) may keep/syphon
    profits in native AXM/SINC at defined intervals or profit thresholds.

This module centralizes the logic so every revenue path respects the policy.
"""

import os
from typing import NamedTuple


# ====================== CONFIGURATION ======================
TREASURY_CONVERSION_ENABLED = os.getenv("TREASURY_CONVERSION_ENABLED", "true").lower() == "true"
TREASURY_TARGET_ASSET = os.getenv("TREASURY_TARGET_ASSET", "USDC")  # USDC or WETH
TREASURY_CONVERSION_SLIPPAGE_BPS = int(os.getenv("TREASURY_CONVERSION_SLIPPAGE_BPS", "50"))

TRADING_WALLET_ADDRESSES = set(
    addr.strip() for addr in os.getenv("TRADING_WALLET_ADDRESSES", "").split(",") if addr.strip()
)
SYPHON_PROFIT_INTERVAL_TRADES = int(os.getenv("SYPHON_PROFIT_INTERVAL_TRADES", "50"))
SYPHON_PROFIT_THRESHOLD_USD = float(os.getenv("SYPHON_PROFIT_THRESHOLD_USD", "2500"))


class TreasuryPolicy:
    """
    Enforces the treasury conversion rule with trading wallet exception.

    Usage Examples:
        # Normal revenue path (treasury deposit)
        decision = convert_before_treasury_if_needed(
            amount=1250.0,
            from_token="AXM",
            receiving_wallet="TREASURY"
        )
        # decision.converted is False until an executor is armed (item 32);
        # decision.reason explains why.  Never treat converted=True as proof
        # that funds moved — verify the ConversionLedger / onchain receipts.

        # Trading wallet syphon check
        if treasury_policy.should_syphon_native_profit(wallet_address, profit_usd=3200):
            # Allow keeping native tokens
            pass
    """

    def __init__(self):
        self.enabled = TREASURY_CONVERSION_ENABLED
        self.target_asset = TREASURY_TARGET_ASSET
        self.slippage_bps = TREASURY_CONVERSION_SLIPPAGE_BPS
        self.trading_wallets = TRADING_WALLET_ADDRESSES
        self.syphon_interval = SYPHON_PROFIT_INTERVAL_TRADES
        self.syphon_threshold = SYPHON_PROFIT_THRESHOLD_USD

        # Simple in-memory counters (use persistent storage in production)
        self.trade_count = 0
        self.native_profit = 0.0

    def should_convert_before_treasury(self, from_token: str, receiving_wallet: str) -> bool:
        """
        Core rule: Convert AXM/SINC before sending to treasury,
        unless the receiving wallet is an approved trading wallet.
        """
        if not self.enabled:
            return False
        if from_token.upper() not in ("AXM", "AXIOM", "SINC"):
            return False
        if receiving_wallet in self.trading_wallets:
            return False  # Exception: trading wallets can keep native tokens
        return True

    def should_syphon_native_profit(self, wallet_address: str, current_profit_usd: float) -> bool:
        """
        Allow active trading wallets to keep/syphon native token profits
        at defined intervals or when profit threshold is reached.
        """
        if wallet_address not in self.trading_wallets:
            return False

        self.trade_count += 1
        self.native_profit += current_profit_usd

        if self.trade_count >= self.syphon_interval:
            self.trade_count = 0
            return True

        if self.native_profit >= self.syphon_threshold:
            self.native_profit = 0.0
            return True

        return False

    def get_conversion_params(self) -> dict:
        return {
            "target_asset": self.target_asset,
            "max_slippage_bps": self.slippage_bps,
            "via": os.getenv("TREASURY_CONVERT_VIA", "univ4|intent")
        }


class ConversionDecision(NamedTuple):
    """
    Fail-closed conversion signal (build-out item 34, 2026-09-30).

    ``converted`` is True ONLY when a real conversion executor is armed and
    has performed (or is responsible for) the swap.  Until then it is False
    and ``reason`` explains why.  No caller may treat ``converted=True`` as
    proof that funds moved — verify the ConversionLedger / onchain receipts.
    """
    amount: float
    target_asset: str
    converted: bool
    reason: str


def is_conversion_executor_armed() -> bool:
    """
    Whether a real fee-conversion executor is armed and able to perform the
    swap.  Fail-closed default False.

    This flag may ONLY be set true by the item-32 arming ceremony
    (docs/ops/FEE_EXECUTOR_RUNBOOK.md): pinned pool, fork-simulated
    calldata, forwarder key in the Secure Vault.  No code path in this repo
    sets it; until the ceremony completes it stays False.
    """
    return os.getenv("SINCOR_FEE_EXECUTOR_ARMED", "false").lower() == "true"


# Singleton instance for easy import across the codebase
treasury_policy = TreasuryPolicy()


def convert_before_treasury_if_needed(
    amount: float,
    from_token: str,
    receiving_wallet: str = "TREASURY"
) -> ConversionDecision:
    """
    Helper used by revenue paths (SADAS, A2A tasks, trading profits, etc).

    FAIL-CLOSED (2026-09-30, build-out item 34): this returns
    ``converted=False`` with a ``reason`` until a conversion executor is
    actually armed (item 32).  No code path may believe conversion happened
    when it didn't.

    Returns:
        ConversionDecision(amount, target_asset, converted, reason)
    """
    if not treasury_policy.should_convert_before_treasury(from_token, receiving_wallet):
        return ConversionDecision(
            amount=amount,
            target_asset=from_token,
            converted=False,
            reason="conversion not required by policy "
                   "(non-fee token or trading-wallet exception)",
        )
    if is_conversion_executor_armed():
        # The armed executor owns the swap; this signal alone never proves
        # funds moved — verify the ConversionLedger / onchain receipts.
        return ConversionDecision(
            amount=amount,
            target_asset=treasury_policy.target_asset,
            converted=True,
            reason="conversion executor is armed; conversion is the armed "
                   "executor's responsibility (verify onchain)",
        )
    return ConversionDecision(
        amount=amount,
        target_asset=from_token,
        converted=False,
        reason="conversion required by policy but the fee-conversion "
               "executor is disarmed (item 32 not complete); no conversion "
               "was performed — queue the obligation in the ConversionLedger",
    )


# ====================== USAGE EXAMPLES ======================
if __name__ == "__main__":
    print("=== Treasury Policy Examples ===\n")

    # Example 1: Normal SADAS / A2A revenue going to treasury
    print("1. Normal revenue to treasury (policy says convert, executor disarmed):")
    decision = convert_before_treasury_if_needed(1250.0, "AXM", "TREASURY")
    print(f"   Original: 1250 AXM | Target: {decision.target_asset} | "
          f"Converted: {decision.converted} | Reason: {decision.reason}\n")

    # Example 2: Trading wallet (Polyclaw / TOA-44) - should NOT convert
    print("2. Trading wallet profit (should keep native):")
    trading_wallet = list(TRADING_WALLET_ADDRESSES)[0] if TRADING_WALLET_ADDRESSES else "0xTradingWallet123"
    decision = convert_before_treasury_if_needed(980.0, "SINC", trading_wallet)
    print(f"   Original: 980 SINC | Target: {decision.target_asset} | "
          f"Converted: {decision.converted} | Reason: {decision.reason}\n")

    # Example 3: Syphon check for trading wallet
    print("3. Should trading wallet syphon native profit?")
    should_syphon = treasury_policy.should_syphon_native_profit(trading_wallet, current_profit_usd=2700)
    print(f"   Profit = $2700 | Should syphon: {should_syphon}\n")

    print("Policy configuration loaded from environment variables.")