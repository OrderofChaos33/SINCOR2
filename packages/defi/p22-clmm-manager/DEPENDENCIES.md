# Dependency Inventory — P22 Concentrated Liquidity Manager

PACK-1 inventory of every import in the extracted core module
(`src/p22_clmm_manager/clmm_manager.py`, copied from the platform file
`src/sincor2/defi/clmm_manager.py` at SINCOR2 commit `5d20c8fa`).

## Runtime dependencies

### Standard library only

| Import | Kind | Used for |
|---|---|---|
| `__future__.annotations` | stdlib | postponed annotation evaluation |
| `hashlib` | stdlib | `commitment_id` (SHA-256) |
| `logging` | stdlib | non-bricking fee-math failure logging |
| `math` | stdlib | realized-volatility computation (`log`, `sqrt`) |
| `os` | stdlib | environment-variable config surface (see deploy/DEPLOY.md) |
| `time` | stdlib | intent timestamps |
| `collections.defaultdict` | stdlib | event/role aggregation |
| `dataclasses` (`asdict`, `dataclass`, `field`) | stdlib | state/event records |
| `typing` (`Any`, `Dict`, `List`, `Optional`, `Tuple`) | stdlib | annotations |
| `decimal.Decimal` | stdlib (lazy, inside `_jit_sensitivity_bps`) | exact parsing of the legacy `CLMM_JIT_SENSITIVITY` value — deliberately never via float |

### Third-party

None. There are no third-party runtime dependencies to pin.

### Platform-internal

One reach-in existed in the platform original and was removed:

| Platform dependency | How it was used | PACK-1 disposition |
|---|---|---|
| `sincor2.defi.price_oracle.wiring_for` | Lazy import inside `price_feed_for()` only; binds the shared price oracle to this product's declared assets (`P02_CLMM`: `ETH/USD`, `USDC/USD`) | Vendored as a minimal stub in `src/p22_clmm_manager/price_oracle_stub.py`; the extracted copy's single lazy import was redirected to the stub. The stub fetches no prices and wires no feed — a real oracle adapter is injected by the deployer inside a signed engagement. Full record in PACKAGING.md. |

No other platform imports exist: the platform original imported nothing
else from the SINCOR2 tree (verified by import scan of the source file).

## Added by this package (not part of the extracted core)

| File | Imports | Notes |
|---|---|---|
| `src/p22_clmm_manager/service_fees.py` | stdlib only (`dataclasses`, `typing`) | Engagement-level service-fee accounting (15% of earned LP trading fees). New in PACK-2; not platform code. |
| `src/p22_clmm_manager/price_oracle_stub.py` | stdlib only (`typing`) | Vendored minimal oracle surface, above. |
| `src/p22_clmm_manager/__init__.py` | package-relative only | Re-exports the public surface. |

## Test dependencies

| Dependency | Pin |
|---|---|
| `pytest` | `>=8` (declared in `pyproject.toml` optional `test` extra; the packaging run used the SINCOR2 test venv's pytest 9.1.1) |
