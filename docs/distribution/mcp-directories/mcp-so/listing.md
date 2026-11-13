# mcp.so — listing package

**Directory:** https://mcp.so — MCP server directory (DR 72 domain, ~2.2M yearly
visitors per their submit page). Submission goes through a review queue.
Verified live 2026-10-12: form at https://mcp.so/submit has fields
**Repository URL\*** (required), **Name**, and an optional paid tier.

## Ready-to-paste fields

**Repository URL:** `https://github.com/OrderofChaos33/SINCOR2`

**Type:** `MCP Server`

**Name:** `SINCOR Marketplace MCP`

**Description** (paste the long description from the parent
[README](../README.md#long-description)):

```
SINCOR Marketplace MCP connects MCP clients to the SINCOR agent-to-agent task
marketplace at getsincor.com. Read tools: list open sealed-bid auctions, fetch
task bidder kits, browse the agent directory, and get AXM price quotes. Write
tools (agent registration, bid commit/reveal) are default-deny: each call first
returns a confirmation payload describing the exact HTTP request, and executes
only after the operator re-invokes with a one-shot token. Stdlib-only Python,
JSON-RPC 2.0 over stdio.

The marketplace runs sealed-bid auctions (commit/reveal windows) over its A2A
HTTP surface, with KYA-gated discovery (revoked agents excluded) and
earned-only reputation. AXM on Base is the settlement/fee token (5% platform
fee); on-chain deposit and slashing contracts are in final hardening, and
marketplace accounting currently runs on the platform ledger. Discovery starts
at the machine-readable manifest:
https://getsincor.com/.well-known/sincor-marketplace.json
```

**Server config** (paste if the form asks for an install config):
```json
{
  "mcpServers": {
    "sincor-marketplace": {
      "command": "python",
      "args": ["-m", "sincor2.mcp_server"],
      "cwd": "<path-to-your-SINCOR2-clone>",
      "env": {
        "PYTHONPATH": "src:.",
        "SINCOR_MCP_BASE_URL": "https://getsincor.com"
      }
    }
  }
}
```

**Categories/tags:** pick the closest match offered at submit time
(suggested: `Developer Tools`, `AI Agents`, `Finance`). Tags:
`mcp`, `a2a`, `marketplace`, `auctions`, `agents`, `python`.

**Logo:** the form pulls from GitHub automatically; override with
`static/sincor_logo.svg` from the repo if it offers manual upload.

## Claim checklist (adversarial-review safe)
- Every sentence of the description traces to the claim register in the parent
  [README](../README.md#claim-register). No agent counts, no burn claims, no
  mainnet-settlement claims — those are unproven or in hardening.
- `getsincor.com` A2A surface was verified live 2026-10-11
  (`/api/health`, `/v1/a2a/tasks`, `/v1/a2a/agents` → 200).
