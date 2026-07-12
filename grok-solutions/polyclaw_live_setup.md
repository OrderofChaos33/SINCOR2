# Polyclaw Trader - Live Cash Setup & Safe Mobile Operation Guide (Updated July 2026 for Gemini + Telegram Fixes)

**Direct Answer**: The blockers (stuck on Ollama, Gemini switch failing, Crestodian not helping, Telegram broken) are common OpenClaw config issues. We fix them here with explicit steps. Once running, Polyclaw can execute trades and contribute to balance changes. Your 20M liquid SINC + this = real path to revenue. No sugarcoating: trading bots carry risk — we keep sizes tiny and rules strict until you see positive balance movement.

## Quick Fix for Your Reported Issues (Do These First)

1. **Switch to Gemini Free Tier (Best Model)**:
   - Go to https://aistudio.google.com/app/apikey (free tier, generous limits for Gemini 1.5 Flash or Pro).
   - Create/copy API key (starts with AIza...).
   - In your OpenClaw env or ~/.openclaw/openclaw.json:
     ```json
     {
       "llm": {
         "provider": "google",
         "model": "gemini-1.5-flash"  // or gemini-1.5-pro for best free tier
         "apiKey": "YOUR_GEMINI_API_KEY_HERE"
       }
     }
     ```
   - Or via env: `export GOOGLE_API_KEY="YOUR_KEY"` and set provider in config.
   - Restart gateway: `openclaw gateway restart`
   - Test: Chat to agent "What model are you using? Switch to Gemini if not."
   - If Crestodian is blocking: Run `openclaw crestodian --fix` or `openclaw onboard` to reconfigure inference. It should detect Gemini key and switch. If still dumb/stuck on Ollama, manually edit the json above — Crestodian is just a helper, not magic.

2. **Fix Telegram to Talk to Agents**:
   - Follow the exact steps in telegram_openclaw_setup.md (updated below with troubleshooting).
   - Common fixes: Ensure dmPolicy=pairing, approve pairing code, use correct user ID allowlist, restart gateway.
   - Test bot: Send /start in Telegram. If no response, check `openclaw plugins list` and reinstall telegram plugin if missing.
   - For PolyclawTrader agent: Set separate bot token per agent as in the multi-agent config.

3. **Crestodian Dumb?**:
   - It's the OpenClaw setup/repair CLI (not SINCOR2 code). Run it explicitly for model fix:
     `openclaw crestodian --fix llm`
   - Or full: `openclaw doctor` then `openclaw crestodian`.
   - If it fails to switch: The json edit above bypasses it. Crestodian sometimes lags on new providers — manual config wins.

Once above done, Polyclaw should use Gemini and Telegram works. Then proceed to live small trades for balance movement.

## Prerequisites (Same as Before, Plus Gemini)
- OpenClaw with Gemini configured (above).
- Dedicated small USDC Polygon wallet.
- polyclaw skill installed.
- Telegram bot working (fix above).

## Step 1-6: Same as Previous (Tiny Sizes, Confirmation, etc.)
(Keep the risk rules, first test trade with confirmation, SOUL.md safety, kill switches. All unchanged — focus on getting Gemini + Telegram live first.)

## Updated Troubleshooting
- Ollama stuck: Explicitly set provider to "google" + key in config. Ollama is local/cloud fallback; Gemini overrides.
- Gemini free tier limits: Start small, monitor usage in AI Studio.
- Telegram no response: Gateway restart + pairing approve + allowlist your user ID.
- Crestodian not helping: Manual json edit + restart > relying on it.
- Balance not changing: Once agents run with Gemini, first trades will show P&L. Report here for iteration.

**This gets you unblocked. Execute the Gemini config and Telegram fix now (10-15 mins), test "What model?", then report back. When you see the first trade or balance tick, optimism kicks in — and we compound from there with the hook + agents.**

Ready. Drop the error after trying the Gemini json or bot token and we patch instantly.