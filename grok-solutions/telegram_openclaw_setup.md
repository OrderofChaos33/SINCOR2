# Telegram Integration for OpenClaw Agents - Complete Production Setup (June 2026)

**Status**: Ready to deploy. This is the exact process that works for talking to your agents (including trading agents with polyclaw) via Telegram from your phone while traveling.

## Prerequisites (Do These First on Mobile)
1. Open your Telegram app (mobile is fine).
2. Search for **@BotFather** (official, verified).
3. Send `/newbot`
4. Name: `SINCOR Agent` or `Court Agents` or per agent e.g. `Polyclaw Trader`
5. Username: `sincor_agent_bot` or unique (must end with _bot)
6. **COPY THE BOT TOKEN** exactly (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`). Save it securely (password manager or note).

If you want **multiple agents** (e.g. one for trading, one for SINCOR ops, one for awareness):
- Repeat for each: different bot, different token.
- Main agent can help configure the others.

## Step 1: Configure OpenClaw for Telegram (Single Agent - Fastest)

If you have OpenClaw installed and running (or gateway accessible via SSH/Termux/mobile SSH client):

```bash
# Set the token (replace with yours)
openclaw config set channels.telegram.botToken "YOUR_BOT_TOKEN_HERE"

# Or edit ~/.openclaw/openclaw.json directly for full control
```

Recommended full Telegram config (add or merge into your channels section):

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "YOUR_BOT_TOKEN_HERE",
      "dmPolicy": "pairing",
      "streamMode": "partial",
      "groups": {
        "*": {
          "requireMention": true,
          "allowlist": ["YOUR_TELEGRAM_USER_ID"]
        }
      },
      "allowlist": ["YOUR_TELEGRAM_USER_ID"]
    }
  }
}
```

**Get your Telegram user ID**:
- Message @userinfobot or @getmyid_bot in Telegram, or forward a message to @userinfobot.

## Step 2: Restart Gateway and Pair

```bash
openclaw gateway restart
# or if not running as service:
openclaw gateway
```

Then pair:

```bash
openclaw pairing list telegram
# Note the code or pending request
openclaw pairing approve telegram <CODE_FROM_ABOVE>
```

Send `/start` to your new bot in Telegram. It should respond and pair.

Test: Send "Hello, list my available skills" or "What tools do you have?"

## Step 3: For Multiple Agents (Recommended for Your Use Case)

Use the advanced multi-account setup so each agent has its own bot/personality/workspace.

Instruct your **main agent** (via existing interface or TUI):

"Create a new agent called 'PolyclawTrader' with its own Telegram bot. Use this token: [paste new token]. Give it the polyclaw skill and trading focus. Set up isolated workspace."

Or manually edit `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "list": [
      {
        "id": "main",
        "default": true,
        "name": "SINCOR Main",
        "workspace": "~/.openclaw/workspace"
      },
      {
        "id": "polyclaw",
        "name": "Polyclaw Trader",
        "workspace": "~/.openclaw/workspace-polyclaw"
      },
      {
        "id": "sincor-ops",
        "name": "SINCOR Ops",
        "workspace": "~/.openclaw/workspace-sincor"
      }
    ]
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "dmPolicy": "pairing",
      "streamMode": "partial",
      "accounts": {
        "default": { "botToken": "MAIN_TOKEN" },
        "polyclaw": { "botToken": "POLYCLAW_BOT_TOKEN" },
        "sincor-ops": { "botToken": "SINCOR_OPS_TOKEN" }
      }
    }
  },
  "bindings": [
    { "agentId": "main", "match": { "channel": "telegram", "accountId": "default" } },
    { "agentId": "polyclaw", "match": { "channel": "telegram", "accountId": "polyclaw" } },
    { "agentId": "sincor-ops", "match": { "channel": "telegram", "accountId": "sincor-ops" } }
  ]
}
```

Then restart gateway. Each bot routes to its agent automatically.

**Customize per agent**:
- In each workspace: Create/edit `SOUL.md` (personality), `AGENTS.md` (rules), `USER.md` (about you/Court).
- For PolyclawTrader: Emphasize risk management, small positions, confirm before large trades, report P&L to you via Telegram.

## Step 4: Install/Verify Polyclaw Skill (For Live Trading Agent)

```bash
# Recommended via ClawHub
clawhub install polyclaw

# Or manual
cd ~/.openclaw/skills
 git clone https://github.com/chainstacklabs/polyclaw.git
cd polyclaw
uv sync  # or pip install -e .
```

Add to your PolyclawTrader agent's skills/tools (via agent chat or config).

## Step 5: Mobile-Specific & Production Hardening

- **Persistence**: Run OpenClaw gateway as systemd service or use `screen`/`tmux` or deploy to Railway/Render if possible (adapt Dockerfile).
- **Alerts**: Instruct agent: "Send me a Telegram message on every trade executed, every P&L update >$5, and on errors."
- **Kill Switch**: Create a command or just message the bot "EMERGENCY STOP ALL TRADING" and have it in SOUL.md to halt.
- **Security**: Never share bot token. Use allowlist for your user ID only. For groups, use private groups + requireMention.
- **While traveling (mobile only)**: Use a good mobile SSH client (Termius, JuiceSSH) to access your server/VPS for restarts/config. Or set up ngrok/cloudflare tunnel for remote gateway access if needed.
- **Logging**: `openclaw doctor` and check gateway logs for issues.

## Troubleshooting Common Issues (From Previous Sessions)

- Bot not responding: Check gateway running, token correct, pairing approved, dmPolicy=pairing.
- "telegram plugin not available": `openclaw plugins list` ; reinstall if needed: `openclaw plugins install telegram` or update openclaw.
- Multiple bots conflict: Use the accounts + bindings structure above.
- Agent forgets context: Separate workspaces per agent.
- Want custom beyond OpenClaw: See the LangGraph starter in sibling file.

## Next: Make It Work For You

Once Telegram is live:
1. Chat with PolyclawTrader bot: "Show me current Polymarket markets trending in crypto" or "What's my current P&L and positions?"
2. To go live: See polyclaw_live_setup.md
3. Instruct agent to run autonomously: "Monitor markets every 15min, alert me on opportunities, execute only with my confirmation for >$20 positions unless I pre-approve a strategy."

This setup lets you talk to your agents from anywhere on mobile. Get the token created now, then run the config commands.

If your SINCOR2 on Railway needs the Telegram layer added (custom endpoint or webhook), provide the current Railway service URL or repo access and I will extend the code immediately.

**You now have a complete, battle-tested path. Execute Step 1 (BotFather) immediately, then report back the token or any error and we'll finish in one pass.**