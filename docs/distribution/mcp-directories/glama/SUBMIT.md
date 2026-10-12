# Glama — submission steps

**Status: READY — needs founder click.** Nothing submitted; no account created.
There is no submit form — listing happens via auto-index + claim.

## Submission URL
https://glama.ai/mcp/servers (browse/search; the listing appears after Glama
crawls the public repo)

## Account needed
GitHub sign-in (to claim ownership of the listing once it exists).

## Founder clicks (step by step)
1. Make sure the repo is public and `src/sincor2/mcp_server.py` is on `main`
   (it is — merged via PR #271).
2. Wait for Glama's crawler to index `OrderofChaos33/SINCOR2`
   (can take days; there is no manual "index now" button).
3. Go to https://glama.ai/mcp/servers and search `SINCOR`.
4. Open the SINCOR listing → click **"Claim ownership"** → sign in with GitHub.
5. Run their automated checks (build + tool introspection). If a check fails
   for want of a build recipe, give it the honest stdio command from
   `listing.md` (`python -m sincor2.mcp_server`, repo root, `PYTHONPATH=src`).
6. Paste the description/metadata from `listing.md` into the listing's
   description field.
7. If Glama auto-detected a wrong install package (npm/PyPI), correct it —
   we ship no package; install is a repo checkout.

## Review process
Automated checks on claim; the listing is already public from the crawl, the
claim adds ownership, verified badge, and edit access.
