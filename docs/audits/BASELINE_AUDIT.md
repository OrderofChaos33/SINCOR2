# Baseline Audit — SINCOR2 Repo Overhaul (Phase 1)

- **Branch:** `feat/sincor-a-plus-master-overhaul`
- **Base:** local main HEAD `45f34309` ("W-101 fix: pool ledger reconciliation replaces tautological invariant")
- **Date:** 2026-11-14
- **Method:** direct measurement in this worktree — `git ls-files`, `find`, targeted `grep`
  sweeps, a full `pytest tests/pytest` run, and a `forge build` attempt. Every number
  below was produced by a command run against this tree, not carried over from other
  reports. Where an earlier figure could not be reproduced, that is stated explicitly.

## Measured test baseline (this tree, this invocation)

```
FLASK_ENV=test SECRET_KEY=<32-char> JWT_SECRET_KEY=<32-char> ADMIN_USERNAME=admin \
ADMIN_PASSWORD=testpass STRIPE_SECRET_KEY=sk_test_x PYTHONPATH=src:. \
python -m pytest tests/pytest -q -p no:cacheprovider
→ collected 2213 · 31 failed · 2169 passed · 1 skipped · 12 errors · 555s
```

Failure composition (31): `test_sinc_integration.py` ×7, `test_phase1_marketplace.py` ×7,
`test_a2a_gas_profiling.py` ×4 (eth-tester), `test_launch_gate_dashboards.py` ×3,
`test_vector_protocol_integration.py` ×2, `test_live_pointer_hygiene.py` ×2, and ×1 each in
`test_kya_registry.py`, `test_kya_airdrop_quest.py`, `test_deploy_artifacts.py`,
`test_defi_p24_bridge.py`, `test_contract_net_compliance_and_self_healing.py`,
`test_a2a_streaming.py`. All 12 errors are fixture/setup errors in
`test_a2a_heartbeat_auth.py`.

**Discrepancy note:** a figure of "~78 failures (76 OFAC-cluster)" was supplied with the
overhaul order. It is **not reproduced** on this tree with this invocation: the log
contains zero `ofac` hits and only 2,213 tests collect under `tests/pytest`. The 78-failure
figure evidently came from a different tree/invocation (a cleanup worktree whose full-suite
run collected ~7,200 tests). All overhaul deltas are therefore measured against the
baseline above, re-run with the identical invocation after each step.

`forge build` (root profile) **fails on a fresh worktree**: `lib/forge-std` and
`lib/openzeppelin-contracts` are not vendored (`No such file or directory` for
`lib/openzeppelin-contracts/contracts/token/ERC20/IERC20.sol`, `lib/forge-std/src/Test.sol`,
etc.). This matches the known vendoring situation; CI's forge job skips when libs are absent.

---

## Pillar 1 — Security & Secret Hygiene · Grade: B

Evidence:
- Pattern sweeps over all tracked `*.py` / `*.ts` / `*.js` / `*.sol` (excluding vendored
  libs) for Stripe/GitHub/AWS key shapes, PEM private-key blocks, `private_key = "0x<64hex>"`,
  `api_key = "<literal>"`, embedded-key RPC URLs (Alchemy/Infura/QuickNode/Ankr), and
  `Bearer <token>` literals: **zero hits**.
- Every bare 64-hex literal in non-test source is a public constant: the ERC-20
  `Transfer(address,address,uint256)` topic (`0xddf252ad…b3ef`, 5 files), the empty-keccak
  hash (`marketplace/contract_net/types.py:27`), Polyclaw vault event topics
  (`verticals/trading/polyclaw/vault_event_listener.py:78-82`), and
  `ZERO_PK = 0x000…001` in `scripts/deployPoolsV4.ts:38` (the well-known dummy key #1, not a
  credential). A prior dedicated sweep (`SECRET_SWEEP_REPORT.md`, 2026-09-30) reached the
  same all-clean verdict across 41 branches.
