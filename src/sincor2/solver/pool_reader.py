"""
Read-only Base mainnet pool reader.

Fetches pool discovery + state from public RPC endpoints with batched
JSON-RPC eth_calls, every call pinned to a single block so a snapshot
is internally consistent. READ-ONLY by construction: the only RPC
methods used are eth_blockNumber, eth_gasPrice and eth_call. There is
no code path here that can build, sign, or send a transaction.
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Optional, Sequence, Tuple

import requests
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from web3 import Web3

from .pools import (
    AERODROME_DEFAULT_FEE_BPS, AERODROME_FACTORY, DEFAULT_TOKENS,
    TOKEN_DECIMALS_BY_ADDR, UNISWAP_V2_FACTORY, UNISWAP_V3_FACTORY,
    V2_FEE_BPS, V3_FEE_TIERS, V3_TICK_SPACING, VENUE_AERODROME,
    VENUE_UNISWAP_V2, VENUE_UNISWAP_V3, PoolSnapshot, V2PoolState,
    V3PoolState, checksum,
)

DEFAULT_RPC_URLS = (
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
)
_USER_AGENT = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/120.0 Safari/537.36"}
ZERO_ADDRESS = "0x" + "0" * 40


def _selector(signature: str) -> bytes:
    return bytes(Web3.keccak(text=signature)[:4])


def _call_data(signature: str, arg_types: List[str], args: list) -> str:
    return "0x" + (_selector(signature) + abi_encode(arg_types, args)).hex()


class RpcError(Exception):
    pass


class BasePoolReader:
    def __init__(self, rpc_urls: Sequence[str] = DEFAULT_RPC_URLS,
                 window_words: int = 4, batch_size: int = 50,
                 timeout: int = 25):
        self.rpc_urls = list(rpc_urls)
        self.window_words = window_words
        self.batch_size = batch_size
        self.timeout = timeout
        self._id_counter = itertools.count(1)

    # -- raw JSON-RPC -------------------------------------------------------

    def _post(self, payload, url: Optional[str] = None) -> list:
        last_error: Optional[Exception] = None
        for target in ([url] if url else self.rpc_urls):
            try:
                resp = requests.post(target, json=payload, headers=_USER_AGENT,
                                     timeout=self.timeout)
                resp.raise_for_status()
                body = resp.json()
                return body if isinstance(body, list) else [body]
            except Exception as exc:  # try the next endpoint
                last_error = exc
        raise RpcError(f"all RPC endpoints failed: {last_error}")

    def _single(self, method: str, params: list):
        """One JSON-RPC call, rotating endpoints on ANY failure.

        A JSON-RPC *error item* is retried on the next endpoint too, not
        treated as final: pinned-block eth_calls against load-balanced
        public RPCs intermittently hit nodes that have not seen the
        block yet ("header not found") or are rate-limiting, and the
        sibling endpoint usually answers immediately. Observed live
        2026-11-11: without rotation, snapshots randomly lost pools.
        """
        last_error: Optional[Exception] = None
        for url in self.rpc_urls:
            for _attempt in range(2):
                try:
                    items = self._post(
                        {"jsonrpc": "2.0", "id": next(self._id_counter),
                         "method": method, "params": params}, url=url)
                    item = items[0]
                    if "error" in item:
                        last_error = RpcError(f"{method}: {item['error']}")
                        continue
                    return item["result"]
                except Exception as exc:
                    last_error = exc
        raise RpcError(f"{method} failed on all endpoints: {last_error}")

    def block_number(self) -> int:
        return int(self._single("eth_blockNumber", []), 16)

    def gas_price(self) -> int:
        return int(self._single("eth_gasPrice", []), 16)

    def eth_call_batch(self, calls: List[Tuple[str, str]],
                       block: int) -> List[Optional[str]]:
        """Batch eth_call at a pinned block. None = call failed/reverted.

        Public endpoints silently TRUNCATE oversized batch responses
        (observed live 2026-11-11: mainnet.base.org returned 25 of 36
        items, a different subset each run). Any request id absent from
        the response is therefore retried individually; only an item
        that comes back with an explicit error/empty result counts as a
        failed call. Without this, pool discovery silently loses pools.
        """
        results: List[Optional[str]] = [None] * len(calls)
        block_hex = hex(block)
        for start in range(0, len(calls), self.batch_size):
            chunk = calls[start:start + self.batch_size]
            ids = [next(self._id_counter) for _ in chunk]
            payload = [
                {"jsonrpc": "2.0", "id": rid, "method": "eth_call",
                 "params": [{"to": to, "data": data}, block_hex]}
                for rid, (to, data) in zip(ids, chunk)
            ]
            items = self._post(payload)
            by_id = {item.get("id"): item for item in items}
            missing: List[int] = []
            for offset, rid in enumerate(ids):
                item = by_id.get(rid)
                if item is None or "error" in item:
                    # absent (truncated batch) or errored (often a lagging
                    # node on pinned-block calls): retry individually,
                    # where _single rotates endpoints
                    missing.append(start + offset)
                elif "result" in item and item["result"] not in (None, "0x"):
                    results[start + offset] = item["result"]
                # empty "0x" result: genuine empty return, stays None
            for idx in missing:
                to, data = calls[idx]
                try:
                    single = self._single(
                        "eth_call", [{"to": to, "data": data}, block_hex])
                    if single not in (None, "0x"):
                        results[idx] = single
                except RpcError:
                    results[idx] = None
        return results

    # -- snapshot -----------------------------------------------------------

    def _verify_decimals(self, token_list: List[str], block: int) -> None:
        """Fail-closed check: on-chain decimals() must match the table.

        Token decimals are the kind of 'constant' that silently wrecks
        every human-unit price if wrong (SINC is 8, not 18 — caught
        2026-11-11). The chain is the source of truth; a mismatch or an
        unreadable decimals() aborts the snapshot rather than letting a
        mispriced quote out.
        """
        calls = [(t, _call_data("decimals()", [], [])) for t in token_list]
        results = self.eth_call_batch(calls, block)
        for token, raw in zip(token_list, results):
            expected = TOKEN_DECIMALS_BY_ADDR.get(token)
            if expected is None:
                continue  # untracked token: nothing claimed, nothing to verify
            if raw is None:
                raise RpcError(f"decimals() unreadable for {token}")
            (actual,) = abi_decode(["uint8"], bytes.fromhex(raw[2:]))
            if int(actual) != expected:
                raise RpcError(
                    f"decimals mismatch for {token}: table says {expected}, "
                    f"chain says {int(actual)} — refusing to snapshot")

    def snapshot(self, tokens: Sequence[str] = DEFAULT_TOKENS) -> PoolSnapshot:
        block = self.block_number()
        gas_price = self.gas_price()
        token_list = [checksum(t) for t in tokens]
        self._verify_decimals(token_list, block)
        pairs = [(a, b) for i, a in enumerate(token_list)
                 for b in token_list[i + 1:]]

        # 1. discovery
        discovery_calls: List[Tuple[str, str]] = []
        discovery_index: List[Tuple[str, str, str, Optional[int]]] = []
        for a, b in pairs:
            for fee in V3_FEE_TIERS:
                discovery_calls.append((UNISWAP_V3_FACTORY, _call_data(
                    "getPool(address,address,uint24)",
                    ["address", "address", "uint24"],
                    [Web3.to_checksum_address(a), Web3.to_checksum_address(b),
                     fee])))
                discovery_index.append((VENUE_UNISWAP_V3, a, b, fee))
            discovery_calls.append((UNISWAP_V2_FACTORY, _call_data(
                "getPair(address,address)", ["address", "address"],
                [Web3.to_checksum_address(a), Web3.to_checksum_address(b)])))
            discovery_index.append((VENUE_UNISWAP_V2, a, b, None))
            discovery_calls.append((AERODROME_FACTORY, _call_data(
                "getPool(address,address,bool)",
                ["address", "address", "bool"],
                [Web3.to_checksum_address(a), Web3.to_checksum_address(b),
                 False])))
            discovery_index.append((VENUE_AERODROME, a, b, None))
        found_raw = self.eth_call_batch(discovery_calls, block)
        found: List[Tuple[str, str, Optional[int]]] = []  # (venue, pool, fee)
        for (venue, _a, _b, fee), raw in zip(discovery_index, found_raw):
            if raw is None:
                continue
            (address,) = abi_decode(["address"], bytes.fromhex(raw[2:]))
            if checksum(address) != ZERO_ADDRESS:
                found.append((venue, address, fee))
        if not found:
            return PoolSnapshot(block_number=block, gas_price_wei=gas_price,
                                pools=())

        # 2. token0/token1 for every found pool
        token_calls: List[Tuple[str, str]] = []
        for _venue, address, _fee in found:
            token_calls.append((address, _call_data("token0()", [], [])))
            token_calls.append((address, _call_data("token1()", [], [])))
        token_raw = self.eth_call_batch(token_calls, block)
        pool_tokens: Dict[str, Tuple[str, str]] = {}
        for i, (_venue, address, _fee) in enumerate(found):
            raw0, raw1 = token_raw[2 * i], token_raw[2 * i + 1]
            if raw0 is None or raw1 is None:
                continue
            (t0,) = abi_decode(["address"], bytes.fromhex(raw0[2:]))
            (t1,) = abi_decode(["address"], bytes.fromhex(raw1[2:]))
            pool_tokens[address] = (t0, t1)

        # 3. state reads (+ Aerodrome fee probe)
        state_calls: List[Tuple[str, str]] = []
        state_index: List[Tuple[str, str]] = []  # (pool, kind)
        for venue, address, _fee in found:
            if address not in pool_tokens:
                continue
            if venue == VENUE_UNISWAP_V3:
                state_calls.append((address, _call_data("slot0()", [], [])))
                state_index.append((address, "slot0"))
                state_calls.append((address, _call_data("liquidity()", [], [])))
                state_index.append((address, "liquidity"))
            else:
                state_calls.append(
                    (address, _call_data("getReserves()", [], [])))
                state_index.append((address, "reserves"))
                if venue == VENUE_AERODROME:
                    state_calls.append((address, _call_data(
                        "getFee(address,bool)", ["address", "bool"],
                        [Web3.to_checksum_address(address), False])))
                    state_index.append((address, "aero_factory_fee"))
        # Aerodrome getFee lives on the factory, not the pool: re-point calls
        fixed_calls = []
        for (to, data), (_addr, kind) in zip(state_calls, state_index):
            if kind == "aero_factory_fee":
                fixed_calls.append((AERODROME_FACTORY, data))
            else:
                fixed_calls.append((to, data))
        state_raw = self.eth_call_batch(fixed_calls, block)
        state: Dict[Tuple[str, str], Optional[str]] = {}
        for (pool_addr, kind), raw in zip(state_index, state_raw):
            state[(pool_addr, kind)] = raw

        pools: List = []
        v3_pending = []
        for venue, address, fee in found:
            if address not in pool_tokens:
                continue
            token0, token1 = pool_tokens[address]
            if venue in (VENUE_UNISWAP_V2, VENUE_AERODROME):
                raw = state.get((address, "reserves"))
                if raw is None:
                    continue
                reserve0, reserve1, _ts = abi_decode(
                    ["uint112", "uint112", "uint32"], bytes.fromhex(raw[2:]))
                if venue == VENUE_UNISWAP_V2:
                    fee_bps, fee_source = V2_FEE_BPS, "protocol_constant"
                else:
                    fee_raw = state.get((address, "aero_factory_fee"))
                    if fee_raw is not None:
                        (fee_val,) = abi_decode(
                            ["uint256"], bytes.fromhex(fee_raw[2:]))
                        fee_bps, fee_source = int(fee_val), "factory"
                    else:
                        fee_bps = AERODROME_DEFAULT_FEE_BPS
                        fee_source = "assumed_default"
                pools.append(V2PoolState(
                    venue=venue, address=address, token0=token0, token1=token1,
                    reserve0=int(reserve0), reserve1=int(reserve1),
                    fee_bps=fee_bps, fee_source=fee_source))
            else:
                raw_slot0 = state.get((address, "slot0"))
                raw_liq = state.get((address, "liquidity"))
                if raw_slot0 is None or raw_liq is None:
                    continue
                slot0 = abi_decode(
                    ["uint160", "int24", "uint16", "uint16", "uint16",
                     "uint8", "bool"], bytes.fromhex(raw_slot0[2:]))
                (liquidity,) = abi_decode(
                    ["uint128"], bytes.fromhex(raw_liq[2:]))
                v3_pending.append({
                    "venue": venue, "address": address, "token0": token0,
                    "token1": token1, "fee_pips": fee,
                    "tick_spacing": V3_TICK_SPACING[fee],
                    "sqrt_price_x96": int(slot0[0]), "tick": int(slot0[1]),
                    "liquidity": int(liquidity),
                })

        # 4. V3 tick windows: bitmap words around the current tick
        bitmap_calls: List[Tuple[str, str]] = []
        bitmap_index: List[Tuple[int, int]] = []  # (pending idx, word)
        for idx, pending in enumerate(v3_pending):
            spacing = pending["tick_spacing"]
            compressed = _compress(pending["tick"], spacing)
            word = compressed >> 8
            for w in range(word - self.window_words,
                           word + self.window_words + 1):
                bitmap_calls.append((pending["address"], _call_data(
                    "tickBitmap(int16)", ["int16"], [w])))
                bitmap_index.append((idx, w))
        bitmap_raw = self.eth_call_batch(bitmap_calls, block)
        tick_positions: Dict[int, List[int]] = {}
        for (idx, word), raw in zip(bitmap_index, bitmap_raw):
            if raw is None:
                continue
            (bitmap,) = abi_decode(["uint256"], bytes.fromhex(raw[2:]))
            spacing = v3_pending[idx]["tick_spacing"]
            ticks_here = []
            remaining = int(bitmap)
            bit = 0
            while remaining:
                if remaining & 1:
                    tick = (word * 256 + bit) * spacing
                    if -887272 <= tick <= 887272:
                        ticks_here.append(tick)
                remaining >>= 1
                bit += 1
            tick_positions.setdefault(idx, []).extend(ticks_here)

        # 5. liquidityNet for every initialized tick in the windows
        tick_calls: List[Tuple[str, str]] = []
        tick_index: List[Tuple[int, int]] = []
        for idx, ticks_here in tick_positions.items():
            pending = v3_pending[idx]
            current = pending["tick"]
            ordered = sorted(ticks_here, key=lambda t: abs(t - current))
            for tick in ordered[:2000]:
                tick_calls.append((pending["address"], _call_data(
                    "ticks(int24)", ["int24"], [tick])))
                tick_index.append((idx, tick))
        tick_raw = self.eth_call_batch(tick_calls, block)
        tick_nets: Dict[int, Dict[int, int]] = {}
        for (idx, tick), raw in zip(tick_index, tick_raw):
            if raw is None:
                continue
            decoded = abi_decode(
                ["uint128", "int128", "uint256", "uint256", "int56",
                 "uint160", "uint32", "bool"], bytes.fromhex(raw[2:]))
            tick_nets.setdefault(idx, {})[tick] = int(decoded[1])

        for idx, pending in enumerate(v3_pending):
            pools.append(V3PoolState(
                venue=pending["venue"], address=pending["address"],
                token0=pending["token0"], token1=pending["token1"],
                fee_pips=pending["fee_pips"],
                tick_spacing=pending["tick_spacing"],
                sqrt_price_x96=pending["sqrt_price_x96"],
                tick=pending["tick"], liquidity=pending["liquidity"],
                initialized_ticks=tick_nets.get(idx, {})))
        return PoolSnapshot(block_number=block, gas_price_wei=gas_price,
                            pools=tuple(pools))


def _compress(tick: int, spacing: int) -> int:
    """TickBitmap compression; Python floor division matches the
    Solidity truncation-then-adjust semantics exactly."""
    return tick // spacing
