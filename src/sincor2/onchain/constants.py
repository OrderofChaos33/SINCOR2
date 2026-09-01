"""Single source of truth for Base (8453) token and infrastructure addresses.

Every runtime module must import from here. Environment variables may override
a live address, but a stale override is ignored so forgotten .env files cannot
silently settle against a retired contract.

This file must stay in lockstep with ``CANONICAL_ADDRESSES.md``.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, FrozenSet, Mapping

logger = logging.getLogger("sincor.onchain")

BASE_CHAIN_ID = 8453
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# --- Live, verified (2026-08-19) -------------------------------------------
AXIOM_TOKEN = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
SINC_TOKEN = "0xe1D836087F6573b665d25CE088793E916D7892f8"
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
USDC_TOKEN = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# Retired — never a buy path. Kept so leftover imports fail closed via STALE_ADDRESSES.
RETIRED_BONDING_CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
BONDING_CURVE = RETIRED_BONDING_CURVE
LIMIT_ORDER_HOOK = "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0"
GENESIS_NFT = "0xF3Bd56788b5E56DE638AF5dDffFA478838A68d09"
POOL_MANAGER = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
POSITION_MANAGER = "0x7C5f5A4bBd8fD63184577525326123B519429bDc"
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"

AXM_SYMBOL = "AXM"
AXM_DECIMALS = 18
SINC_SYMBOL = "SINC"
SINC_DECIMALS = 8
USDC_SYMBOL = "USDC"
USDC_DECIMALS = 6

# Retired / wrong — never use for new quotes, settlement, or billing.
STALE_ADDRESSES: FrozenSet[str] = frozenset(
    addr.lower()
    for addr in (
        "0x9C8cd8d3961F445D653713dE65C6578bE11668e7",  # previous SINC (retired 2026-08-19)
        "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822",  # dead PumpClawToken labeled AXM
        "0x75dE341a2BC81806198364F125d4Cde36527619C",  # retired bonding curve
        "0xb627F53E08AD7d455e787d052C18D6877020E2BF",  # old bonding curve
        "0x25cA41Dac29f892c72A53500853eC45a5FfF90aa",  # superseded bonding curve
        "0x49E392de962Fa835B862F59E78611c69E930b5C4",  # dead-liquidity v2 SINC
        "0xAf9B539D8043C634b7E611818518BA7E850F289e",  # legacy treasury
    )
)

_STALE_REASON: Dict[str, str] = {
    "0x9c8cd8d3961f445d653713de65c6578be11668e7": "retired SINC (2026-08-19)",
    "0xff7af6ffca25a9dc0fc990d998acf24cc60b7822": "dead PumpClawToken labeled AXM",
    "0x75de341a2bc81806198364f125d4cde36527619c": "retired bonding curve",
    "0xb627f53e08ad7d455e787d052c18d6877020e2bf": "old bonding curve",
    "0x25ca41dac29f892c72a53500853ec45a5fff90aa": "superseded bonding curve",
    "0x49e392de962fa835b862f59e78611c69e930b5c4": "dead-liquidity v2 SINC",
    "0xaf9b539d8043c634b7e611818518ba7e850f289e": "legacy treasury",
}


def is_address(value: str) -> bool:
    return bool(value) and bool(ADDRESS_RE.match(value))

def is_stale(value: str) -> bool:
    return bool(value) and value.lower() in STALE_ADDRESSES


def stale_reason(value: str) -> str:
    return _STALE_REASON.get(value.lower(), "retired")


def resolve_address(env_key: str, canonical: str) -> str:
    """Env override, but stale/malformed values fall back to canonical."""
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return canonical
    if not is_address(raw):
        logger.warning("Ignoring malformed %s=%r; using canonical %s", env_key, raw, canonical)
        return canonical
    if is_stale(raw):
        logger.warning(
            "Ignoring stale %s=%s (%s); using canonical %s",
            env_key,
            raw,
            stale_reason(raw),
            canonical,
        )
        return canonical
    return raw


def catalog() -> Dict[str, Mapping[str, object]]:
    """Operator-facing snapshot of live pointers."""
    return {
        "axiom": {
            "symbol": AXM_SYMBOL,
            "decimals": AXM_DECIMALS,
            "address": resolve_address("AXIOM_CONTRACT_ADDRESS", AXIOM_TOKEN),
            "canonical": AXIOM_TOKEN,
            "role": "primary A2A settlement",
        },
        "sinc": {
            "symbol": SINC_SYMBOL,
            "decimals": SINC_DECIMALS,
            "address": resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN),
            "canonical": SINC_TOKEN,
            "role": "legacy residual holders",
        },
        "treasury": {
            "symbol": "TREASURY",
            "decimals": 0,
            "address": resolve_address("TREASURY_ADDRESS", TREASURY),
            "canonical": TREASURY,
            "role": "fees and A2A routing",
        },
        "usdc": {
            "symbol": USDC_SYMBOL,
            "decimals": USDC_DECIMALS,
            "address": resolve_address("USDC_CONTRACT_ADDRESS", USDC_TOKEN),
            "canonical": USDC_TOKEN,
            "role": "stable routing",
        },
    }


def assert_live_not_stale() -> None:
    """Hard fail if any resolved live pointer equals a retired address."""
    for name, row in catalog().items():
        addr = str(row["address"])
        if is_stale(addr):
            raise RuntimeError(
                f"Live {name} pointer {addr} is stale ({stale_reason(addr)}). "
                "Update src/sincor2/onchain/constants.py."
            )
        if not is_address(addr):
            raise RuntimeError(f"Live {name} pointer {addr!r} is not a 20-byte address")
