# Official MCP Registry — submission steps

**Status: BLOCKED — the packaging prerequisite is unresolved.** Nothing has
been published; no credentials touched. The registry does NOT accept a bare
GitHub repo: it requires either a published package
(npm / pypi / oci / nuget / **mcpb**) or a live remote endpoint. SINCOR
currently ships neither — the server only exists as a checkout of
`src/sincor2/mcp_server.py`. **Step 1 below must be completed before the CLI
publish in steps 2–6 can work.** The registry-publish path (and therefore the
PulseMCP auto-ingest that depends on it) stays blocked until this lands.

## Packaging decision (FOUNDER DECISION — choose one)

**Option A — .mcpb bundle (RECOMMENDED).** The server is stdlib-only, so a
zero-dependency bundle works. Package the server with the official
`@anthropic-ai/mcpb` tooling, attach the `.mcpb` to a GitHub Release, and
point the draft's `packages[]` entry at the release asset URL (plus its
SHA-256). One release artifact, no hosting, no new infra.

**Option B — hosted Streamable-HTTP endpoint (`remotes[]`).** Publish
`"remotes": [{"type": "streamable-http", "url": "https://…/mcp"}]` instead of
`packages[]`. This requires building and hosting a public MCP endpoint that
does not exist today — a bigger lift, and a founder call on infra/ops cost.

The draft `server.json.DRAFT` in this folder is written for **Option A**
(`registryType: "mcpb"`). Switch to Option B only after the founder chooses
it; do not publish both without deciding.

## Account needed
GitHub authentication via OIDC — run by the founder (or on a machine where
the founder is logged into GitHub). The publishing identity must own the
`orderofchaos33` GitHub namespace, or the registry rejects the
`io.github.orderofchaos33/*` name.

## Founder steps (step by step)
1. **CREATE THE PACKAGE (prerequisite — the old steps 1–6 hit a wall
   without this).** For Option A:
   - `npm install -g @anthropic-ai/mcpb`
   - In a packaging folder containing the server files, run `mcpb init`
     (guided `manifest.json` creation) then `mcpb pack` to produce e.g.
     `sincor-marketplace-1.0.0.mcpb`.
   - Create a GitHub Release `v1.0.0` on `OrderofChaos33/SINCOR2` and attach
     the `.mcpb` as a release asset.
   - Record the release asset URL and the file's SHA-256 hex
     (`sha256sum sincor-marketplace-1.0.0.mcpb`).
   - Fill both into the `packages[0].identifier` and
     `packages[0].fileSha256` placeholders in `server.json.DRAFT`
     (replacing the `TODO-FOUNDER:` values). Nothing else ships until this
     is real — the draft must never be published with placeholders.
2. Install the publisher CLI (follow the install docs at
   https://registry.modelcontextprotocol.io — the tool is `mcp-publisher`).
3. Copy `server.json.DRAFT` from this folder to a scratch location as
   `server.json`.
4. **Validate first:** `mcp-publisher validate server.json` — must pass clean
   against the CURRENT registry schema (the registry also validates
   server-side; the schema evolves). The previous draft failed validation
   (top-level `transport`, 422-char description); this rewrite is fixed, but
   re-check anyway.
5. Log in: `mcp-publisher login github` (opens the GitHub OIDC flow —
   the founder completes it in the browser).
6. Confirm the metadata matches the module: version `1.0.0` =
   `SERVER_VERSION` in `src/sincor2/mcp_server.py`; name =
   `io.github.orderofchaos33/sincor2`.
7. Publish: `mcp-publisher publish` (from the directory holding `server.json`).
8. Verify: `curl "https://registry.modelcontextprotocol.io/v0/servers?search=sincor"`
   should return `"name":"io.github.orderofchaos33/sincor2"`.

## What happens next
- The registry lists the server immediately on successful publish.
- **PulseMCP** auto-ingests from the registry — *but only after this package
  publishes* (see `../pulsemcp/SUBMIT.md`; the "bypass the pause" path is
  BLOCKED until the step-1 release exists).
- **mcp.directory** auto-discovers from the registry — claim it there instead
  of submitting the web form.
- On later server releases, re-run `mcp-publisher publish` with a bumped
  `version` (kept in step with `SERVER_VERSION`) and a fresh `.mcpb` release
  asset + SHA-256.

## Do not
- Do not publish under anyone else's namespace (e.g. `io.github.<other>`);
  OIDC verification will fail and it looks like squatting.
- Do not publish a version ahead of the code.
- Do not publish the draft with `TODO-FOUNDER:` placeholders still in it —
  the registry will reject it and it would advertise a nonexistent package.
- Do not claim the server is "listed" or "on the registry" until step 8
  verifies.
