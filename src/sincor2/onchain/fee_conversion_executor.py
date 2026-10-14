"""Fee-conversion planning and offline simulation for AXM/SINC fee obligations.

The live conversion pipeline below is design intent only and is not executable
in this repository. Live execution is rejected unconditionally; no production
signer, key-custody integration, or canonical authorization capability exists.

Proposed pipeline (not currently enabled)
-----------------------------------------
pending obligation (recorded by ``record_axm_receipt`` / settle-proof builders)
  -> quote via the Uniswap V4 Quoter (read-only)
  -> approve Permit2 to pull the fee token (if allowance is short)
  -> swap exact-input through the V4-capable Universal Router
  -> forward the USDC/WETH output to the treasury
  -> ledger marks the obligation executed (or failed, with the reason)

Security rules (same posture as ``onchain/auction_client.py``)
---------------------------------------------------------------
* This module NEVER reads private keys. The legacy signer callback is
  unreachable for live execution: no canonical release/approval contract or
  owner-approved live capability exists. ``armed`` is not authorization.
* All token / infra addresses come from ``sincor2.onchain.constants`` or are
  verified Base deployments named below with their source.
* Pool keys are operator-pinned config (see ``discover_pool``).  The executor
  never guesses a pool.
* Every state-changing step is preceded by an ``eth_call`` simulation; a
  revert fails the plan closed instead of broadcasting.
* Swaps are exact-input with an explicit ``amountOutMinimum`` derived from
  the live quote minus ``slippage_bps``.  Dust below ``min_swap_wei`` stays
  queued in the ledger instead of being swapped at a loss to gas.
* Live broadcasting is denied unconditionally.  Planning, read-only quoting,
  unsigned transaction construction, and dry-run simulation remain available.

Fee-policy intent (recorded 2026-09-26; conversion not live or authorized): 5 %
platform fee, 100 % to treasury, desired conversion to USDC/WETH before deposit,
no burn. Deflationary mechanics are deferred to a later governance decision.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from eth_abi import encode as abi_encode
from eth_hash.auto import keccak

from sincor2.onchain.constants import (
    AXIOM_TOKEN,
    SINC_TOKEN,
    TREASURY,
    USDC_TOKEN,
    AXM_DECIMALS,
    SINC_DECIMALS,
    USDC_DECIMALS,
)

logger = logging.getLogger("sincor.treasury.fee_executor")

# --- Verified Base (8453) deployments ---------------------------------------
# V4Quoter — matches docs/superpowers/plans/2026-05-17-sinc-smart-contracts.md
# and the Uniswap v4 deployment sheets (Dedaub + clawd reference, 2026-07).
V4_QUOTER = "0x0d5e0f971ed27fbff6c2837bf31316121532048d"
# Universal Router, canonical V4 build (24_546 B runtime, same on every chain).
# Older V4-aware build 0x6fF5693b99212Da76ad316178A184AB56D299b43 is still live
# and is kept as the configured fallback.
UNIVERSAL_ROUTER_V4 = "0xfdf682f51fE81aA4898F0aE2163D8a55C127fbc7"
UNIVERSAL_ROUTER_V4_FALLBACK = "0x6fF5693b99212Da76ad316178A184AB56D299b43"
# Permit2 — canonical on every chain.
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
# WETH — OP-Stack predeploy on Base.
WETH_TOKEN = "0x4200000000000000000000000000000000000006"
WETH_DECIMALS = 18

BASE_CHAIN_ID = 8453

# --- Verified Base Sepolia (84532) deployments --------------------------------
# V4 stack on Base Sepolia lives at DIFFERENT addresses than Base mainnet
# (non-canonical deployment).  Presence verified on-chain 2026-10-14
# (eth_getCode via https://sepolia.base.org):
#   PoolManager  0x05E73354cFDd6745C338b50BcFDfA3Aa6fA03408  (24009 bytes)
#   V4Quoter     0x4a6513c898fe1B2d0E78d3b0e0A4a151589B1cBa  (5820 bytes)
#   UniversalRouter 0x492E6456D9528771018DeB9E87ef7750EF184104 (19540 bytes)
#   Permit2      0x000000000022D473030F116dDEE9F6B43aC78BA3  (canonical, 9152 bytes)
#   WETH         0x4200000000000000000000000000000000000006  (OP-Stack predeploy)
#   USDC         0x036CbD53842c5426634e7929541eC2318f3dCF7e  (Circle testnet USDC)
# NOTE: the AXM/SINC fee tokens are NOT deployed on Base Sepolia.  Sepolia
# dry-runs use a control route (WETH->USDC); the AXM leg cannot be quoted on
# Sepolia and must honestly report NO_POOL there.
SEPOLIA_CHAIN_ID = 84532
SEPOLIA_POOL_MANAGER = "0x05E73354cFDd6745C338b50BcFDfA3Aa6fA03408"
SEPOLIA_V4_QUOTER = "0x4a6513c898fe1B2d0E78d3b0e0A4a151589B1cBa"
SEPOLIA_UNIVERSAL_ROUTER = "0x492E6456D9528771018DeB9E87ef7750EF184104"
SEPOLIA_PERMIT2 = PERMIT2  # canonical on every chain
SEPOLIA_WETH = WETH_TOKEN  # OP-Stack predeploy, same address
SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
SEPOLIA_TOKEN_ADDRESSES = {
    "WETH": SEPOLIA_WETH,
    "USDC": SEPOLIA_USDC,
}
SEPOLIA_RPC_URL = "https://sepolia.base.org"

# Universal Router command bytes (universal-router Commands.sol)
CMD_V4_SWAP = 0x10
CMD_SWEEP = 0x04
# V4 periphery action bytes (v4-periphery Actions.sol)
ACT_SWAP_EXACT_IN_SINGLE = 0x06
ACT_SETTLE_ALL = 0x0C
ACT_TAKE_ALL = 0x0F

# ERC-20 selectors
SEL_APPROVE = keccak(b"approve(address,uint256)")[:4]
SEL_TRANSFER = keccak(b"transfer(address,uint256)")[:4]
SEL_ALLOWANCE = keccak(b"allowance(address,address)")[:4]
SEL_BALANCE_OF = keccak(b"balanceOf(address)")[:4]
# Permit2 (AllowanceTransfer): approve(token, spender, amount, expiration) and
# allowance(owner, token, spender). The V4 settle path pulls the fee token via
# Permit2.transferFrom, which draws on this internal allowance — the ERC-20
# approve(Permit2) alone is NOT sufficient.
SEL_P2_APPROVE = keccak(b"approve(address,address,uint160,uint48)")[:4]
SEL_P2_ALLOWANCE = keccak(b"allowance(address,address,address)")[:4]
# V4Quoter.quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))
SEL_QUOTE_EXACT_IN_SINGLE = keccak(
    b"quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))"
)[:4]
# UniversalRouter.execute(bytes,bytes[],uint256)
SEL_UR_EXECUTE = keccak(b"execute(bytes,bytes[],uint256)")[:4]

TOKEN_DECIMALS = {
    "AXM": AXM_DECIMALS,
    "AXIOM": AXM_DECIMALS,
    "SINC": SINC_DECIMALS,
    "USDC": USDC_DECIMALS,
    "WETH": WETH_DECIMALS,
}
TOKEN_ADDRESSES = {
    "AXM": AXIOM_TOKEN,
    "AXIOM": AXIOM_TOKEN,
    "SINC": SINC_TOKEN,
    "USDC": USDC_TOKEN,
    "WETH": WETH_TOKEN,
}

# Candidate (fee, tickSpacing) pairs probed by discover_pool, most common first.
POOL_CANDIDATES: Tuple[Tuple[int, int], ...] = (
    (3000, 60),    # 0.30 %
    (500, 10),     # 0.05 %
    (10000, 200),  # 1.00 %
    (100, 1),      # 0.01 %
)
ZERO_HOOKS = "0x0000000000000000000000000000000000000000"


# --- address hygiene -------------------------------------------------------
# web3.py v8 validates EIP-55 checksums on every address in tx params before
# any network I/O, and several in-repo constants (V4_QUOTER,
# UNIVERSAL_ROUTER_V4, AXIOM_TOKEN) are not checksummed. Normalize at every
# boundary where an address enters an RPC call or an unsigned tx dict.

def _ck(addr: str) -> str:
    from web3 import Web3

    return Web3.to_checksum_address(addr)


# --- Pool keys ---------------------------------------------------------------

@dataclass(frozen=True)
class PoolKey:
    """Uniswap V4 PoolKey.  currency0 < currency1 by address value."""

    currency0: str
    currency1: str
    fee: int
    tick_spacing: int
    hooks: str = ZERO_HOOKS

    def as_tuple(self) -> Tuple[str, str, int, int, str]:
        return (self.currency0, self.currency1, self.fee, self.tick_spacing, self.hooks)

    @staticmethod
    def for_pair(token_a: str, token_b: str, fee: int, tick_spacing: int,
                 hooks: str = ZERO_HOOKS) -> "PoolKey":
        a, b = sorted((token_a.lower(), token_b.lower()))
        return PoolKey(currency0=a, currency1=b, fee=fee,
                       tick_spacing=tick_spacing, hooks=hooks)


def zero_for_one(pool: PoolKey, token_in: str) -> bool:
    return token_in.lower() == pool.currency0


# --- Config ------------------------------------------------------------------

def _default_fee_ledger_path() -> str:
    """Volume-aware default: Railway /data when mounted, else local data dir.

    The old default (~/workspace/ops/fee_conversion_ledger.json) lived on
    Railway's ephemeral filesystem, so redeploys wiped the conversion journal.
    """
    explicit = os.environ.get("SINCOR_FEE_LEDGER_PATH", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "fee_conversion_ledger.json")
    except Exception:
        return os.path.expanduser("~/workspace/ops/fee_conversion_ledger.json")


@dataclass
class FeeConversionConfig:
    """Operator config for one executor instance."""

    rpc_url: str
    forwarder: str                       # EOA that holds the fee tokens; signs swaps
    treasury: str = TREASURY
    quoter: str = V4_QUOTER
    universal_router: str = UNIVERSAL_ROUTER_V4
    permit2: str = PERMIT2
    slippage_bps: int = 100              # 1 % default
    tx_deadline_secs: int = 1200         # 20 min
    min_swap_wei: int = 0                # dust stays queued below this
    target: str = "USDC"                 # "USDC" | "WETH" (fee policy lists both)
    pool_keys: Dict[Tuple[str, str], PoolKey] = field(default_factory=dict)
    armed: bool = False                  # legacy compatibility; never authorizes live execution
    ledger_path: str = field(default_factory=_default_fee_ledger_path)
    # Multi-chain support.  Defaults preserve the historical Base-mainnet
    # behavior exactly; Sepolia dry-runs override these.
    chain_id: int = BASE_CHAIN_ID
    token_addresses: Optional[Dict[str, str]] = None  # None -> TOKEN_ADDRESSES

    def token_address(self, symbol: str) -> str:
        """Resolve a token symbol to its address on this config's chain."""
        addrs = self.token_addresses if self.token_addresses else TOKEN_ADDRESSES
        try:
            return addrs[symbol.upper()]
        except KeyError:
            raise ExecutorError(
                f"unknown token {symbol} on chain {self.chain_id}")

    @classmethod
    def sepolia_defaults(cls, forwarder: str,
                         treasury: str = TREASURY) -> "FeeConversionConfig":
        """Config pointed at the verified Base Sepolia V4 deployments.

        Read-only dry-runs only.  The fee tokens (AXM/SINC) do not exist on
        Sepolia; use a control route (WETH->USDC) for the dry-run.
        """
        return cls(
            rpc_url=SEPOLIA_RPC_URL,
            forwarder=forwarder,
            treasury=treasury,
            quoter=SEPOLIA_V4_QUOTER,
            universal_router=SEPOLIA_UNIVERSAL_ROUTER,
            permit2=SEPOLIA_PERMIT2,
            chain_id=SEPOLIA_CHAIN_ID,
            token_addresses=dict(SEPOLIA_TOKEN_ADDRESSES),
        )

    def pool_key_for(self, token_in: str, token_out: str) -> PoolKey:
        key = (token_in.upper(), token_out.upper())
        try:
            return self.pool_keys[key]
        except KeyError:
            raise MissingPoolKey(
                f"no pinned PoolKey for {token_in}->{token_out}; "
                f"run discover_pool() and pin it in config")


