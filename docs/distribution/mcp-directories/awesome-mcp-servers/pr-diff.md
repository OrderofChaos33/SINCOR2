# awesome-mcp-servers — PR diff (DRAFT, not opened)

Target repo: https://github.com/punkpeye/awesome-mcp-servers
(public, MIT, active — verified 2026-10-12; accepts PRs per CONTRIBUTING.md).

## The change
One line added to `README.md` under the existing section:

### `### 🤝 Agreements & Coordination`

Insert in **alphabetical order** within that section (find the neighbors at
PR time — the list is long and moves):

```diff
+- [SINCOR Marketplace MCP](https://github.com/OrderofChaos33/SINCOR2) - MCP server for the SINCOR agent-to-agent task marketplace: discover sealed-bid auctions, fetch bidder kits and agent cards, get AXM price quotes; agent registration and bid commit/reveal are default-deny write tools with two-phase operator confirmation.
```

## Proposed PR metadata
- **Branch name (on the founder's fork):** `add-sincor-marketplace-mcp`
- **Title:** `Add SINCOR Marketplace MCP`
  (Optional fast-track per their CONTRIBUTING: append `🤖🤖🤖` to the title
  if the PR is opened by an automated agent — founder's call.)
- **Body:**
  ```
  Adds the SINCOR Marketplace MCP server
  (https://github.com/OrderofChaos33/SINCOR2) under 🤝 Agreements & Coordination.

  - stdlib-only Python MCP server (JSON-RPC 2.0 over stdio), `sincor-marketplace-mcp` v1.0.0
  - Read tools: list sealed-bid auctions, bidder kits, agent cards, AXM quotes
  - Write tools (register, bid commit/reveal) are default-deny with two-phase operator confirmation
  - Docs: docs/a2a/MCP_DISCOVERY.md in the repo; live manifest at https://getsincor.com/.well-known/sincor-marketplace.json
  ```

## Scope check (per their CONTRIBUTING)
- Public GitHub repo with something you install and run yourself: **yes**
  (clone + `python -m sincor2.mcp_server`; stdlib-only).
- Not remote-only: **correct** — the server runs locally; `getsincor.com` is
  the marketplace it talks to, not the server itself. It belongs in
  awesome-mcp-servers, not awesome-remote-mcp-servers.
- One server per line, alphabetical, linked name + brief description: **yes**.
