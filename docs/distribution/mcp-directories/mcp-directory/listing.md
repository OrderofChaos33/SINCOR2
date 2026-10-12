# mcp.directory — listing package

**Directory:** https://mcp.directory — MCP server directory.
Verified live 2026-10-12: the submit form at https://mcp.directory/submit
auto-pulls metadata from GitHub (name, description, stars, language, license,
README), auto-detects tools by analyzing the MCP implementation, generates
install configs for major clients, and reviews **within 24 hours**. No
sign-in, no fee.

## Ready-to-paste fields

**GitHub Repository URL (required):**
`https://github.com/OrderofChaos33/SINCOR2`

**npm package (optional):** leave blank — we ship no npm package.

**PyPI package (optional):** leave blank — stdlib-only, no package.

**Short description (optional, ≤100 chars):**
`MCP server for the SINCOR A2A task marketplace: discover sealed-bid auctions, cards, quotes.`

**Your email (optional):** founder's email, to receive the review notification.

**Full description** (the form scrapes the README anyway; paste the long
description from the parent [README](../README.md#long-description) if it
asks for more):

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

## Notes specific to mcp.directory
- Because it scrapes the README, make sure the repo `README.md` describes
  the MCP server accurately (it references the marketplace; the MCP module
  docstring in `src/sincor2/mcp_server.py` is the canonical technical text).
- Tool auto-detection analyzes the MCP implementation: our six tools
  (`list_tasks`, `get_task`, `get_agent_card`, `get_quote`, `register_agent`,
  `submit_bid`) are plainly declared in the module — detection should succeed.
- If already listed via auto-discovery from the Official MCP Registry, use
  their claim flow (email in the "Already listed?" section) instead of
  re-submitting.

## Claim checklist
Description claims trace to the parent
[README](../README.md#claim-register). Leave package fields blank rather than
inventing an npm/PyPI name.
