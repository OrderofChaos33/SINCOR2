# Glama — listing package

**Directory:** https://glama.ai/mcp/servers — "Open-Source MCP Servers"
registry (97,619 servers at time of check, 2026-10-12). **Glama indexes
public GitHub repositories automatically** — there is no submit form to fill.
The founder's job is to **claim** the auto-created listing after Glama crawls
the repo, then run its checks and paste metadata.

## Ready-to-paste fields (for the claim/edit form)

**Name:** `SINCOR Marketplace MCP`

**GitHub repo:** `https://github.com/OrderofChaos33/SINCOR2`

**Description:** the long description from the parent
[README](../README.md#long-description).

**Categories/tags:** whatever taxonomy the claim form offers; suggested
`Developer Tools` / `AI Agents`. Tags: `mcp`, `a2a`, `marketplace`,
`auctions`, `agents`, `python`.

**Install command** (Glama generates install configs; if it asks for one):
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

## Notes specific to Glama
- Glama builds the server and introspects its tools during the claim check.
  Our server is stdlib-only Python run as `python -m sincor2.mcp_server`
  from the repo root — no Dockerfile, no npm package. If their check wants a
  build recipe, the honest answer is the command above (there is nothing to
  compile).
- Glama scores license / quality / maintenance from the repo. We are MIT
  (`LICENSE`), active, and the MCP module has a real test suite
  (`tests/pytest/test_mcp_server.py`, `test_mcp_payment_challenge.py`).
- Do not let the claim form invent an npm/PyPI package — we ship none.
  Correct any auto-detected package field to "repo checkout".

## Claim checklist
All description claims trace to the parent
[README](../README.md#claim-register). Fix the README-side facts, not the
Glama-side text, if anything it scraped is wrong.
