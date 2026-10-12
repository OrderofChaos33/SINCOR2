# Smithery — submission steps

**Status: READY — needs founder click.** Nothing submitted; no account created.
Prerequisite file is drafted, not committed.

## Account needed
GitHub sign-in on smithery.ai (the listing must be owned by someone who can
prove repo ownership — that is the founder's GitHub account,
`OrderofChaos33`).

## Founder clicks (step by step)

### A. Commit the launch config (one-time)
1. Copy `smithery.yaml.DRAFT` from this folder to the **SINCOR2 repo root**
   as `smithery.yaml` (on `main`, via a normal PR — it is a 20-line config).
2. Verify it landed: https://github.com/OrderofChaos33/SINCOR2/blob/main/smithery.yaml

### B. Add the server
1. Go to https://smithery.ai and **sign in with GitHub**
2. Click **"Add server"** → choose the GitHub-repo path
3. Enter `OrderofChaos33/SINCOR2` — it pulls metadata
4. Paste fields from `listing.md` in this folder:
   display name `SINCOR Marketplace MCP`, description, categories
   (`Developer Tools`, `AI Agents`), tags, license `MIT`,
   logo `static/sincor_logo.svg`
5. Confirm the start command shows the `smithery.yaml` stdio config
   (command `python`, args `-m sincor2.mcp_server`)
6. Click **Publish**

## Review process
Automated/CLI-driven; typically live within minutes once publish succeeds.
Stdio servers get wrapped and proxied over HTTP by Smithery — expect the
hosted endpoint to appear on the server page's Deployments tab.

## After it is live
The server page URL will look like
`https://smithery.ai/servers/OrderofChaos33/sincor-marketplace-mcp`
(exact slug confirmed at publish time). Record the real URL in the parent
README and re-verify the tool list renders.
