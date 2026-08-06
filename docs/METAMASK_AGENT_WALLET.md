# MetaMask Agent Wallet for SINCOR Personal Agents

**Status:** Live as of 2026-08-06  
**Purpose:** Give your personal SINCOR agents (TOA, revenue, DeFi loops, SINC/AXM settlement) a self-custodial, policy-enforced execution path that never exposes private keys to the agent process.

## Why this exists

SINCOR already has careful dry-run + kill-switch + bankroll controls in `execution_adapter.py` and `bankroll.py`. The MetaMask Agent Wallet upgrades the on-chain path:

- Keys stay in TEE (server-wallet mode)
- Every tx is simulated + Blockaid-scanned + MEV-protected
- Guard Mode enforces spend limits + allowlists + 2FA on policy breaches
- Transaction Protection coverage up to $10k/month on eligible txs
- Explicit support for OpenClaw / Claude Code / Codex / Hermes / Cursor

## One-time host setup (the machine that runs your personal agents)

```bash
npm install -g @metamask/agent-wallet@latest
npx skills add MetaMask/agent-skills
# when prompted, install metamask-agent-wallet

mm doctor
mm init --wallet server-wallet --mode guard
```

Prefer **MetaMask Mobile QR** or a dedicated Google/email used only for this agent wallet.

Verify:

```bash
mm doctor
mm auth status
mm wallet address
mm wallet trading-mode get
mm wallet policy get
```

You must see `authenticated: true` and `initialized: true` before funding.

## SINCOR policy (Base first)

Lock the policy tightly for production:

- Network: Base (8453) only until proven
- Tokens: native ETH, USDC, SINC `0x9C8cd8d3961F445D653713dE65C6578bE11668e7`, AXM `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822`
- Recipients / protocols: treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` + your known Uniswap V4 hooks / Morpho / routers
- Rolling 24h outflow limit: start low ($500–$2000 equivalent)

Commands:

```bash
mm wallet policy get
mm wallet policy template
mm wallet policy set --policy '<tightened yaml>'
mm wallet trading-mode get   # must remain guard
```

## Using the adapter inside SINCOR

```python
from sincor2.adapters.metamask_agent_wallet import get_metamask_agent_wallet

mmw = get_metamask_agent_wallet()

# Always check readiness
status = mmw.status()
assert status.authenticated and status.initialized

# Read
addr = mmw.address()
bal = mmw.balance(chain_id=8453)

# Write (dry-run by default)
result = mmw.transfer(
    to="0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
    amount="0.01",
    token="native",
    chain_id=8453,
)
# result.simulated == True until METAMASK_AGENT_LIVE=true
```

Enable live execution only when you are ready:

```bash
export METAMASK_AGENT_LIVE=true
```

The adapter still respects the global bankroll kill switch.

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `METAMASK_AGENT_LIVE` | `false` | Must be `true` for real txs |
| `MM_CLI` | `mm` | Path to the CLI binary |
| `METAMASK_AGENT_TIMEOUT` | `120` | Seconds per command |

## Integration points

- **Personal OpenClaw / coding agents**: install the MetaMask skills; they already know the `mm` commands.
- **SINCOR Python agents / TOA / revenue paths**: import `get_metamask_agent_wallet()` and call `transfer` / `swap_*` / `send_to_treasury`.
- **A2A Agent Cards**: add a capability such as `metamask_agent_wallet_execute` on the agents that are allowed to settle under policy.

## Safety rules (non-negotiable)

1. Fund the agent wallet with operational capital only. Keep main treasury separate.
2. Stay on Guard Mode.
3. Start with low outflow limits.
4. Always run `mm doctor` at the start of any spending session.
5. Never put a mnemonic on the command line; use server-wallet.
6. Approve flagged txs promptly via the channel you chose at sign-in (email or MetaMask Mobile).

## Related files

- Adapter: `src/sincor2/adapters/metamask_agent_wallet.py`
- Existing execution controls: `src/sincor2/execution_adapter.py`, `src/sincor2/bankroll.py`
- On-chain contracts: `onchain/` (SINC, AXM, Morpho oracle, hooks)
