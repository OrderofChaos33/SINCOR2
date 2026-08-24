"""Startup probe: eth_call symbol() and decimals() on canonical tokens."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .constants import (
    AXIOM_TOKEN,
    AXM_DECIMALS,
    AXM_SYMBOL,
    BASE_CHAIN_ID,
    SINC_DECIMALS,
    SINC_SYMBOL,
    SINC_TOKEN,
    TREASURY,
    assert_live_not_stale,
    catalog,
    is_stale,
    resolve_address,
)

logger = logging.getLogger("sincor.onchain.probe")

# ERC-20 selectors (keccak256 first 4 bytes)
SELECTOR_SYMBOL = "0x95d89b41"
SELECTOR_DECIMALS = "0x313ce567"

EthCall = Callable[[str, str], str]


@dataclass
class TokenProbe:
    symbol_name: str
    address: str
    expected_symbol: str
    expected_decimals: int
    observed_symbol: str = ""
    observed_decimals: int = -1
    ok: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TokenProbeReport:
    ok: bool
    chain_id: int
    rpc: str
    catalog_ok: bool
    probes: List[TokenProbe] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"catalog={'ok' if self.catalog_ok else 'FAIL'}"]
        for probe in self.probes:
            if probe.error:
                bits.append(f"{probe.symbol_name}={probe.error}")
            else:
                bits.append(
                    f"{probe.symbol_name}={probe.observed_symbol}/{probe.observed_decimals}"
                    f"{'' if probe.ok else ' MISMATCH'}"
                )
        return "; ".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["probes"] = [p.to_dict() for p in self.probes]
        return payload


def decode_uint(hex_data: str) -> int:
    raw = hex_data[2:] if hex_data.startswith("0x") else hex_data
    if not raw:
        return 0
    return int(raw, 16)


def encode_uint(value: int) -> str:
    return "0x" + format(int(value), "064x")


def encode_abi_string(value: str) -> str:
    blob = (value or "").encode("utf-8")
    offset = format(32, "064x")
    length = format(len(blob), "064x")
    payload = blob.hex()
    pad = (64 - (len(payload) % 64)) % 64
    payload = payload + ("0" * pad)
    return "0x" + offset + length + payload


def decode_abi_string(hex_data: str) -> str:
    raw = hex_data[2:] if hex_data.startswith("0x") else hex_data
    if len(raw) < 128:
        # Some tokens return bytes32 symbols (short string).
        try:
            blob = bytes.fromhex(raw[:64].ljust(64, "0"))
            return blob.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        except ValueError:
            return ""
    length = int(raw[64:128], 16)
    start = 128
    end = start + length * 2
    try:
        return bytes.fromhex(raw[start:end]).decode("utf-8", errors="replace")
    except ValueError:
        return ""


def http_eth_call(rpc_url: str, timeout: float = 2.5) -> EthCall:
    def _call(to: str, data: str) -> str:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"],
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            rpc_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "SINCOR-onchain/1.0"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        result = payload.get("result")
        if not isinstance(result, str):
            raise RuntimeError("eth_call returned no result")
        return result

    return _call


def probe_token(
    *,
    name: str,
    address: str,
    expected_symbol: str,
    expected_decimals: int,
    eth_call: EthCall,
) -> TokenProbe:
    probe = TokenProbe(
        symbol_name=name,
        address=address,
        expected_symbol=expected_symbol,
        expected_decimals=expected_decimals,
    )
    if is_stale(address):
        probe.error = "stale address"
        return probe
    try:
        probe.observed_symbol = decode_abi_string(eth_call(address, SELECTOR_SYMBOL)).strip()
        probe.observed_decimals = decode_uint(eth_call(address, SELECTOR_DECIMALS))
    except Exception as exc:  # noqa: BLE001 — RPC is best-effort
        probe.error = f"rpc:{exc}"
        return probe
    probe.ok = (
        probe.observed_symbol.upper() == expected_symbol.upper()
        and probe.observed_decimals == expected_decimals
    )
    if not probe.ok:
        probe.error = (
            f"expected {expected_symbol}/{expected_decimals} "
            f"got {probe.observed_symbol}/{probe.observed_decimals}"
        )
    return probe


def validate_at_startup(
    rpc_url: Optional[str] = None,
    eth_call: Optional[EthCall] = None,
    timeout: float = 2.5,
) -> TokenProbeReport:
    """Catalog integrity always; on-chain symbol/decimals when RPC is reachable."""
    notes: List[str] = []
    catalog_ok = True
    try:
        assert_live_not_stale()
    except RuntimeError as exc:
        catalog_ok = False
        notes.append(str(exc))

    rpc = (rpc_url or os.getenv("BASE_RPC_URL") or os.getenv("WEB3_PROVIDER") or "").strip()
    report = TokenProbeReport(
        ok=catalog_ok,
        chain_id=BASE_CHAIN_ID,
        rpc=rpc or "(offline)",
        catalog_ok=catalog_ok,
        notes=notes,
    )
    if eth_call is None:
        if not rpc:
            notes.append("no BASE_RPC_URL; skipped symbol/decimals eth_call")
            report.ok = catalog_ok
            return report
        eth_call = http_eth_call(rpc, timeout=timeout)

    live = catalog()
    axiom = probe_token(
        name="AXM",
        address=str(live["axiom"]["address"]),
        expected_symbol=AXM_SYMBOL,
        expected_decimals=AXM_DECIMALS,
        eth_call=eth_call,
    )
    sinc = probe_token(
        name="SINC",
        address=str(live["sinc"]["address"]),
        expected_symbol=SINC_SYMBOL,
        expected_decimals=SINC_DECIMALS,
        eth_call=eth_call,
    )
    report.probes = [axiom, sinc]
    report.ok = catalog_ok and axiom.ok and sinc.ok
    if axiom.error:
        notes.append(axiom.error)
    if sinc.error:
        notes.append(sinc.error)
    report.notes = notes
    logger.info("onchain probe %s treasury=%s", report.summary(), TREASURY)
    return report


# Re-export resolved live pointers for modules that previously used env defaults.
AXIOM_CONTRACT = resolve_address("AXIOM_CONTRACT_ADDRESS", AXIOM_TOKEN)
SINC_CONTRACT = resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN)
TREASURY_WALLET = resolve_address("TREASURY_ADDRESS", TREASURY)