class MissingPoolKey(RuntimeError):
    pass


class ExecutorError(RuntimeError):
    pass


# --- Ledger ------------------------------------------------------------------

# Obligation lifecycle.  Every transition is timestamped and keeps the tx
# hashes that moved it, so the journal receipt -> conversion -> treasury
# deposit chain is auditable end to end.
STATUS_PENDING = "pending"
STATUS_QUOTED = "quoted"
STATUS_APPROVED = "approved"
STATUS_EXECUTING = "executing"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"


class ConversionLedger:
    """JSON-persisted obligation ledger with atomic writes."""

    def __init__(self, path: str):
        self.path = path
        self._obligations: Dict[str, Dict[str, Any]] = {}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._obligations = data.get("obligations", {})
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("ledger load failed (%s); starting empty", exc)

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"obligations": self._obligations}, fh, indent=2)
        os.replace(tmp, self.path)

    # -- API --------------------------------------------------------------
    def record(self, obligation_id: str, token_in: str, amount_wei: int,
               source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record a pending conversion obligation (idempotent)."""
        if obligation_id in self._obligations:
            return self._obligations[obligation_id]
        ob = {
            "id": obligation_id,
            "token_in": token_in.upper(),
            "amount_wei": str(amount_wei),
            "target": None,
            "status": STATUS_PENDING,
            "source": source or {},
            "history": [{"status": STATUS_PENDING, "at": _now()}],
            "txs": [],
        }
        self._obligations[obligation_id] = ob
        self._save()
        return ob

    def transition(self, obligation_id: str, status: str,
                   detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ob = self._obligations[obligation_id]
        ob["status"] = status
        entry: Dict[str, Any] = {"status": status, "at": _now()}
        if detail:
            entry.update(detail)
        ob["history"].append(entry)
        self._save()
        return ob

    def add_tx(self, obligation_id: str, kind: str, tx_hash: str,
               extra: Optional[Dict[str, Any]] = None) -> None:
        ob = self._obligations[obligation_id]
        rec: Dict[str, Any] = {"kind": kind, "tx_hash": tx_hash, "at": _now()}
        if extra:
            rec.update(extra)
        ob["txs"].append(rec)
        self._save()

    def get(self, obligation_id: str) -> Optional[Dict[str, Any]]:
        return self._obligations.get(obligation_id)

    def pending(self) -> List[Dict[str, Any]]:
        return [ob for ob in self._obligations.values()
                if ob["status"] in (STATUS_PENDING, STATUS_QUOTED, STATUS_APPROVED)]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- Executor ----------------------------------------------------------------

# Legacy callback type retained for compatibility with the planned pipeline.
# This repository has no production key-custody integration, and the callback
# is unreachable from every non-dry-run entrypoint while live execution is held.
SignAndBroadcast = Callable[[Dict[str, Any], str], str]
# Waits for inclusion; returns a receipt-ish mapping.
WaitForReceipt = Callable[[str], Mapping[str, Any]]


class FeeConversionExecutor:
    """Builds and simulates fee conversions; live execution is disabled."""

    def __init__(self, config: FeeConversionConfig,
                 ledger: Optional[ConversionLedger] = None):
        self.config = config
        self.ledger = ledger or ConversionLedger(config.ledger_path)
        self._w3 = None  # lazy: only needed for RPC paths

    # -- RPC plumbing -----------------------------------------------------
    @property
    def w3(self):
        if self._w3 is None:
            from web3 import Web3
            self._w3 = Web3(Web3.HTTPProvider(self.config.rpc_url,
                                             request_kwargs={"timeout": 15}))
        return self._w3

    def _eth_call(self, to: str, data: bytes,
                  from_addr: Optional[str] = None) -> bytes:
        # web3.py v8 rejects non-EIP-55 addresses in call params before any
        # network I/O, and several configured addresses (V4_QUOTER,
        # UNIVERSAL_ROUTER_V4, AXIOM_TOKEN) are not checksummed in-repo.
        # Normalize at the RPC boundary so the read-only path works.
        params: Dict[str, Any] = {
            "to": self.w3.to_checksum_address(to),
            "data": "0x" + data.hex(),
        }
        if from_addr:
            params["from"] = self.w3.to_checksum_address(from_addr)
        raw = self.w3.eth.call(params, "latest")
        return bytes(raw)

    # -- quoting ----------------------------------------------------------
    def _quote_calldata(self, pool: PoolKey, token_in: str,
                        amount_in_wei: int) -> bytes:
        addr_in = self.config.token_address(token_in)
        zfo = zero_for_one(pool, addr_in)
        payload = abi_encode(
            ["((address,address,uint24,int24,address),bool,uint128,bytes)"],
            [(pool.as_tuple(), zfo, amount_in_wei, b"")],
        )
        return SEL_QUOTE_EXACT_IN_SINGLE + payload

    @staticmethod
    def _decode_quote(raw: bytes) -> Tuple[int, int]:
        """Decode (amountOut, gasEstimate); tolerates quoter revert-style data."""
        if len(raw) >= 64:
            amount_out = int.from_bytes(raw[0:32], "big")
            gas_est = int.from_bytes(raw[32:64], "big")
            return amount_out, gas_est
        raise ExecutorError(f"quoter returned {len(raw)} bytes; cannot decode")

    def quote(self, token_in: str, token_out: str,
              amount_in_wei: int) -> Dict[str, Any]:
        """Live quote via the V4 Quoter (read-only)."""
        pool = self.config.pool_key_for(token_in, token_out)
        data = self._quote_calldata(pool, token_in, amount_in_wei)
        try:
            raw = self._eth_call(self.config.quoter, data)
            amount_out, gas_est = self._decode_quote(raw)
        except Exception as exc:  # eth_call raises on revert
            # V4 Quoter reverts with the quote encoded in the revert data on
            # some builds; surface whatever came back before giving up.
            msg = str(exc)
            raise ExecutorError(f"quote failed for {token_in}->{token_out}: {msg}")
        min_out = amount_out * (10_000 - self.config.slippage_bps) // 10_000
        token_in_address = self.config.token_address(token_in)
        return {
            "token_in": token_in.upper(),
            "token_out": token_out.upper(),
            "amount_in_wei": amount_in_wei,
            "amount_out_wei": amount_out,
            "amount_out_min_wei": min_out,
            "gas_estimate": gas_est,
            "slippage_bps": self.config.slippage_bps,
            "pool": asdict(pool),
            "zero_for_one": zero_for_one(pool, token_in_address),
        }

    def discover_pool(self, token_in: str, token_out: str,
                      probe_wei: int) -> PoolKey:
        """Probe candidate (fee, tickSpacing) pairs; return the first pool
        that returns a non-zero quote.  Read-only.  The result must be pinned
        in config.pool_keys by the operator."""
        addr_in = self.config.token_address(token_in)
        addr_out = self.config.token_address(token_out)
        last_err: Optional[str] = None
        for fee, spacing in POOL_CANDIDATES:
            pool = PoolKey.for_pair(addr_in, addr_out, fee, spacing)
            data = self._quote_calldata(pool, token_in, probe_wei)
            try:
                raw = self._eth_call(self.config.quoter, data)
                amount_out, _ = self._decode_quote(raw)
            except Exception as exc:
                last_err = str(exc)[:120]
                continue
            if amount_out > 0:
                logger.info("discovered pool %s fee=%d spacing=%d -> out=%d",
                            token_in, fee, spacing, amount_out)
                return pool
        raise ExecutorError(
            f"no live V4 pool found for {token_in}->{token_out} "
            f"(last error: {last_err})")

    # -- ERC-20 helpers ---------------------------------------------------
    def allowance(self, token: str, owner: str, spender: str) -> int:
        data = SEL_ALLOWANCE + abi_encode(
            ["address", "address"], [owner, spender])
        raw = self._eth_call(self.config.token_address(token), data)
        return int.from_bytes(raw[-32:], "big")

    def balance_of(self, token: str, owner: str) -> int:
        data = SEL_BALANCE_OF + abi_encode(["address"], [owner])
        raw = self._eth_call(self.config.token_address(token), data)
        return int.from_bytes(raw[-32:], "big")

    @staticmethod
    def _resolve_token(token: str,
                       token_addresses: Optional[Mapping[str, str]]) -> str:
        addrs = token_addresses if token_addresses else TOKEN_ADDRESSES
        try:
            return addrs[token.upper()]
        except KeyError:
            raise ExecutorError(f"unknown token {token}")

    @staticmethod
    def build_approve_tx(token: str, spender: str, amount_wei: int,
                         chain_id: int = BASE_CHAIN_ID,
                         token_addresses: Optional[Mapping[str, str]] = None
                         ) -> Dict[str, Any]:
        data = SEL_APPROVE + abi_encode(
            ["address", "uint256"], [spender, amount_wei])
        addr = FeeConversionExecutor._resolve_token(token, token_addresses)
        return {"to": _ck(addr),
                "data": "0x" + data.hex(), "value": 0, "chainId": chain_id}

    @staticmethod
    def build_transfer_tx(token: str, to: str, amount_wei: int,
                          chain_id: int = BASE_CHAIN_ID,
                          token_addresses: Optional[Mapping[str, str]] = None
                          ) -> Dict[str, Any]:
        data = SEL_TRANSFER + abi_encode(
            ["address", "uint256"], [to, amount_wei])
        addr = FeeConversionExecutor._resolve_token(token, token_addresses)
        return {"to": _ck(addr),
                "data": "0x" + data.hex(), "value": 0, "chainId": chain_id}

    def permit2_allowance(self, token: str, owner: str, spender: str) -> int:
        """Permit2-internal allowance (owner, token, spender).

        Distinct from the ERC-20 allowance: the V4 settle path calls
        Permit2.transferFrom(payer, poolManager, ...), which checks THIS
        allowance. Returns (amount, expiration, nonce) packed; we read amount.
        """
        data = SEL_P2_ALLOWANCE + abi_encode(
            ["address", "address", "address"],
            [owner, self.config.token_address(token), spender])
        raw = self._eth_call(self.config.permit2, data)
        # returns (uint160 amount, uint48 expiration, uint48 nonce); amount first
        return int.from_bytes(raw[:32], "big")

    @staticmethod
    def build_permit2_approve_tx(token: str, spender: str, amount_wei: int,
                                 expiration: int,
                                 chain_id: int = BASE_CHAIN_ID,
                                 token_addresses: Optional[Mapping[str, str]] = None
                                 ) -> Dict[str, Any]:
        """Unsigned Permit2.approve(token, spender, amount, expiration).

        Grants the Universal Router a Permit2-internal allowance so the V4
        settle step (Permit2.transferFrom) can pull the fee token. This is in
        ADDITION to the ERC-20 approve(Permit2); either one alone reverts.
        """
        data = SEL_P2_APPROVE + abi_encode(
            ["address", "address", "uint160", "uint48"],
            [FeeConversionExecutor._resolve_token(token, token_addresses),
             spender, amount_wei, expiration])
        return {"to": _ck(PERMIT2),
                "data": "0x" + data.hex(), "value": 0, "chainId": chain_id}

    # -- Universal Router V4 swap -----------------------------------------
    def build_swap_tx(self, token_in: str, token_out: str, amount_in_wei: int,
                      amount_out_min_wei: int, recipient: str,
                      deadline: Optional[int] = None,
                      chain_id: int = BASE_CHAIN_ID) -> Dict[str, Any]:
        """Unsigned Universal Router execute() for an exact-input V4 swap.

        Encodes commands [V4_SWAP] with actions
        [SWAP_EXACT_IN_SINGLE, SETTLE_ALL, TAKE_ALL].
        The deployed V4Router's TAKE_ALL does
        ``_take(currency, msgSender(), amount)`` where msgSender() is the
        original execute() caller (the forwarder), so the output token lands
        directly on the forwarder -- no SWEEP command is needed (and a SWEEP
        would revert with InsufficientBalance because the router holds
        nothing). Verified on a Base fork: take emitted
        USDC.transfer(to=forwarder).
        A separate forward tx then moves the output to the treasury, so the
        post-swap balance can be verified before it leaves the forwarder.
        ``recipient`` must equal the forwarder (the execute() sender).
        """
        pool = self.config.pool_key_for(token_in, token_out)
        addr_in = self.config.token_address(token_in)
        addr_out = self.config.token_address(token_out)
        zfo = zero_for_one(pool, addr_in)

        actions = bytes([ACT_SWAP_EXACT_IN_SINGLE, ACT_SETTLE_ALL, ACT_TAKE_ALL])
        # SWAP_EXACT_IN_SINGLE params MUST be a single abi-encoded struct
        # (ExactInputSingleParams), NOT flat multi-arg encoding: v4-periphery's
        # CalldataDecoder.decodeSwapExactInSingleParams does
        #   swapParams := add(params.offset, calldataload(params.offset))
        # i.e. it follows the first word as an offset to the struct. Flat
        # encoding makes the decoder read garbage and the unlock callback
        # reverts before any sub-call. The struct also carries minHopPriceX36
        # (0 = disabled); omitting it shifts every field.
        params = [
            abi_encode(
                ["((address,address,uint24,int24,address),bool,uint128,"
                 "uint128,uint256,bytes)"],
                [[pool.as_tuple(), zfo, amount_in_wei, amount_out_min_wei,
                  0, b""]]),
            abi_encode(["address", "uint256"], [addr_in, amount_in_wei]),
            abi_encode(["address", "uint256"], [addr_out, amount_out_min_wei]),
        ]
        v4_input = abi_encode(["bytes", "bytes[]"], [actions, params])

        commands = bytes([CMD_V4_SWAP])
        if deadline is None:
            deadline = int(time.time()) + self.config.tx_deadline_secs
        data = SEL_UR_EXECUTE + abi_encode(
            ["bytes", "bytes[]", "uint256"],
            [commands, [v4_input], deadline])
        return {"to": _ck(self.config.universal_router),
                "data": "0x" + data.hex(), "value": 0, "chainId": chain_id}

    # -- planning ---------------------------------------------------------
    def plan(self, obligation_id: str,
             target: Optional[str] = None) -> Dict[str, Any]:
        """Quote an obligation and build every unsigned tx.  No broadcast."""
        ob = self.ledger.get(obligation_id)
        if ob is None:
            raise ExecutorError(f"unknown obligation {obligation_id}")
        if ob["status"] not in (STATUS_PENDING, STATUS_QUOTED, STATUS_APPROVED):
            raise ExecutorError(
                f"obligation {obligation_id} is {ob['status']}; not plannable")

        token_in = ob["token_in"]
        amount_in = int(ob["amount_wei"])
        token_out = (target or self.config.target).upper()
        if token_out not in ("USDC", "WETH"):
            raise ExecutorError(f"unsupported target {token_out}")

        if amount_in < self.config.min_swap_wei:
            raise ExecutorError(
                f"amount {amount_in} below min_swap_wei {self.config.min_swap_wei}; "
                f"left queued")

        q = self.quote(token_in, token_out, amount_in)
        self.ledger.transition(obligation_id, STATUS_QUOTED,
                               {"quote": {k: v for k, v in q.items()
                                          if k != "pool"}})
        txs: List[Dict[str, Any]] = []

        current_allowance = self.allowance(token_in, self.config.forwarder,
                                           self.config.permit2)
        if current_allowance < amount_in:
            txs.append({"kind": "approve_permit2",
                        "unsigned": self.build_approve_tx(
                            token_in, self.config.permit2, amount_in,
                            chain_id=self.config.chain_id,
                            token_addresses=self.config.token_addresses)})

        # The V4 settle step pulls the fee token via Permit2.transferFrom,
        # which checks the Permit2-INTERNAL allowance (forwarder -> router),
        # not the ERC-20 approval above. Without this the swap reverts at
        # the Permit2 call (fork-sim proven).
        p2_allowance = self.permit2_allowance(
            token_in, self.config.forwarder, self.config.universal_router)
        if p2_allowance < amount_in:
            txs.append({"kind": "permit2_approve",
                        "unsigned": self.build_permit2_approve_tx(
                            token_in, self.config.universal_router, amount_in,
                            expiration=int(time.time()) + 30 * 86400,
                            chain_id=self.config.chain_id,
                            token_addresses=self.config.token_addresses)})

        txs.append({"kind": "swap",
                    "unsigned": self.build_swap_tx(
                        token_in, token_out, amount_in,
                        q["amount_out_min_wei"], self.config.forwarder,
                        chain_id=self.config.chain_id)})
        # Forward amount is filled after the swap confirms (verified balance).
        txs.append({"kind": "forward_to_treasury", "unsigned": None,
                    "note": "built post-swap from the verified output balance"})

        plan = {"obligation_id": obligation_id, "quote": q, "txs": txs,
                "forwarder": self.config.forwarder,
                "treasury": self.config.treasury, "armed": False,
                "live_execution_allowed": False}
        return plan

    # -- execution ----------------------------------------------------------
    def execute_plan(self, plan: Dict[str, Any],
                     sign_and_broadcast: SignAndBroadcast,
                     wait_for_receipt: WaitForReceipt,
                     dry_run: bool = False) -> Dict[str, Any]:
        """Dry-run a plan, or deny live execution before touching a signer.

        WP0 has no live onchain authorization contract.  The existing ``armed``
        boolean is not authorization and cannot grant a live capability.
        """
        if not dry_run:
            raise ExecutorError(
                "live conversion is blocked: no canonical release/approval "
                "contract or owner-approved onchain capability exists")

        oid = plan["obligation_id"]
        token_out = plan["quote"]["token_out"]

        mode = "dry_run" if dry_run else "live"
        logger.info("executing conversion %s (%s)", oid, mode)
        # Dry-runs are simulations, not durable progress.  Live execution is
        # denied above, so this method must not leave an obligation executing.

        try:
            for step in plan["txs"]:
                kind = step["kind"]
                if kind == "forward_to_treasury":
                    self._execute_forward(oid, token_out, sign_and_broadcast,
                                          wait_for_receipt, dry_run)
                    continue
                unsigned = dict(step["unsigned"])
                self._simulate(kind, unsigned)
                if dry_run:
                    logger.info("[dry-run] %s simulated OK", kind)
                    continue
                tx_hash = sign_and_broadcast(unsigned, kind)
                self.ledger.add_tx(oid, kind, tx_hash)
                receipt = wait_for_receipt(tx_hash)
                if int(receipt.get("status", 0)) != 1:
                    raise ExecutorError(f"{kind} tx {tx_hash} reverted")
                logger.info("%s confirmed: %s", kind, tx_hash)

            if not dry_run:
                self.ledger.transition(oid, STATUS_EXECUTED)
            return {"obligation_id": oid, "mode": mode,
                    "status": self.ledger.get(oid)["status"]}
        except Exception as exc:
            if not dry_run:
                self.ledger.transition(oid, STATUS_FAILED,
                                       {"error": str(exc)[:500]})
            raise

    def _execute_forward(self, oid: str, token_out: str,
                         sign_and_broadcast: SignAndBroadcast,
                         wait_for_receipt: WaitForReceipt,
                         dry_run: bool) -> None:
        """Build the treasury forward from the VERIFIED post-swap balance."""
        if not dry_run:
            raise ExecutorError(
                "live conversion is blocked: no canonical release/approval "
                "contract or owner-approved onchain capability exists")
        if dry_run:
            logger.info("[dry-run] forward_to_treasury: balance check skipped")
            return
        balance = self.balance_of(token_out, self.config.forwarder)
        if balance <= 0:
            raise ExecutorError(
                f"post-swap {token_out} balance is 0; refusing to forward")
        unsigned = self.build_transfer_tx(token_out, self.config.treasury,
                                          balance,
                                          chain_id=self.config.chain_id,
                                          token_addresses=self.config.token_addresses)
        self._simulate("forward_to_treasury", unsigned)
        tx_hash = sign_and_broadcast(unsigned, "forward_to_treasury")
        self.ledger.add_tx(oid, "forward_to_treasury", tx_hash,
                           {"amount_wei": str(balance), "token": token_out})
        receipt = wait_for_receipt(tx_hash)
        if int(receipt.get("status", 0)) != 1:
            raise ExecutorError(f"forward tx {tx_hash} reverted")
        logger.info("forwarded %d %s to treasury: %s",
                    balance, token_out, tx_hash)

    def _simulate(self, kind: str, unsigned: Dict[str, Any]) -> None:
        """eth_call the exact calldata from the forwarder; revert = fail closed."""
        data_hex = unsigned["data"]
        data = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x")
                             else data_hex)
        try:
            self._eth_call(unsigned["to"], data, self.config.forwarder)
        except Exception as exc:
            raise ExecutorError(f"simulation reverted for {kind}: {exc}")


# --- convenience -------------------------------------------------------------

def pending_conversion_id(token: str, amount_wei: int,
                          source: Optional[Dict[str, Any]] = None
                          ) -> str:
    """Deterministic obligation id for a fee inflow (idempotency key).

    Shared with the fee-event listener so it can tell newly recorded
    obligations apart from idempotent re-encounters on rescans.  When the
    source carries a ``log_index`` (event-sourced obligations), it is part
    of the id so two identical transfers inside one transaction record two
    obligations instead of collapsing into one.
    """
    source = source or {}
    tx_hash = source.get("tx_hash") or source.get("receipt_tx") or "adhoc"
    oid = f"feeconv-{token.upper()}-{tx_hash[:16]}-{amount_wei}"
    if "log_index" in source:
        oid = f"{oid}-log{source['log_index']}"
    return oid


def record_pending_conversion(ledger: ConversionLedger, token: str,
                              amount_wei: int,
                              source: Optional[Dict[str, Any]] = None
                              ) -> Dict[str, Any]:
    """Bridge from fee recording (record_axm_receipt / settle-proof builders)
    into the executor ledger.  The obligation id is derived from the source
    tx when present, so re-recording is idempotent."""
    oid = pending_conversion_id(token, amount_wei, source)
    return ledger.record(oid, token, amount_wei, source)