- `.env.example` (129 vars) contains placeholders plus **public** contract addresses only.
- Gaps: (a) `.env.test` is **tracked** in git — its values are explicitly fake placeholders
  (file header says so), but it should not be tracked under a strict `.env*` rule;
  (b) `.gitignore` covers `.env`, `.env.local`, `.env.*.local`, `.env.production.local`,
  `.env.test` but **not** the generic `.env.<environment>` shape — `git check-ignore
  .env.production` returns nothing, so a real `.env.production` could be committed;
  (c) `node_modules/` is ignored only at repo root (`/node_modules/`);
  (d) the codebase reads **328 distinct env vars** (Python `os.environ`/`os.getenv`,
  JS/TS `process.env`, Solidity `vm.env*`, `${VAR}` in foundry/compose/railway configs)
  while `.env.example` documents only 129 — 239 read-but-undocumented variables.
- No tracked build/cache artifacts: `git ls-files` shows **0** files under `cache/`, `out/`,
  `broadcast/`, `node_modules/`, and no tracked `*.db`, `*.sqlite`, `*.log`, `__pycache__`,
  or `.DS_Store`.

## Pillar 2 — 52 Money-Path Registry & Wiring · Grade: D

Evidence:
- No machine-readable registry exists in the repo: no `config/money_paths_registry.json`,
  no money-path index doc. The only canonical status record lives **outside** the repo
  (`~/workspace/goals/make-money/files/52-PATH-STATUS-TABLE.md`, verified 2026-11-14), so
  the repo itself cannot answer "what is the state of path N".
- Verified status counts from that table: **LIVE 3 · SEND-READY 3 · WIRED-NEEDS-TRIGGER 5 ·
  IN-QUEUE (remainder) · NOT FOUND 5** (paths 2, 6, 8-fee, 11-fee, 19). Outside revenue: $0
  on every path.
- The manifest's three named core contracts — `SincorsettlementEngine.sol`,
  `SigilVerifier.sol`, `TimeLockRewardEscrow.sol` — **do not exist** anywhere in the repo.
  The real settlement engine is `contracts/ExecutionEscrowManager.sol` (no treasury address,
  no fee/bps constant). The 5% fee canon is Python-side only:
  `A2A_PLATFORM_FEE_BPS = int(os.getenv("A2A_PLATFORM_FEE_BPS", "500"))` at
  `src/sincor2/a2a_integration.py:104`, treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`.
- **Branch gap found during this audit:** `src/sincor2/axm_antigaming.py` (the
  anonymous-poster / self-bid guard, `check_self_bid`) and `src/sincor2/activity_engine.py`
  (cited as path 1 / path 49 locations in the status table) are **absent from this branch's
  HEAD**. They exist only on unmerged branches (`xioix/axm-activity-engine-r2`,
  `remotes/pull/438`; commits `72269d3f`, `683269c1`, `8345c29c` — verified via
  `git log --all` / `git ls-tree`; `72269d3f` is NOT an ancestor of HEAD). On this branch,
  anonymous-poster handling is limited to a ghost-slash comment at
  `src/sincor2/a2a_inbound_market.py:929`. The registry records this honestly rather than
  pointing at files that are not here.

## Pillar 3 — MoebiusX Isolation & Safety · Grade: C+

Evidence:
- The **live** MoebiusX trader (v3.1 kit, ERC-7579 session key, permissionId `0x56066a14`,
  107 confirmed SUCCESS trades on Base mainnet through 2026-11-14) lives **outside** the
  repo in `~/workspace/ops/moebiusx/` — deliberate isolation: no session-key material, state
  logs, or telemetry can leak into git. That part is sound.
- The in-repo MoebiusX surface is thin and scattered: `onchain/src/hooks/MoebiusMEVHook.sol`
  (+ `onchain/proposals/hooks/` duplicate), `onchain/script/DeployMoebius.s.sol`,
  `onchain/script/InitializeMoebiusPool.s.sol`, `onchain/test/MoebiusMEVHook.t.sol`,
  `onchain/MOEBIUS_V2_NOTES.md`. There is no in-repo package boundary, no repo-side dry-run
  harness for routing math, and the proposals/src duplication is untriaged.
- Circuit-breaker state handling for the live loop lives with the external kit (session
  grant + WITH_RESET rate-limit policy), not in this repo; nothing in-repo documents that
  boundary. (Step 3 of the overhaul addresses the in-repo side.)

## Pillar 4 — Smart Contract Workspace · Grade: C

Evidence:
- **126 `.sol` files** total: 16 under `contracts/`, 107 under `onchain/` (plus tests/scripts
  elsewhere). Two separate Foundry projects: root `foundry.toml` (src `contracts/`, solc
  0.8.27, `via_ir`, fuzz runs 256) and `onchain/foundry.toml`.
- Live/deployed contracts are documented in `CANONICAL_ADDRESSES.md` (AXM
  `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`, SINC `0xe1D836087F6573b665d25CE088793E916D7892f8`,
  Treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`, SharedLiquidityVault
  `0xeA90a257e5Dae20a0472C4812775F28614459bb6` LIVE, SharedLiquidityHook staging
  `0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0`), including a retired-address table — good —
  but there is no per-contract manifest separating production contracts from sandbox /
  experimental / retired ones, and most `.sol` files (escrow, paymaster, hooks) have no
  deployed address and no explicit "not deployed" marker.
