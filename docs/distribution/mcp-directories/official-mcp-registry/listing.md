# Official MCP Registry — listing package

**Registry:** https://registry.modelcontextprotocol.io — the canonical MCP
registry run by the Model Context Protocol project. Verified live 2026-10-12.

**Why this one matters most:** PulseMCP's paused submissions page says the
Official MCP Registry is *"the best first step even when we are not paused,
and we will pick it up automatically"*; mcp.directory also auto-discovers
from it. One publish feeds three surfaces — **once the packaging prerequisite
below is met** (it is not met yet; this package is BLOCKED, see
`SUBMIT.md`).

## How publishing works (no web form)
Publishing is done with the `mcp-publisher` CLI, authenticated via GitHub
OIDC. The server name must be reverse-DNS under a namespace you own on
GitHub — for this repo that is:

**`io.github.orderofchaos33/sincor2`**

**Packaging prerequisite:** the registry requires a published package
(npm / pypi / oci / nuget / **mcpb**) or a live remote URL — a bare GitHub
repo is NOT accepted. SINCOR currently ships no package: the server only
exists as a checkout of `src/sincor2/mcp_server.py`. The recommended path is
a `.mcpb` bundle attached to a GitHub Release (founder step — `SUBMIT.md`
step 1). The `packages[]` `identifier` and `fileSha256` in the draft are
`TODO-FOUNDER:` placeholders until that release exists.

## Ready-to-paste metadata

**Name:** `io.github.orderofchaos33/sincor2`

**Title / display:** `SINCOR Marketplace MCP`

**Version:** `1.0.0` (matches `SERVER_VERSION` in `src/sincor2/mcp_server.py`;
bump in lockstep if the module version changes)

**Description (92 chars — schema cap is 100; the previous draft was 422 and
invalid):**
`MCP server for the SINCOR A2A task marketplace: discover sealed-bid auctions, cards, quotes.`

**Repository:** `https://github.com/OrderofChaos33/SINCOR2`

**License:** `MIT`

**Package:** `registryType: mcpb` — identifier = the GitHub Release asset URL
(TODO-FOUNDER until the release exists), `fileSha256` = the bundle's
SHA-256 hex.

**Transport:** `stdio` — the ONLY allowed transport shape in the schema is
`{"type": "stdio"}` inside the package entry. The old draft's top-level
`transport` object with `command`/`args`/`env` was schema-invalid and has
been removed. (Runtime details — `python -m sincor2.mcp_server`,
`PYTHONPATH=src`, `SINCOR_MCP_BASE_URL` — live in the `.mcpb` bundle's own
`manifest.json` `user_config`, not in `server.json`.)

A starter `server.json` is drafted in this folder (`server.json.DRAFT`,
rewritten 2026-10-12 after adversarial review). **Validate it with the
publisher CLI's own schema check (`mcp-publisher validate`) before
publishing** — the registry validates server-side and the schema evolves; the
draft is a starting point, not gospel.

## Claim checklist
- Name is `io.github.orderofchaos33/sincor2` — the GitHub org/user must be
  `orderofchaos33` at publish time or OIDC verification fails. The founder
  publishes from the account that owns the repo.
- Version `1.0.0` only. Do not publish a higher version than the module's
  `SERVER_VERSION`.
- Description claims trace to the parent
  [README](../README.md#claim-register).
- No `identifier`/`fileSha256` is real yet. Never claim the server is listed
  or that the release exists until SUBMIT.md step 1 is done.
