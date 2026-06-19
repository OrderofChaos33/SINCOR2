# SINCOR Strategic Focus — June 2026

**Current Mandate:** Go all-in on highest-ROI, highest-value-creation surfaces while everything trends toward autonomous operation with minimal ongoing input.

## Active & Accelerated
- **SADAS** (Alternative Derivative Alpha Swarm): Pre-IPO intel, yield arb (SINAX), binary primitives (TOA-44), Risk-Compliance-as-a-Service B2B
- **Trading Edge**: Polyclaw / OpenClaw / TOA-44 with native token syphoning + strict treasury conversion rule
- **Outreach + Lead Gen Engine**
- **Content / SEO Engine** (autonomous)
- **Core A2A Marketplace + Swarm Coordination + High-Margin Verticals** (healthcare RCM, compliance, trading signals)
- **Treasury Discipline**: AXM/SINC always converted to USDC/WETH before treasury deposit (except designated trading wallets)

## Treasury Policy Usage Examples

The treasury policy is enforced via `src/sincor2/treasury_policy.py`.

### Convert before Treasury Deposit (Normal Path)
```python
from src.sincor2.treasury_policy import convert_before_treasury_if_needed

amount, target_asset, converted = convert_before_treasury_if_needed(
    amount=1250.0,
    from_token="AXM",
    receiving_wallet="TREASURY"
)
# Result: (1250.0, 'USDC', True)
```

### Trading Wallet Exception (Keep Native Tokens)
```python
# For Polyclaw, TOA-44 execution wallets, etc.
amount, target_asset, converted = convert_before_treasury_if_needed(
    amount=980.0,
    from_token="SINC",
    receiving_wallet="0xYourTradingWalletAddress"
)
# Result: (980.0, 'SINC', False)  → No conversion
```

### Check if Trading Wallet Should Syphon Profits
```python
should_syphon = treasury_policy.should_syphon_native_profit(
    wallet_address="0xYourTradingWalletAddress",
    current_profit_usd=2700
)
# Returns True if interval or threshold is met
```

## Paused (Lower Immediate ROI / Higher Maintenance)
- WebBuilder vertical and similar items (re-enable only when fully autonomous)

## Key Principles
- Always growing
- Make the smartest choices
- Stay awake to the world (opportunities + risks)
- Maximize the most and best
- Everything must trend toward running with less input from the founder

## Media & Institutional Assets
Ready-to-use institutional media pack for SADAS Enterprise module ($2,997/mo):
- `media/sadas/Press_Release_SADAS.md`
- `media/sadas/Executive_One_Pager_SADAS.md`
- `media/sadas/Social_Assets_SADAS.md`

These assets position SADAS for venture funds, family offices, and crypto hedge funds.

**Last Updated:** June 18, 2026