- Root `forge build` is broken on a fresh checkout (unvendored `lib/`, see baseline above),
  so the contract workspace cannot be built from a clean clone without manual steps.

## Pillar 5 — Dependency & SDK Packaging · Grade: C

Evidence:
- Three dependency sources that can drift: `pyproject.toml` (name `sincor2` 1.0.1,
  dependencies as unpinned `>=` ranges), `requirements.txt` (74 lines, partially pinned,
  header notes a 2026-07-27 pin repair), `requirements.lock` (180 pinned entries, generated
  2026-09-25 era), plus `requirements-dev.txt` and `package.json` / `package-lock.json` for
  the TS side. No single documented source of truth.
- `sdk/python/` is the **A2A** SDK (`sincor_a2a`, own `pyproject.toml`); `sdk/typescript/`
  holds `sincor-a2a`. The **sincor-x402** SDK (PyPI v0.1.1, LIVE) is **not** a package in
  this repo — the repo carries the server side (`src/sincor2/x402_payments.py`,
  `config/x402_pricing.yaml`, `tests/test_x402_payments.py`). That split is undocumented.
- CI is fragmented across 9 workflows (`ci.yml`, `pr-ci.yml`, `onchain-ci.yml`,
  `security.yml`, `codeql.yml`, `codacy.yml`, `deploy-base.yml`, `indexer-agent.yml`,
  `metrics-freshness.yml`) with overlapping responsibilities.

## Pillar 6 — Test Coverage & Fuzzing · Grade: B−

Evidence:
- Python: 167 `test_*.py` files under `tests/`; the CI-invoked suite (`tests/pytest`)
  collects 2,213 tests — **2,169 passed (98.0%), 31 failed, 1 skipped, 12 errors** at
  baseline (composition above). All failures are pre-existing; none are in money-path
  fee logic (`A2A_PLATFORM_FEE_BPS` path is covered by passing tests, e.g.
  `tests/test_a2a_fee_split_axm.py` at repo level).
- Solidity: 30 `.t.sol` files (3 root `test/`, 27 `onchain/test/`); 5 files contain
  fuzz/invariant tests; root `foundry.toml` already sets `[fuzz] runs = 256`. A BountyPool
  conservation invariant + 30-seed fuzz landed recently (`5eb723a0`). But with `lib/`
  unvendored, none of it runs from a fresh worktree — rigor exists on paper, not in a
  clean-clone workflow.
- No coverage measurement is wired into CI in this tree.

