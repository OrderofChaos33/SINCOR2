"""P22 venue adapters: Morpho (ERC-4626) and Aave V3 (IPool) on Base.

Adapters quote net APR and build *unsigned* supply/withdraw transaction
payloads. They never sign and never broadcast. Net APR = venue supply APR
minus the curator performance fee (Steakhouse 25%, Moonwell 15%, Gauntlet 0%).

Calldata is built with real ABI encoding for the static types used
(uint256, address, uint16); selectors are keccak of the canonical signatures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict

from eth_hash.auto import keccak

from . import PARAMS
from .gate import assert_stable
from .interfaces import PoolQuote

BASE_CHAIN_ID = 8453


def selector(signature: str) -> bytes:
    """First 4 bytes of keccak of the canonical function signature."""
    return keccak(signature.encode("ascii"))[:4]


def _enc_uint256(value: int) -> bytes:
    if value < 0 or value >= 2 ** 256:
        raise ValueError("uint256 out of range")
    return value.to_bytes(32, "big")


def _enc_address(value: str) -> bytes:
    h = value.lower().removeprefix("0x")
    if len(h) != 40 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"bad address: {value!r}")
    return bytes(12) + bytes.fromhex(h)


def _enc_uint16(value: int) -> bytes:
    if value < 0 or value >= 2 ** 16:
        raise ValueError("uint16 out of range")
    return value.to_bytes(32, "big")


# Canonical selectors (recomputed at import; tests assert them explicitly).
SEL_DEPOSIT = selector("deposit(uint256,address)")
SEL_REDEEM = selector("redeem(uint256,address,address)")
SEL_SUPPLY = selector("supply(address,uint256,address,uint16)")
SEL_WITHDRAW = selector("withdraw(address,uint256,address)")

# Sentinel for "address not yet resolved from the venue registry". Any
# tx-building against an unresolved venue raises instead of emitting a
# transaction to the zero address.
UNRESOLVED = "0x" + "00" * 20


def _require_resolved(config: "VenueConfig") -> None:
    if config.vault_or_pool.lower() == UNRESOLVED:
        raise ValueError(
            f"venue {config.venue_id}: vault/pool address unresolved — "
            "resolve from the venue registry before building transactions"
        )


@dataclass
class VenueConfig:
    venue_id: str
    asset: str
    vault_or_pool: str       # ERC-4626 vault (Morpho) or IPool (Aave V3)
    curator_fee_bps: int     # performance fee priced into net APR
    is_morpho: bool
    depth_usd: float         # last observed available liquidity


class BaseVenueAdapter:
    """Shared quote/tx-building logic behind the StableVenueAdapter protocol."""

    venue_id: str = ""
    asset: str = ""

    def __init__(self, config: VenueConfig, supply_apr: float = 0.0) -> None:
        # Gate at the adapter entrypoint: construction with a bad asset fails.
        assert_stable(config.asset, BASE_CHAIN_ID, config.venue_id, "adapter")
        self.config = config
        self.venue_id = config.venue_id
        self.asset = config.asset
        self._supply_apr = supply_apr

    # -- quoting ---------------------------------------------------------
    def set_supply_apr(self, apr: float) -> None:
        self._supply_apr = apr

    def net_apr(self) -> float:
        return self._supply_apr * (1.0 - self.config.curator_fee_bps / 10_000)

    def quote(self) -> PoolQuote:
        assert_stable(self.asset, BASE_CHAIN_ID, self.venue_id, "adapter")
        return PoolQuote(
            venue_id=self.venue_id,
            asset=self.asset,
            chain_id=BASE_CHAIN_ID,
            net_apr=self.net_apr(),
            depth_usd=self.config.depth_usd,
            ts=time.time(),
            is_morpho=self.config.is_morpho,
        )


class MorphoAdapter(BaseVenueAdapter):
    """ERC-4626 vault adapter (Morpho Blue / MetaMorpho vaults on Base)."""

    def build_supply_tx(self, amount_wei: int, receiver: str) -> Dict:
        assert_stable(self.asset, BASE_CHAIN_ID, self.venue_id, "adapter")
        _require_resolved(self.config)
        data = SEL_DEPOSIT + _enc_uint256(amount_wei) + _enc_address(receiver)
        return {
            "to": self.config.vault_or_pool,
            "chain_id": BASE_CHAIN_ID,
            "data": "0x" + data.hex(),
            "function": "deposit(uint256,address)",
            "value": 0,
        }

    def build_withdraw_tx(self, shares_wei: int, receiver: str) -> Dict:
        assert_stable(self.asset, BASE_CHAIN_ID, self.venue_id, "adapter")
        _require_resolved(self.config)
        data = (SEL_REDEEM + _enc_uint256(shares_wei)
                + _enc_address(receiver) + _enc_address(receiver))
        return {
            "to": self.config.vault_or_pool,
            "chain_id": BASE_CHAIN_ID,
            "data": "0x" + data.hex(),
            "function": "redeem(uint256,address,address)",
            "value": 0,
        }


class AaveV3Adapter(BaseVenueAdapter):
    """Aave V3 IPool adapter (Base). Secondary venue, same interface."""

    def build_supply_tx(self, amount_wei: int, receiver: str) -> Dict:
        assert_stable(self.asset, BASE_CHAIN_ID, self.venue_id, "adapter")
        _require_resolved(self.config)
        # supply(asset, amount, onBehalfOf, referralCode)
        data = (SEL_SUPPLY + _enc_address(self._asset_address())
                + _enc_uint256(amount_wei) + _enc_address(receiver)
                + _enc_uint16(0))
        return {
            "to": self.config.vault_or_pool,
            "chain_id": BASE_CHAIN_ID,
            "data": "0x" + data.hex(),
            "function": "supply(address,uint256,address,uint16)",
            "value": 0,
        }

    def build_withdraw_tx(self, shares_wei: int, receiver: str) -> Dict:
        # Aave withdraw is in underlying asset units, not shares; the caller
        # passes the underlying amount in `shares_wei`.
        assert_stable(self.asset, BASE_CHAIN_ID, self.venue_id, "adapter")
        _require_resolved(self.config)
        data = (SEL_WITHDRAW + _enc_address(self._asset_address())
                + _enc_uint256(shares_wei) + _enc_address(receiver))
        return {
            "to": self.config.vault_or_pool,
            "chain_id": BASE_CHAIN_ID,
            "data": "0x" + data.hex(),
            "function": "withdraw(address,uint256,address)",
            "value": 0,
        }

    def _asset_address(self) -> str:
        # Only USDC has a pinned onchain address here (verified Base USDC).
        # USDT quotes are symbol-gated; its address is resolved at deploy
        # time from the venue registry, never hardcoded from memory.
        if self.asset == "USDC":
            return "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        raise ValueError(
            f"no pinned onchain asset address for {self.asset}; refusing to guess"
        )


def default_venue_configs() -> Dict[str, VenueConfig]:
    """The frozen venue allowlist (spec: additions need amendment + re-audit).

    Vault/pool addresses are resolved at deploy from the venue registry;
    placeholder zeros below mark venues whose addresses are unresolved: quoting
    works, but build_*_tx raises UNRESOLVED until deploy resolution.
    """
    return {
        "morpho-gauntlet-usdc": VenueConfig(
            "morpho-gauntlet-usdc", "USDC", UNRESOLVED, 0, True, 50_000_000.0),
        "morpho-steakhouse-usdc": VenueConfig(
            "morpho-steakhouse-usdc", "USDC", UNRESOLVED, 2500, True, 20_000_000.0),
        "morpho-moonwell-usdc": VenueConfig(
            "morpho-moonwell-usdc", "USDC", UNRESOLVED, 1500, True, 15_000_000.0),
        "aave-v3-usdc": VenueConfig(
            "aave-v3-usdc", "USDC", UNRESOLVED, 0, False, 80_000_000.0),
        "aave-v3-usdt": VenueConfig(
            "aave-v3-usdt", "USDT", UNRESOLVED, 0, False, 30_000_000.0),
    }
