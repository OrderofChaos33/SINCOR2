# Polyclaw Trader - Live Cash Setup & Safe Mobile Operation Guide (June 2026)

**Direct Answer**: Yes, we can get polyclaw executing real trades with live USDC on Polygon/Polymarket. But because you are on mobile only and away from home, we do this with extreme caution: tiny position sizes, confirmation gates, full Telegram alerts, and a kill switch. No sugarcoating: live trading bots on mobile without constant monitoring is how people lose money fast. We minimize that.

## Prerequisites
- OpenClaw running with Telegram working (see telegram_openclaw_setup.md - do this first).
- A dedicated Polygon wallet (fresh recommended) with **small amount of USDC** (start with $20-100 USDC total exposure).
- Private key for that wallet (you will put it in env - treat as nuclear secret).
- polyclaw skill installed in the relevant agent (PolyclawTrader recommended).

## Step 1: Fund & Prepare Wallet (Mobile Friendly)
1. Use MetaMask mobile, Trust Wallet, or Polygon official wallet.
2. Create new wallet or use existing with low balance.
3. Bridge or buy small USDC on Polygon (use official bridge or CEX withdraw to Polygon address).
4. **Copy the PRIVATE KEY** (0x... format). Never share. For bot, we put in env var only.

**Risk Note**: Private key in env means if server compromised, funds gone. Use dedicated low-balance wallet. Consider multi-sig or future MPC for production, but for now small size.

## Step 2: Configure Polyclaw for Live Execution

In your OpenClaw environment (SSH into server or wherever gateway runs):

```bash
# Set the private key (one time)

export POLYCLAW_PRIVATE_KEY="0x_YOUR_PRIVATE_KEY_HERE"
# Make persistent: add to ~/.bashrc, ~/.zshrc, or OpenClaw env file, or systemd service
echo 'export POLYCLAW_PRIVATE_KEY="0x_..."' >> ~/.bashrc
source ~/.bashrc

# Verify polyclaw skill is available to your trading agent
# Chat to the agent via Telegram or TUI:
# "Do you have the polyclaw skill installed? List your trading tools."
```

If not installed:

```bash
clawhub install polyclaw
# or
cd ~/.openclaw/skills/polyclaw
uv sync
```

The skill auto-handles:
- Wallet connection
- One-time contract approvals (~6 Polygon txns, very cheap gas ~0.01 POL total)
- Market browsing (trending, search, full data)
- Buy YES/NO positions (split + CLOB execution)
- Position tracking with entry price, current price, P&L
- LLM-powered hedge discovery

## Step 3: First Live Test - Tiny Size, Confirmation On

Instruct your **PolyclawTrader** agent via Telegram (once bot working):

```
You are now in LIVE mode with real USDC. Rules (never break):
- Start with maximum $10-25 position size per trade.
- ALWAYS confirm with me via Telegram before executing any trade >$10.
- For every executed trade, immediately report: market, side (Yes/No), size, price, tx hash, new P&L.
- Send P&L summary every 4 hours or on significant move.
- If any error or anomaly, stop and alert me immediately.
- Maintain a simple risk log in your workspace.
- Default to conservative: only high-conviction markets with clear edge.
- Have a kill switch: if I say "EMERGENCY STOP" or "HALT ALL TRADING", immediately cease all activity and close or hold positions per my instruction.

Confirm you understand and are ready for first test trade.
```

Then test:
"Show me 3 trending crypto-related Polymarket markets with volume and odds."

Then: "Analyze [specific market] and recommend a small position with reasoning. Do not execute yet."

Once you approve: "Execute $15 Yes on [market id or slug]"

The agent + polyclaw skill will handle the rest via tools.

## Step 4: Make It Autonomous But Safe (While You're Mobile)

Add to SOUL.md or AGENTS.md of the PolyclawTrader workspace (edit via agent or directly):

```
- You have full access to polyclaw tools for live trading.
- You ONLY execute live trades after explicit user confirmation in this chat for sizes >$10.
- You proactively monitor open positions and alert on P&L changes >10% or unusual market moves.
- You can suggest hedges using the hedge discovery tool.
- You run in a loop: every 15-30 min check key markets, but only alert or act per rules.
- Safety first: If unsure, ask user. Never risk more than 5% of allocated capital on single idea without approval.
- Log every action with timestamp and tx details.
```

For full autonomy later (after you're back or more testing): Remove the "confirm every trade" rule and set strategy params, but keep Telegram alerts + daily summary + max daily loss limit coded in.

## Step 5: Monitoring & Kill Switches (Critical on Mobile)

- **Primary**: Your Telegram bot chat with the agent - all alerts flow here.
- **Secondary**: If possible, set up simple webhook or email/SMS on critical errors (OpenClaw supports extensions).
- **Kill**: Message "EMERGENCY STOP ALL TRADING AND CLOSE POSITIONS" or specific "close all" .
- In code/skill: You can ask the agent to implement a state flag "trading_enabled": false on command.
- Check positions manually via agent: "Show my current open positions and total P&L"

## Step 6: Scaling Up Safely

Once you see consistent small wins and are back at reliable computer:
- Increase size gradually ($50 -> $100 etc.).
- Add self-improving modules (win rate tracking, as you mentioned previously).
- Integrate with your SINCOR/DAE systems if desired (e.g. route profits to SINC liquidity).
- Consider forking polyclaw for custom risk rules, confirmation flows, or integration with LangGraph supervisor.

## Current Limitations & Recommendations

- **Mobile constraint**: No real-time chart watching or quick manual intervention. Rely 100% on agent alerts + your rules in SOUL.md.
- **Gas & slippage**: Small sizes on Polymarket are fine; larger need care.
- **No financial advice**: This is technical enablement only. You control the capital and rules. Past performance (your previous OpenClaw tests) != future.
- **Backup**: Export private key to password manager. Have a way to access wallet directly from mobile (MetaMask) to manually close if bot dies.
- **Testnet alternative**: Polymarket is primarily mainnet. For pure testing, use very small real money or check if there's a test deployment (rare for prediction markets).

## Immediate Action Plan (You on Mobile)

1. Create/fund the dedicated small USDC Polygon wallet (10 mins).
2. Get Telegram bot token(s) via @BotFather (2 mins).
3. Complete telegram_openclaw_setup.md (config + pair - 10-20 mins depending on SSH access).
4. Install polyclaw if missing.
5. Set POLYCLAW_PRIVATE_KEY.
6. Chat with the agent, set the strict rules above, do first $10-15 test trade together.
7. Report results here or in chat: I will iterate instantly (refine rules, add features, fix issues).

**We are doing this in the safest possible way given mobile constraints. Once Telegram + first live trade works, the "make my agents work for me" and revenue part accelerates dramatically.**

If your polyclaw is a custom fork or integrated differently in SINCOR2 (not the chainstacklabs one), paste the current code or describe the difference and I'll adapt this guide + provide patched version immediately.

Ready when you are. Drop the bot token or first error and we finish it.