# Official MCP Registry — listing package

**Registry:** https://registry.modelcontextprotocol.io — the canonical MCP
registry run by the Model Context Protocol project. Verified live 2026-10-12.

**Why this one matters most:** PulseMCP's paused submissions page says the
Official MCP Registry is *"the best first step even when we are not paused,
and we will pick it up automatically"*; mcp.directory also auto-discovers
from it. One publish feeds three surfaces.

## How publishing works (no web form)
Publishing is done with the `mcp-publisher` CLI, authenticated via GitHub
OIDC. The server name must be reverse-DNS under a namespace you own on
GitHub — for this repo that is:

**`io.github.orderofchaos33/sincor2`**

## Ready-to-paste metadata

**Name:** `io.github.orderofchaos33/sincor2`

**Title / display:** `SINCOR Marketplace MCP`

**Version:** `1.0.0` (matches `SERVER_VERSION` in `src/sincor2/mcp_server.py`;
bump in lockstep if the module version changes)

**Description:** the long description from the parent
[README](../README.md#long-description).

**Repository:** `https://github.com/OrderofChaos33/SINCOR2`

**License:** `MIT`

**Transport:** `stdio` — command `python -m sincor2.mcp_server`,
env `PYTHONPATH=src`, `SINCOR_MCP_BASE_URL` (default `https://getsincor.com`
for clients).

A starter `server.json` is drafted in this folder (`server.json.DRAFT`).
**Validate it with the publisher CLI's own schema check before publishing** —
the registry schema evolves, and the draft is a starting point, not gospel.

## Claim checklist
- Name is `io.github.orderofchaos33/sincor2` — the GitHub org/user must be
  `orderofchaos33` at publish time or OIDC verification fails. The founder
  publishes from the account that owns the repo.
- Version `1.0.0` only. Do not publish a higher version than the module's
  `SERVER_VERSION`.
- Description claims trace to the parent
  [README](../README.md#claim-register).
