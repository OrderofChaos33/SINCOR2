# Archive — quarantined dead modules

**Quarantined:** 2026-09-29 (build-out wave w50, P1 backlog item 4).
**Rule:** nothing here is imported by any live code, test, script, or packaging entry point.
Everything below was moved (not deleted) from its original location after a repo-wide
re-verification of zero importers on base `fd96801` (import statements, `from`-imports,
`__import__`/`importlib` dynamic references, `__init__` re-exports, test imports,
packaging entry points, start commands, and CI paths were all checked).

**Why quarantine instead of delete:** per program precedent, dead code is quarantined so
nothing is lost and anything can be restored deliberately. No runtime behavior changed:
none of these modules had a single importer, so no live path can observe their move.

## Grandfathered (stayed in place — see notes)

| Module | Why it stayed |
|---|---|
| `src/sincor2/chroma_app.py` | Has live callers: `verticals/auto_detailing/tests/test_dashboard.py` imports `create_chroma_app` from it (3 references). Not dead. |
| `src/sincor2/wsgi.py` | Trivial alias (`from railway_start import app, wsgi`); conventional deployment entry point. Harmless; left as the grandfathered alias. |

## Quarantined modules

| Archived file | Original path | What it was | Why quarantined |
|---|---|---|---|
| `adversarial_resilience.py` | `src/sincor2/adversarial_resilience.py` | Adversarial resilience / game-theory module (anti-collusion, anti-sybil in bidding/barter). | Zero importers repo-wide. |
| `agent_schema.py` | `src/sincor2/agent_schema.py` | Pydantic schemas for validating SINCOR2 agent YAML configs. | Zero importers repo-wide. |
| `bidding_engine.py` | `src/sincor2/bidding_engine.py` | Isolated contract-net auction logic (`BiddingEngine` / `run_auction`). | Zero callers; `marketplace/contract_net/` is the canonical sealed-bid money path. Doc references updated to point here. |
| `check_status.py` | `src/sincor2/check_status.py` | Platform status-check helpers. | Zero importers repo-wide. |
| `hitl_protocol.py` | `src/sincor2/hitl_protocol.py` | Human-in-the-loop escalation protocols as a design pattern. | Zero importers repo-wide. |
| `interop_negotiation.py` | `src/sincor2/interop_negotiation.py` | Protocol-negotiation layer (A2A transport selection at runtime). | Zero importers repo-wide. |
| `lifecycle_system.py` | `src/sincor2/lifecycle_system.py` | Lifecycle/rhythm management (health rhythms, shift budgets, mandatory downtime). | Zero importers repo-wide. |
| `membership.py` | `src/sincor2/membership.py` | Session membership helpers for paid-shell templates (`PAID_PLANS`). | Zero importers repo-wide. |
| `meta_optimizer.py` | `src/sincor2/meta_optimizer.py` | Meta-marketplace optimizer over barter/coalition signals. | Zero importers repo-wide. |
| `security_lockdown.py` | `src/sincor2/security_lockdown.py` | Security lockdown system (protects critical files from unauthorized modification). Not the same as `scripts/ops/` below. | Zero importers repo-wide. |
| `sinc_payment_verifier.py` | `src/sincor2/sinc_payment_verifier.py` | Orphaned SINC twin of the wired AXM `payment_verifier.py` (on-chain SINC transfer validation). The AXM `payment_verifier.py` is live and was NOT touched. | Zero importers repo-wide. |
| `startup.py` | `src/sincor2/startup.py` | Boot/logging configuration helpers (`configure_logging`, request-id factory). | Zero importers repo-wide. CI lint path (`.github/workflows/ci.yml`) and `CONTRIBUTING.md` lint command updated to drop the moved path. |
| `unified_content_engine.py` | `src/sincor2/unified_content_engine.py` | Unified content-package generation engine. | Zero importers. See quarantine-effect note below re: `order_fulfillment.py`. |
| `zk_privacy_layer.py` | `src/sincor2/zk_privacy_layer.py` | Zero-knowledge privacy layer for A2A task-completion proofs. | Zero importers repo-wide. |
| `security_lockdown_ops.py` | `scripts/ops/security_lockdown.py` | Ops checksum-verification script ("validate file checksums before commit"). Renamed on archive to avoid colliding with the `src/sincor2` one. | Zero importers/references repo-wide. |
| `barter_engine.py` | `marketplace/barter_engine.py` | A2A barter / non-monetary exchange engine (compute-for-data etc.). | Zero importers repo-wide. |

## Quarantine-effect notes

- **`order_fulfillment.py` (G5.11):** `src/sincor2/order_fulfillment.py` contains two
  `try/except`-wrapped bare imports of this module (`from unified_content_engine import ...`
  at lines 16–17 and 282). The bare top-level form could never resolve
  (the module lived only at `sincor2.unified_content_engine`), so the except branch
  (`CONTENT_ENGINE_AVAILABLE = False`) was already the only reachable behavior —
  including on the live path via `app.py`'s `from sincor2.order_fulfillment import
  fulfillment_system`. Quarantine preserves that behavior exactly; no load-bearing
  import was broken. The proper import repair (package-qualified path or removal)
  belongs to P1 backlog item 5 (mislabeled files), which owns `order_fulfillment.py:16-17`.
  Note: restoring this module to `src/sincor2/` alone does NOT fix
  `order_fulfillment.py`'s import — it needs the package-qualified form.
- **`startup.py`:** only non-code references were the ruff file list in
  `.github/workflows/ci.yml` and `CONTRIBUTING.md`; both were updated to drop the
  moved path. No import depended on it.
- **`bidding_engine.py`:** doc references in `docs/ops/AUCTION_PATHS.md` and
  `marketplace/README.md` were repointed to `archive/bidding_engine.py`.
  The ghost-canonical docstring claim in `marketplace/contract_net/engine.py:3`
  ("does not replace `sincor2.bidding_engine.BiddingEngine.run_auction`") is
  backlog item 5's fix, not this wave's.

## Restore instructions

To restore a module: `git mv archive/<file> <original-path>` (see the table above),
then re-run the repo-wide importer check and the neighboring test suites. If the
module was repointed in docs/CI (startup, bidding_engine), restore those references
too. Restores should land via a reviewed, human-gated PR like any other change.
