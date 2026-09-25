"""Canonical Base addresses and on-chain probes.

Import from here. Do not copy token literals into other modules.
Human index: ``CANONICAL_ADDRESSES.md`` (must match this package).
"""

from .constants import (
    AXIOM_TOKEN,
    AXM_DECIMALS,
    AXM_SYMBOL,
    BASE_CHAIN_ID,
    BONDING_CURVE,
    DEAD_ADDRESS,
    GENESIS_NFT,
    LIMIT_ORDER_HOOK,
    POOL_MANAGER,
    POSITION_MANAGER,
    SINC_DECIMALS,
    SINC_SYMBOL,
    SINC_TOKEN,
    STALE_ADDRESSES,
    TREASURY,
    USDC_DECIMALS,
    USDC_SYMBOL,
    USDC_TOKEN,
    catalog,
    is_stale,
    resolve_address,
)
# NOTE (2026-09-25): probe / epoch_commitment_pipeline / live_snapshot pull in
# heavy deps (numpy, the sinax ML stack). They are lazy-loaded via __getattr__
# so that `import sincor2.onchain` (or `marketplace`, which imports its
# constants) stays light. Accessing any of these names imports the submodule
# on first use.
_LAZY_EXPORTS = {
    "TokenProbe": (".probe", "TokenProbe"),
    "TokenProbeReport": (".probe", "TokenProbeReport"),
    "validate_at_startup": (".probe", "validate_at_startup"),
    "EpochCommitmentEnvelope": (".epoch_commitment_pipeline", "EpochCommitmentEnvelope"),
    "EpochStateCommitmentPipeline": (".epoch_commitment_pipeline", "EpochStateCommitmentPipeline"),
    "attach_official_price_fields": (".live_snapshot", "attach_official_price_fields"),
    "fetch_live_onchain": (".live_snapshot", "fetch_live_onchain"),
}


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        import importlib

        module_name, attr = _LAZY_EXPORTS[name]
        module = importlib.import_module(module_name, __name__)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    # Backward compat: submodule attribute access (e.g. `onchain.probe`
    # after a plain `import sincor2.onchain`) keeps working.
    if name in ("probe", "epoch_commitment_pipeline", "live_snapshot"):
        import importlib

        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AXIOM_TOKEN",
    "AXM_DECIMALS",
    "AXM_SYMBOL",
    "BASE_CHAIN_ID",
    "BONDING_CURVE",
    "DEAD_ADDRESS",
    "GENESIS_NFT",
    "LIMIT_ORDER_HOOK",
    "POOL_MANAGER",
    "POSITION_MANAGER",
    "SINC_DECIMALS",
    "SINC_SYMBOL",
    "SINC_TOKEN",
    "STALE_ADDRESSES",
    "TREASURY",
    "USDC_DECIMALS",
    "USDC_SYMBOL",
    "USDC_TOKEN",
    "TokenProbe",
    "TokenProbeReport",
    "catalog",
    "is_stale",
    "resolve_address",
    "validate_at_startup",
    "EpochCommitmentEnvelope",
    "EpochStateCommitmentPipeline",
    "attach_official_price_fields",
    "fetch_live_onchain",
]
