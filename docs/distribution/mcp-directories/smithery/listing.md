# Smithery — listing package

**Directory:** https://smithery.ai — MCP registry with one-click installs and
hosted deployments. Verified live 2026-10-12. Two things are required to list:
(a) a `smithery.yaml` in the repo root telling Smithery how to start the
server, and (b) signing in with GitHub to add/claim the server.
A draft `smithery.yaml` is in this folder (`smithery.yaml.DRAFT`) — it is
**not** committed to the repo root yet (founder decision).

## Ready-to-paste fields

**Display name:** `SINCOR Marketplace MCP`

**Slug:** `sincor-marketplace-mcp` (matches `SERVER_NAME` in
`src/sincor2/mcp_server.py`)

**GitHub repo:** `https://github.com/OrderofChaos33/SINCOR2`

**Homepage:** `https://getsincor.com`

**Description:** paste the long description from the parent
[README](../README.md#long-description).

**Categories:** `Developer Tools`, `AI Agents`

**Tags:** `mcp`, `a2a`, `marketplace`, `auctions`, `agents`, `python`,
`stdio`, `base`

**Use cases (if the form asks):**
- "Let an MCP client discover and bid on agent-to-agent task auctions"
- "Fetch task bidder kits and agent cards from a live marketplace"
- "Get AXM price quotes before committing a bid"

**License:** `MIT` (repo LICENSE)

**Logo:** upload `static/sincor_logo.svg` from the repo.

**Transport note:** the server is **stdio** (`python -m sincor2.mcp_server`).
Smithery supports stdio but marks it deprecated — it wraps stdio servers and
proxies them over HTTP. That is acceptable for a listing; do not claim a
native HTTP transport (we don't ship one).

**Tool-list visibility:** Smithery can only display tool lists for servers
whose `tools/list` needs no auth. Ours qualifies: `tools/list` returns all six
tools with no API key (payment/confirmation only gates *calls* to the two
write tools).

## smithery.yaml draft
See `smithery.yaml.DRAFT` in this folder. It declares:
```yaml
startCommand:
  type: stdio
  configSchema: { baseUrl (optional, default https://getsincor.com) }
  commandFunction: python -m sincor2.mcp_server
                   with PYTHONPATH=src and SINCOR_MCP_BASE_URL from config
```
Stdlib-only: no pip install step needed.

## Claim checklist (adversarial-review safe)
- All description claims trace to the parent [README](../README.md#claim-register).
- Do not check "remote/hosted" options you don't control; the only hosted
  surface we claim is `https://getsincor.com` (verified live 2026-10-11).
- Do not set a version higher than `1.0.0` (that's `SERVER_VERSION`).
