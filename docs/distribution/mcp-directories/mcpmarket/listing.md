# MCP Market — listing package

**Directory:** https://mcpmarket.com — MCP server directory with ranked
listings by category. Verified live 2026-10-12: the submit page exists at
https://mcpmarket.com/submit (title "Submit an MCP Server | MCP Market";
the form itself is JS-rendered, so treat the exact field list below as
to-confirm at submit time).

## Ready-to-paste fields

**Name:** `SINCOR Marketplace MCP`

**GitHub repo:** `https://github.com/OrderofChaos33/SINCOR2`

**Website:** `https://getsincor.com`

**Description:** the long description from the parent
[README](../README.md#long-description).

**Short/tagline version** (if the form wants one line):
`MCP server for the SINCOR A2A task marketplace: discover sealed-bid auctions, cards, quotes.`

**Category:** `Developer Tools` (closest observed category; alternatives on
their taxonomy: `Analytics & Monitoring`). Pick the closest match offered.

**Tags:** `mcp`, `a2a`, `marketplace`, `auctions`, `agents`, `python`.

**Install config** (if the form asks):
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

**Logo:** upload `static/sincor_logo.svg` from the repo if manual upload is
offered; otherwise it pulls the GitHub avatar.

## Claim checklist
All description claims trace to the parent
[README](../README.md#claim-register). If the JS form has fields not covered
here, fill them from the canonical copy only — do not invent metrics.
