Security & Secret Handling — Immediate Steps

1) Treat exposed keys as compromised — rotate immediately
- Generate new keys/wallets and move any on-chain funds to them.
- Revoke or rotate API keys, payment credentials, and any other secrets.

2) Remove secrets from repository (what we've done for you)
- Replaced hard-coded private keys in scripts with environment-backed placeholders.
- Added `.env.example` as a template and ensured `.gitignore` excludes `.env` and secret files.

3) Remove secrets from git history (recommended)
- Use `git filter-repo` or BFG to remove secrets from history, then force-push to remote.
  Example (requires installing git-filter-repo):

    # Remove a specific file entirely from history
    git filter-repo --invert-paths --paths scripts/old-secret-file.js

    # Replace literal secret across history (EXTRA CARE: test first)
    git filter-repo --replace-text replacements.txt

  Example `replacements.txt` content:
    0xdeadbeef...==> <REDACTED>

- Or use BFG Repo-Cleaner (easier for standard secrets):
    bfg --replace-text replacements.txt
    git reflog expire --expire=now --all && git gc --prune=now --aggressive

- After history rewrite, force-push: `git push --force --all` and `git push --force --tags`.
  Coordinate with collaborators — rewriting history is disruptive.

4) Verify & monitor
- Check remote (GitHub) for accidental exposure (GitHub will often show alerts).
- Add GitHub secret scanning and enable repository secret scanning.

5) Future best practices
- Never commit private keys or `.env` files; use secrets/secret managers (GitHub Actions secrets, Vault, etc.).
- Use ephemeral deployer wallets for automated tasks; fund them with minimal required amounts and rotate after use.
- Add pre-commit hooks to prevent secrets from being committed (e.g., detect AWS/ETH private key patterns).

If you'd like, I can:
- Run the recommended `git filter-repo` / BFG steps here and force-push to the remote (requires your confirmation and may disrupt history), or
- Produce a minimal, safe script you can run locally with step-by-step commands and checks.
