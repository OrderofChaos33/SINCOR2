# Official MCP Registry — submission steps

**Status: READY — needs founder click (CLI publish, not a web form).**
Nothing published; no credentials touched.

## Account needed
GitHub authentication via OIDC — run by the founder (or on a machine where
the founder is logged into GitHub). The publishing identity must own the
`orderofchaos33` GitHub namespace, or the registry rejects the
`io.github.orderofchaos33/*` name.

## Founder steps (step by step)
1. Install the publisher CLI (follow the install docs at
   https://registry.modelcontextprotocol.io — the tool is `mcp-publisher`).
2. Log in: `mcp-publisher login github` (opens the GitHub OIDC flow —
   the founder completes it in the browser).
3. Copy `server.json.DRAFT` from this folder to a scratch location as
   `server.json`. **Validate first** against the current schema
   (`mcp-publisher` validates on publish; also diff the draft against the
   registry's current schema docs — the draft was written 2026-10-12 and the
   schema evolves).
4. Confirm the metadata matches the module: version `1.0.0` =
   `SERVER_VERSION` in `src/sincor2/mcp_server.py`; name =
   `io.github.orderofchaos33/sincor2`.
5. Publish: `mcp-publisher publish` (from the directory holding `server.json`).
6. Verify: `curl "https://registry.modelcontextprotocol.io/v0/servers?search=sincor"`
   should return `"name":"io.github.orderofchaos33/sincor2"`.

## What happens next
- The registry lists the server immediately on successful publish.
- **PulseMCP** auto-ingests from the registry — the paused-submissions problem
  is bypassed (their own page says so).
- **mcp.directory** auto-discovers from the registry — claim it there instead
  of submitting the web form.
- On later server releases, re-run `mcp-publisher publish` with a bumped
  `version` (kept in step with `SERVER_VERSION`).

## Do not
- Do not publish under anyone else's namespace (e.g. `io.github.<other>`);
  OIDC verification will fail and it looks like squatting.
- Do not publish a version ahead of the code.
