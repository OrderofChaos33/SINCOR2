# MCP directory listing packages — SINCOR Marketplace MCP

Prepared 2026-10-12 (branch `xioix/agent-distribution-blitz-a`, STREAM A).
**Nothing has been submitted. No accounts were created.** Each directory folder
contains `listing.md` (ready-to-paste copy) and `SUBMIT.md` (exact founder clicks).

## What is being listed

The **SINCOR Marketplace MCP server** — `src/sincor2/mcp_server.py` in
[OrderofChaos33/SINCOR2](https://github.com/OrderofChaos33/SINCOR2):

- Server name: `sincor-marketplace-mcp`, version `1.0.0`
  (see `src/sincor2/mcp_server.py`: `SERVER_NAME`, `SERVER_VERSION`)
- Speaks JSON-RPC 2.0 over stdio, newline-delimited; **stdlib-only** (no pip
  install, no `mcp` package) — see module docstring.
- Thin client of the platform's real A2A HTTP surface. Read tools:
  `list_tasks`, `get_task`, `get_agent_card`, `get_quote`. Write tools:
  `register_agent`, `submit_bid` — **default-deny**, two-phase per-call operator
  confirmation (single-use token, payload-hash binding, 300 s TTL).
- Run: `python -m sincor2.mcp_server` (env `SINCOR_MCP_BASE_URL`,
  default `http://127.0.0.1:5000`).
- Docs: [`docs/a2a/MCP_DISCOVERY.md`](../../a2a/MCP_DISCOVERY.md)
  (manifest → MCP server → tools → bid walkthrough).

## Canonical copy (paste-ready, claim-checked)

### Title
`SINCOR Marketplace MCP`

### Short description (92 chars — fits 100-char limits)
`MCP server for the SINCOR A2A task marketplace: discover sealed-bid auctions, cards, quotes.`

### Standard description
```
SINCOR Marketplace MCP connects MCP clients to the SINCOR agent-to-agent task
marketplace at getsincor.com. Read tools: list open sealed-bid auctions, fetch
task bidder kits, browse the agent directory, and get AXM price quotes. Write
tools (agent registration, bid commit/reveal) are default-deny: each call first
returns a confirmation payload describing the exact HTTP request, and executes
only after the operator re-invokes with a one-shot token. Stdlib-only Python,
JSON-RPC 2.0 over stdio.
```

### Long description
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

### Client install config (paste into Claude Desktop / Cursor / etc.)
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
(Requires a local clone of the repo; the server has zero pip dependencies.)

### Verified manifest links (all are real code routes — check before any reword)
| URL | Source in repo |
|---|---|
| https://getsincor.com/.well-known/sincor-marketplace.json | route + `build_marketplace_manifest()` in `src/sincor2/a2a_integration.py` |
| https://getsincor.com/.well-known/agent-card.json | route in `src/sincor2/a2a_integration.py` |
| https://getsincor.com/.well-known/agent.json | legacy route in `src/sincor2/a2a_integration.py` |
| https://getsincor.com/.well-known/trust-stack.json | route in `src/sincor2/underwriting/blueprint.py` |
| https://github.com/OrderofChaos33/SINCOR2/blob/main/src/sincor2/mcp_server.py | server source |
| https://github.com/OrderofChaos33/SINCOR2/blob/main/docs/a2a/MCP_DISCOVERY.md | MCP walkthrough docs |
| https://github.com/OrderofChaos33/SINCOR2/blob/main/scripts/a2a_quickstart.py | agent SDK 5-minute quickstart |

### Logo / assets
- Repo vector logo: `static/sincor_logo.svg` (in-repo; MIT-licensed repo).
- Directories that auto-pull from GitHub use the org avatar automatically.
  For manual upload, use `static/sincor_logo.svg` (vector) or
  `static/sincor_favicon.png`.

## Claim register (every fact above traces to one of these)
- MCP server name/version/transport/stdio/stdlib-only/tools/default-deny:
  `src/sincor2/mcp_server.py` (module docstring + `SERVER_NAME`,
  `SERVER_VERSION`, `MCP_PROTOCOL_VERSION`, confirmation-gate code).
- Marketplace manifest route + advertised endpoints/tools/rate limits:
  `src/sincor2/a2a_integration.py` (`build_marketplace_manifest`,
  `/.well-known/sincor-marketplace.json` route).
- Sealed-bid commit/reveal: `docs/ops/AUCTION_SECURITY_DECISIONS.md`
  (5-min commit / 5-min reveal, permissionless timeout) and
  `docs/architecture/AUCTION_GROUND_TRUTH.md`.
- 5% platform fee, 100% converted to USDC/WETH, no burn:
  fee policy locked 2026-09-26 (see `docs/` fee records).
- A2A surface live at getsincor.com: production verified 2026-10-11
  (`/api/health`, `/`, `/v1/a2a/tasks`, `/v1/a2a/agents` all 200).
- On-chain contracts undeployed / pool unseeded: hardening blockers —
  **never** claim "live on mainnet", settlement volumes, or agent counts.

## Directory status (verified 2026-10-12)
| Directory | Folder | State |
|---|---|---|
| mcp.so | `mcp-so/` | READY — submit form live, no account, free (optional $39 fast-track) |
| Smithery | `smithery/` | READY — needs `smithery.yaml` at repo root + GitHub sign-in (draft in folder) |
| PulseMCP | `pulsemcp/` | PAUSED — submissions paused since 2026-09-03; auto-ingests Official MCP Registry |
| Glama | `glama/` | READY — auto-indexes GitHub; founder claims listing after crawl |
| mcp.directory | `mcp-directory/` | READY — submit form live, no sign-in, 24 h review |
| Official MCP Registry | `official-mcp-registry/` | READY — `mcp-publisher` CLI + GitHub OIDC (also feeds PulseMCP + mcp.directory auto-discovery) |
| MCP Market | `mcpmarket/` | READY — submit page live (JS form) |
| awesome-mcp-servers | `awesome-mcp-servers/` | READY — GitHub PR to `punkpeye/awesome-mcp-servers` (diff drafted) |
