#!/usr/bin/env python3
"""
SharedLiquidityVault Client

Read-only interface to the on-chain SharedLiquidityVault (Base).
Mirrors the contract's availableDraw accounting exactly so off-chain agents
can size drawdown proposals that will not revert on-chain.

- On-chain mode: eth_call via web3.py (read-only; never signs from here).
- Sim mode: injected VaultState — used by simulation_engine/tests.
- Encodes paste-ready calldata for drawDown / settleUp. Execution always
  happens through the registered strategy hook, not this client.

Contract: 0xeA90a257e5Dae20a0472C4812775F28614459bb6 (Base, verified)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

VAULT_ADDRESS = "0xeA90a257e5Dae20a0472C4812775F28614459bb6"
BASE_RPC_DEFAULT = "https://base-rpc.publicnode.com"
BPS_DENOMINATOR = 10_000

# Minimal ABI — view surface + the two strategy-hook entrypoints.
VAULT_ABI = [
    {
        "name": "availableDraw",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "strategyId", "type": "uint256"},
            {"name": "lp", "type": "address"},
            {"name": "token", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "strategies",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "uint256"}],
        "outputs": [
            {"name": "hook", "type": "address"},
            {"name": "defaultBacker", "type": "address"},
            {"name": "capSINC", "type": "uint256"},
            {"name": "capUSDC", "type": "uint256"},
            {"name": "active", "type": "bool"},
        ],
    },
    {
        "name": "virtualAlloc",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "lp", "type": "address"},
            {"name": "strategyId", "type": "uint256"},
            {"name": "token", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "outstanding",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "lp", "type": "address"},
            {"name": "strategyId", "type": "uint256"},
            {"name": "token", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "strategyOutstanding",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "strategyId", "type": "uint256"},
            {"name": "token", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "checkInvariant",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "drawDown",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "strategyId", "type": "uint256"},
            {"name": "lp", "type": "address"},
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "settleUp",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "strategyId", "type": "uint256"},
            {"name": "lp", "type": "address"},
            {"name": "token", "type": "address"},
            {"name": "principal", "type": "uint256"},
            {"name": "fee", "type": "uint256"},
            {"name": "protocolFeeBps", "type": "uint256"},
        ],
        "outputs": [],
    },
]


@dataclass
class VaultState:
    """Off-chain snapshot of one LP/strategy/token lane in the vault.

    Amounts are human units (e.g. USDC dollars), not raw uint256.
    Mirrors SharedLiquidityVault accounting exactly.
    """

    strategy_id: int
    lp: str
    token: str
    virtual_alloc: float          # virtualAlloc[lp][strategyId][token]
    outstanding: float            # outstanding[lp][strategyId][token]
    strategy_outstanding: float   # strategyOutstanding[strategyId][token]
    cap: float                    # capSINC or capUSDC (0 = uncapped)
    real_balance: float           # realBalance[lp][token]
    lp_outstanding_total: float   # lpOutstandingTotal[lp][token]
    token_decimals: int = 6

    def available_draw(self) -> float:
        """Exact mirror of SharedLiquidityVault.availableDraw."""
        commit_left = self.virtual_alloc - self.outstanding
        free_real = self.real_balance - self.lp_outstanding_total
        head = min(commit_left, free_real)
        if self.cap != 0:
            cap_left = self.cap - self.strategy_outstanding
            head = min(head, cap_left)
        return max(head, 0.0)

    def to_raw(self, amount: float) -> int:
        return int(amount * (10 ** self.token_decimals))


class VaultClient:
    """Read-only vault gateway for agents.

    Pass a web3 RPC URL for live reads, or a state_provider callable for
    simulation/tests: state_provider(strategy_id, lp, token) -> VaultState.
    """

    def __init__(
        self,
        vault_address: str = VAULT_ADDRESS,
        rpc_url: Optional[str] = None,
        state_provider: Optional[Callable[[int, str, str], VaultState]] = None,
    ):
        self.vault_address = vault_address
        self._state_provider = state_provider
        self._w3 = None
        self._contract = None
        if rpc_url and state_provider is None:
            try:
                from web3 import Web3  # optional dependency, read-only use
            except ImportError:
                logger.warning("web3 not installed — VaultClient in degraded mode")
            else:
                self._w3 = Web3(Web3.HTTPProvider(rpc_url))
                if self._w3.is_connected():
                    self._contract = self._w3.eth.contract(
                        address=Web3.to_checksum_address(vault_address),
                        abi=VAULT_ABI,
                    )
                else:
                    logger.warning("RPC unreachable — VaultClient in degraded mode")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_state(
        self,
        strategy_id: int,
        lp: str,
        token: str,
        sinc_address: Optional[str] = None,
        token_decimals: int = 6,
    ) -> VaultState:
        """Fetch one lane's full accounting state (on-chain or injected)."""
        if self._state_provider is not None:
            return self._state_provider(strategy_id, lp, token)
        if self._contract is None:
            raise RuntimeError("VaultClient has no live connection or state provider")

        c = self._contract.functions
        strat = c.strategies(strategy_id).call()
        is_sinc = sinc_address is not None and token.lower() == sinc_address.lower()
        return VaultState(
            strategy_id=strategy_id,
            lp=lp,
            token=token,
            virtual_alloc=c.virtualAlloc(lp, strategy_id, token).call() / 10**token_decimals,
            outstanding=c.outstanding(lp, strategy_id, token).call() / 10**token_decimals,
            strategy_outstanding=c.strategyOutstanding(strategy_id, token).call() / 10**token_decimals,
            cap=(strat[2] if is_sinc else strat[3]) / 10**token_decimals,
            real_balance=0.0,  # fill via realBalance call if needed by caller
            lp_outstanding_total=0.0,
            token_decimals=token_decimals,
        )

    def available_draw(self, state: VaultState) -> float:
        if self._contract is not None:
            raw = self._contract.functions.availableDraw(
                state.strategy_id, state.lp, state.token
            ).call()
            return raw / 10**state.token_decimals
        return state.available_draw()

    def check_invariant(self) -> Optional[bool]:
        """On-chain solvency invariant: assets >= deposits + fee claims."""
        if self._contract is None:
            return None
        return bool(self._contract.functions.checkInvariant().call())

    # ------------------------------------------------------------------
    # Calldata encoding (execution stays with the strategy hook)
    # ------------------------------------------------------------------
    def encode_draw_down(self, state: VaultState, amount: float) -> str:
        return self._encode("drawDown", state, state.to_raw(amount))

    def encode_settle_up(
        self, state: VaultState, principal: float, fee: float, protocol_fee_bps: int
    ) -> str:
        return self._encode(
            "settleUp", state, state.to_raw(principal), state.to_raw(fee), protocol_fee_bps
        )

    def _encode(self, fn: str, state: VaultState, *amount_args) -> str:
        if self._contract is not None:
            call = getattr(self._contract.functions, fn)(
                state.strategy_id, state.lp, state.token, *amount_args
            )
            return call._encode_transaction_data()
        # Offline fallback: selector + raw ABI words (no web3 needed).
        return _offline_encode(fn, state, *amount_args)


# keccak256 selectors, precomputed — zero-dependency offline encoding.
_SELECTORS = {
    "drawDown": "0ca63fb1",   # drawDown(uint256,address,address,uint256)
    "settleUp": "d77225c8",   # settleUp(uint256,address,address,uint256,uint256,uint256)
}


def _offline_encode(fn: str, state: VaultState, *amount_args) -> str:
    """Deterministic ABI encoding without web3 (sim/CI environments)."""
    selector = _SELECTORS[fn]
    args = [state.strategy_id, state.lp, state.token, *amount_args]
    words = []
    for a in args:
        if isinstance(a, str):
            words.append(a.lower().replace("0x", "").rjust(64, "0"))
        else:
            words.append(int(a).to_bytes(32, "big").hex())
    return "0x" + selector + "".join(words)