## Pillar 7 — Institutional Documentation · Grade: C

Evidence:
- `README.md` is substantial (496 lines). `ARCHITECTURE.md` is 76 lines, `SECURITY.md` 26
  lines, `CONTRIBUTING.md` 39 lines — thin for institutional review.
- `docs/` holds hundreds of files but is dominated by dated operational briefs
  (e.g. ~30 `CEO_DAILY_BRIEF_*.md` files), with no audits directory and no index separating
  durable institutional docs from ephemeral ops notes.
- Positive: `CANONICAL_ADDRESSES.md`, `DEPLOYMENT_GUIDE.md`, `TOKEN_CANON.json` /
  `TOKEN_CANON.md`, and per-area specs (`docs/DEFI_16_DEEP_SPECS.md`,
  `docs/DEFI_PRODUCT_ARM.md`, `docs/sinax/README.md`) exist and are substantive.

---

## Summary scorecard (baseline)

| Pillar | Grade | One-line reason |
|---|---|---|
| 1. Security & Secret Hygiene | B | No real secrets tracked; `.env*` ignore gap + tracked `.env.test` + 239 undocumented env vars |
| 2. 52 Money-Path Registry & Wiring | D | No in-repo registry; 5 paths NOT FOUND; manifest's core contracts don't exist |
| 3. MoebiusX Isolation & Safety | C+ | Live kit correctly external; in-repo surface scattered, boundary undocumented |
| 4. Smart Contract Workspace | C | 126 `.sol` across two projects; no production/sandbox manifest; clean-clone build broken |
| 5. Dependency & SDK Packaging | C | Three driftable dependency sources; x402 SDK split undocumented |
| 6. Test Coverage & Fuzzing | B− | 98.0% pytest pass; fuzz configured; forge unrunnable from fresh worktree |
| 7. Institutional Documentation | C | Strong README; thin ARCHITECTURE/SECURITY; docs/ cluttered with ops briefs |

*Grades are the auditor's assessment of the measured evidence above. Post-overhaul state
is reported separately; nothing in this document certifies production readiness.*

---

## Errata (post-integration, 2026-11-14)

The measurements in this audit were taken on a side-branch tree (`45f34309`)
before the overhaul branches were rebased onto main `4fd2c248`. Several
branch-gap findings above are artifacts of that wrong base and do not hold on
main:

- **`.sol` count:** on main the count is **157, not 126**
  (`find . -name '*.sol' -not -path './node_modules/*' -not -path './lib/*'`).
  The full reclassification is in `contracts/CONTRACTS_MANIFEST.md`
  (post-integration update).
- **`axm_antigaming.py` / `activity_engine.py`:** both ARE present on main —
  `src/sincor2/axm_antigaming.py` (`check_self_bid` at line 310, enforced via
  `check_bid_eligibility` in `src/sincor2/a2a_inbound_market.py`) and
  `src/sincor2/activity_engine.py`. The Pillar 2 "branch gap" paragraph above
  (and the matching notes in `config/money_paths_registry.json` /
  `docs/MONEY_PATHS_INDEX.md`, since corrected) described the side-branch
  tree only. A related correction: a file named
  `src/sincor2/SincorsettlementEngine.sol` also exists on main — a 79-line
  standalone prototype, not a core settlement contract; `SigilVerifier.sol`
  and `TimeLockRewardEscrow.sol` remain genuinely absent.
- **Pytest baseline:** the 31-failure baseline above was measured on the
  side-branch tree with a `tests/pytest`-only invocation (2,213 collected).
  Main's pytest baseline is the **78-failure figure (76 OFAC lapse cluster +
  2 suite-context flakes, 7,149 passed)** measured on the cleanup tree at the
  same main commit `4fd2c248` — the figure this audit's discrepancy note could
  not reproduce was the correct one for main; the difference is the tree and
  the invocation, not the code.

The audit body above is left as originally written; this errata is the
correction record.
