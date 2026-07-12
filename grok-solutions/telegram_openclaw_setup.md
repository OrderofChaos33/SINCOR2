# Telegram Integration for OpenClaw Agents - Complete Production Setup (Updated July 2026 with User's Fixes)

**Status**: Updated with direct fixes for "Telegram doesn't work". This gets agents (including Polyclaw) talking from your phone. Combined with Gemini switch in the other guide, this unblocks revenue agents.

## Prerequisites (Mobile OK)
1. @BotFather: /newbot, copy token.
2. Your Telegram user ID (from @userinfobot).

## Core Config (Single or Multi-Agent)
Same as before (botToken, dmPolicy=pairing, allowlist your ID, restart gateway, pair/approve).

## Updated Troubleshooting for "Telegram Doesn't Work"
- No response to /start: 
  - `openclaw gateway restart`
  - Check `openclaw plugins list` — if telegram missing: `openclaw plugins install telegram`
  - Verify token in ~/.openclaw/openclaw.json or config set.
  - dmPolicy must be "pairing" not "open".
- Pairing code not showing: `openclaw pairing list telegram`
- Bot responds but wrong agent: Use multi-account bindings (separate tokens per agent like PolyclawTrader).
- Stuck on old Ollama context: Restart clears; Gemini switch (in polyclaw guide) helps consistency.
- Crestodian interference: Ignore it for Telegram — manual config above wins. Run `openclaw crestodian --fix channels` if needed.

## For PolyclawTrader Specifically
After Telegram live:
Chat the bot: "You have polyclaw skill. Confirm and list tools."
Then set rules for live trading as in the other guide.

**Execute this + the Gemini config from the polyclaw update. Once Telegram responds and agents use Gemini, first trades happen and balance starts moving. That's when optimism compounds.**

Report the exact error after trying (e.g. "bot not responding after restart") and we fix in one shot.