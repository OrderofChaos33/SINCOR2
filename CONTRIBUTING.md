# Contributing to SINCOR2

Thanks for contributing. SINCOR2 runs live money paths, so the bar is:
**verified, scoped, and reversible.** A change is done when it is wired
end-to-end and proven — not when it merely compiles.

## Contribution priorities

- A2A interoperability and agent-marketplace correctness
- Money-path safety: auctions, settlement, fees, escrow accounting
- Reliability of the autonomous loops (liveness, trading, outreach)
- Documentation that matches the code (docs that overclaim are bugs)

## Branch & PR flow

1. **Never push directly to `main`.** All work lands through a pull
   request. `main` is protected: PR + review + green CI required.
2. Branch from current `main` with a descriptive name
   (`feat/…`, `fix/…`, `xioix/<lane>` for fleet work).
3. Keep scope narrow; unrelated refactors ride their own branch.
4. Atomic commits with conventional prefixes (below).
5. Open the PR with: what changed, why, risks, and the exact verification
   you ran (commands + results). "Tests pass" without the commands is
   not verification.
6. Update docs in the same PR when behavior, architecture, or interfaces
   change. If a change alters a revenue path, update
   `config/money_paths_registry.json` and `docs/MONEY_PATHS_INDEX.md`
   and run `python scripts/verify_money_paths.py`.

Founder-gated actions — merges of money-path changes, deploys, treasury
or fund movement, live switches, private-key handling, legal/compliance
flips — are never self-approved, regardless of urgency.

## Commit conventions

Conventional Commits, one logical change per commit:

```
feat:     new capability
fix:      bug fix
refactor: behavior-preserving restructure
test:     tests only
docs:     documentation only
chore:    tooling, config, housekeeping
```

Subject in the imperative mood, ≤ 72 chars; body explains *why* and any
verification evidence. Never commit secrets, `.env` files, build caches,
or generated artifacts (see `.gitignore`).

## Two-tier testing requirement

Smart-contract and money-path changes must pass **both** tiers:

| Tier | When | Requirement |
|---|---|---|
| **Fast (PR gate)** | Every PR | `forge build --sizes && forge test` on the default profile — **256 fuzz runs** — plus the pytest gate below |
| **Deep (pre-deploy)** | Before any deploy or mainnet-facing release | `FOUNDRY_PROFILE=ci-deep forge test` — **10,000+ fuzz runs** plus invariant suites (funds conservation: escrowed = active bounties + pending settlements + protocol fees) |

Python gate (CI environment, Python 3.11):

```bash
pip install -r requirements.txt
export PYTHONPATH=src:. FLASK_ENV=test ENVIRONMENT=test
export JWT_SECRET_KEY=test-jwt-secret-key-32-char-minimum-ok
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=admin-password-32-char-minimum-ok
export SINCOR_TASK_QUEUE=eager
ruff check src/sincor2/app.py src/sincor2/mvp_app.py src/sincor2/settings.py \
           src/sincor2/startup.py src/sincor2/error_handling.py \
           src/sincor2/blueprints tests/pytest
pytest tests/pytest -q
python scripts/verify_money_paths.py
```

Rules that are not negotiable:

- **Green baseline first**: reproduce the failure / record the current
  state before changing code; a fix without a failing-then-passing test
  is a claim, not a fix.
- **No weakened guards**: a test that fails against a security guard
  (self-bid, anonymous-poster, OFAC, merit) is presumed a stale test —
  prove the guard wrong before touching either.
- **Testnet/fork before mainnet**: contract changes rehearse on Base
  Sepolia or a fork; production contract bytecode, storage layouts, and
  ABI signatures of deployed contracts are immutable from this repo.
- The local pre-commit hook (secret scan, injection guards, smoke suite)
  must pass; do not use `--no-verify`.

## Pull request checklist

- [ ] Scoped, non-breaking; atomic conventional commits
- [ ] Tests: failing-then-passing evidence for fixes; both fuzz tiers
      for contract/money-path changes
- [ ] `scripts/verify_money_paths.py` green if a revenue path changed
- [ ] Docs/registry updated in the same PR when behavior changed
- [ ] No secrets, no `.env`, no cache artifacts in the diff
- [ ] Security and configuration implications stated in the PR body

## Code of collaboration

- Be respectful and precise; review the diff, not the author.
- Claims carry evidence: hashes, test counts, file:line references.
- Prefer explicit architecture decisions over implicit coupling.
