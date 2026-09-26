"""Forwarder swap executor: converts realized AXM/SINC platform fees to USDC/WETH.

Pipeline
--------
pending obligation (recorded by ``record_axm_receipt`` / settle-proof builders)
  -> quote via the Uniswap V4 Quoter (read-only)
  -> approve Permit2 to pull the fee token (if allowance is short)
  -> swap exact-input through the V4-capable Universal Router
  -> forward the USDC/WETH output to the treasury
  -> ledger marks the obligation executed (or failed, with the reason)

Security rules (same posture as ``onchain/auction_client.py``)
---------------------------------------------------------------
* This module NEVER reads private keys.  Execution takes an injected
  ``sign_and_broadcast`` callable; in production that callable signs with the
  forwarder key held in the Secure Vault and is approved per broadcast.
* All token / infra addresses come from ``sincor2.onchain.constants`` or are
  verified Base deployments named below with their source.
* Pool keys are operator-pinned config (see ``discover_pool``).  The executor
  never guesses a pool.
* Every state-changing step is preceded by an ``eth_call`` simulation; a
  revert fails the plan closed instead of broadcasting.
* Swaps are exact-input with an explicit ``amountOutMinimum`` derived from
  the live quote minus ``slippage_bps``.  Dust below ``min_swap_wei`` stays
  queued in the ledger instead of being swapped at a loss to gas.
* Live broadcasting is gated behind ``config.armed`` (default False).  The
  arming runbook is in ``docs/ops/FEE_EXECUTOR_RUNBOOK.md``: pool discovery,
  fork simulation of the exact calldata, then a dust-amount live swap.

Fee policy (locked 2026-09-26): 5 % platform fee, 100 % to treasury,
converted to USDC/WETH before deposit, no burn.  Deflationary mechanics are
deferred to a later governance decision.
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
    armed: bool = False                  # live broadcast gate; see runbook
    ledger_path: str = os.path.expanduser(
        "~/workspace/ops/fee_conversion_ledger.json")

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

# Type of the injected broadcast hook.  Receives an unsigned tx dict
# (to/data/value/gas/chainId/nonce...), returns the tx hash.  The callable
# owns key custody (Secure Vault in production) — this module never sees keys.
SignAndBroadcast = Callable[[Dict[str, Any], str], str]
# Waits for inclusion; returns a receipt-ish mapping.
WaitForReceipt = Callable[[str], Mapping[str, Any]]


class FeeConversionExecutor:
    """Builds, simulates, and (when armed) executes fee conversions."""

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
        params: Dict[str, Any] = {"to": to, "data": "0x" + data.hex()}
        if from_addr:
            params["from"] = from_addr
        raw = self.w3.eth.call(params, "latest")
        return bytes(raw)

    # -- quoting ----------------------------------------------------------
    def _quote_calldata(self, pool: PoolKey, token_in: str,
                        amount_in_wei: int) -> bytes:
        addr_in = TOKEN_ADDRESSES[token_in.upper()]
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
        return {
            "token_in": token_in.upper(),
            "token_out": token_out.upper(),
            "amount_in_wei": amount_in_wei,
            "amount_out_wei": amount_out,
            "amount_out_min_wei": min_out,
            "gas_estimate": gas_est,
            "slippage_bps": self.config.slippage_bps,
            "pool": asdict(pool),
            "zero_for_one": zero_for_one(pool, token_in),
        }

    def discover_pool(self, token_in: str, token_out: str,
                      probe_wei: int) -> PoolKey:
        """Probe candidate (fee, tickSpacing) pairs; return the first pool
        that returns a non-zero quote.  Read-only.  The result must be pinned
        in config.pool_keys by the operator."""
        addr_in = TOKEN_ADDRESSES[token_in.upper()]
        addr_out = TOKEN_ADDRESSES[token_out.upper()]
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
        raw = self._eth_call(TOKEN_ADDRESSES[token.upper()], data)
        return int.from_bytes(raw[-32:], "big")

    def balance_of(self, token: str, owner: str) -> int:
        data = SEL_BALANCE_OF + abi_encode(["address"], [owner])
        raw = self._eth_call(TOKEN_ADDRESSES[token.upper()], data)
        return int.from_bytes(raw[-32:], "big")

    @staticmethod
    def build_approve_tx(token: str, spender: str, amount_wei: int,
                         chain_id: int = BASE_CHAIN_ID) -> Dict[str, Any]:
        data = SEL_APPROVE + abi_encode(
            ["address", "uint256"], [spender, amount_wei])
        return {"to": TOKEN_ADDRESSES[token.upper()],
                "data": "0x" + data.hex(), "value": 0, "chainId": chain_id}

    @staticmethod
    def build_transfer_tx(token: str, to: str, amount_wei: int,
                          chain_id: int = BASE_CHAIN_ID) -> Dict[str, Any]:
        data = SEL_TRANSFER + abi_encode(
            ["address", "uint256"], [to, amount_wei])
        return {"to": TOKEN_ADDRESSES[token.upper()],
                "data": "0x" + data.hex(), "value": 0, "chainId": chain_id}

    # -- Universal Router V4 swap -----------------------------------------
    def build_swap_tx(self, token_in: str, token_out: str, amount_in_wei: int,
                      amount_out_min_wei: int, recipient: str,
                      deadline: Optional[int] = None,
                      chain_id: int = BASE_CHAIN_ID) -> Dict[str, Any]:
        """Unsigned Universal Router execute() for an exact-input V4 swap.

        Encodes commands [V4_SWAP, SWEEP]:
          V4_SWAP actions = [SWAP_EXACT_IN_SINGLE, SETTLE_ALL, TAKE_ALL]
          SWEEP sends the output token to ``recipient`` (the forwarder).
        A separate forward tx then moves the output to the treasury, so the
        post-swap balance can be verified before it leaves the forwarder.
        """
        pool = self.config.pool_key_for(token_in, token_out)
        addr_in = TOKEN_ADDRESSES[token_in.upper()]
        addr_out = TOKEN_ADDRESSES[token_out.upper()]
        zfo = zero_for_one(pool, addr_in)

        actions = bytes([ACT_SWAP_EXACT_IN_SINGLE, ACT_SETTLE_ALL, ACT_TAKE_ALL])
        params = [
            abi_encode(
                ["(address,address,uint24,int24,address)", "bool", "uint128",
                 "uint128", "bytes"],
                [pool.as_tuple(), zfo, amount_in_wei, amount_out_min_wei, b""]),
            abi_encode(["address", "uint256"], [addr_in, amount_in_wei]),
            abi_encode(["address", "uint256"], [addr_out, amount_out_min_wei]),
        ]
        v4_input = abi_encode(["bytes", "bytes[]"], [actions, params])
        sweep_input = abi_encode(
            ["address", "address", "uint256"],
            [addr_out, recipient, amount_out_min_wei])

        commands = bytes([CMD_V4_SWAP, CMD_SWEEP])
        if deadline is None:
            deadline = int(time.time()) + self.config.tx_deadline_secs
        data = SEL_UR_EXECUTE + abi_encode(
            ["bytes", "bytes[]", "uint256"],
            [commands, [v4_input, sweep_input], deadline])
        return {"to": self.config.universal_router,
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
                            token_in, self.config.permit2, amount_in)})

        txs.append({"kind": "swap",
                    "unsigned": self.build_swap_tx(
                        token_in, token_out, amount_in,
                        q["amount_out_min_wei"], self.config.forwarder)})
        # Forward amount is filled after the swap confirms (verified balance).
        txs.append({"kind": "forward_to_treasury", "unsigned": None,
                    "note": "built post-swap from the verified output balance"})

        plan = {"obligation_id": obligation_id, "quote": q, "txs": txs,
                "forwarder": self.config.forwarder,
                "treasury": self.config.treasury, "armed": self.config.armed}
        return plan

    # -- execution ----------------------------------------------------------
    def execute_plan(self, plan: Dict[str, Any],
                     sign_and_broadcast: SignAndBroadcast,
                     wait_for_receipt: WaitForReceipt,
                     dry_run: bool = False) -> Dict[str, Any]:
        """Run a plan end to end.  With dry_run=True, simulates every step
        via eth_call and broadcasts nothing."""
        oid = plan["obligation_id"]
        ob = self.ledger.get(oid)
        token_in = ob["token_in"]
        token_out = plan["quote"]["token_out"]
        amount_in = int(ob["amount_wei"])

        if not dry_run and not self.config.armed:
            raise ExecutorError(
                "executor is not armed; run the arming runbook first "
                "(docs/ops/FEE_EXECUTOR_RUNBOOK.md)")

        mode = "dry_run" if dry_run else "live"
        logger.info("executing conversion %s (%s)", oid, mode)
        self.ledger.transition(oid, STATUS_EXECUTING, {"mode": mode})

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
        if dry_run:
            logger.info("[dry-run] forward_to_treasury: balance check skipped")
            return
        balance = self.balance_of(token_out, self.config.forwarder)
        if balance <= 0:
            raise ExecutorError(
                f"post-swap {token_out} balance is 0; refusing to forward")
        unsigned = self.build_transfer_tx(token_out, self.config.treasury,
                                          balance)
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

def record_pending_conversion(ledger: ConversionLedger, token: str,
                              amount_wei: int,
                              source: Optional[Dict[str, Any]] = None
                              ) -> Dict[str, Any]:
    """Bridge from fee recording (record_axm_receipt / settle-proof builders)
    into the executor ledger.  The obligation id is derived from the source
    tx when present, so re-recording is idempotent."""
    source = source or {}
    tx_hash = source.get("tx_hash") or source.get("receipt_tx") or "adhoc"
    oid = f"feeconv-{token.upper()}-{tx_hash[:16]}-{amount_wei}"
    return ledger.record(oid, token, amount_wei, source)
