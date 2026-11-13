# PulseMCP — submission steps

**Status: PAUSED — no founder action available.** Nothing submitted; there is
nothing to submit to right now.

## Submission URL
https://www.pulsemcp.com/submit — currently shows a "temporarily paused"
notice (last updated 2026-09-03, verified 2026-10-12).

## Account needed
None (no intake is open).

## Founder clicks (step by step)
**None right now.** The correct sequence is:

1. Complete the `official-mcp-registry/` package in this folder first
   (`mcp-publisher` CLI publish of `io.github.orderofchaos33/sincor2`).
   **STATED PLAINLY: the "registry publish bypasses the pause" narrative is
   BLOCKED until the packaging prerequisite in
   `../official-mcp-registry/SUBMIT.md` step 1 is resolved.** The registry
   requires a published package (or live remote endpoint); SINCOR currently
   ships neither, so no registry publish — and therefore no PulseMCP
   auto-ingest — is possible yet. PulseMCP's own page says the Official MCP
   Registry is the best first step *even when they are not paused*, and they
   auto-ingest from it, but that whole route waits on the founder creating
   the `.mcpb` release first.
2. Re-check https://www.pulsemcp.com/submit periodically. When the pause
   notice is gone, use the ready-to-paste fields in `listing.md` (this folder)
   in their "Add a server"/submit form — it is a URL + summary form.
3. After submitting, search pulsemcp.com for "SINCOR" to confirm the listing.

## Do not
- Do not email them asking for manual listing — the page explicitly says
  changes to existing listings are also paused.
- Do not treat "paused" as "rejected". It is a pipeline overhaul on their
  side; the registry-publish path is the durable route in.
- Do not treat the registry route as "handled" — it is not, until the
  `.mcpb` release exists and the registry publish verifies.
