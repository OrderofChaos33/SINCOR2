"""Read-only Base Sepolia RPC and gas-balance preflight.

This module never signs or broadcasts transactions and never calls a faucet.
It is intended to make testnet readiness explicit before a developer runs a
separate test cycle. Public RPC providers can observe queried addresses.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable

BASE_SEPOLIA_CHAIN_ID = 84532
WEI_PER_ETH = 10**18
DEFAULT_MINIMUM_GAS_ETH = "0.05"
MAX_ADDRESSES = 32
MAX_RPC_RESPONSE_BYTES = 64 * 1024
DEFAULT_RPC_PROVIDERS = (
    ("base_official", "https://sepolia.base.org"),
    ("publicnode", "https://base-sepolia-rpc.publicnode.com"),
)
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class TestnetPreflightError(RuntimeError):
    """Base Sepolia preflight configuration or provider failure."""


class WrongNetworkError(TestnetPreflightError):
    """The selected RPC endpoint is not Base Sepolia."""


class RpcRequestError(TestnetPreflightError):
    """All allowed RPC endpoints failed a bounded read request."""


@dataclass(frozen=True)
class BalanceReport:
    provider: str
    chain_id: int
    minimum_wei: int
    balances_wei: dict[str, int]

    @property
    def underfunded(self) -> tuple[str, ...]:
        return tuple(
            address for address, balance in self.balances_wei.items()
            if balance < self.minimum_wei
        )


def eth_to_wei(value: str | Decimal) -> int:
    """Convert a non-negative decimal ETH amount to exact integer wei."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TestnetPreflightError("minimum gas balance must be a decimal ETH amount") from exc
    if not amount.is_finite() or amount < 0:
        raise TestnetPreflightError("minimum gas balance must be finite and non-negative")
    if amount > Decimal((1 << 256) - 1) / WEI_PER_ETH:
        raise TestnetPreflightError("minimum gas balance exceeds the EVM uint256 range")
    wei = amount * WEI_PER_ETH
    if wei != wei.to_integral_value():
        raise TestnetPreflightError("minimum gas balance has precision smaller than one wei")
    return int(wei)


def validate_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    """Validate and deduplicate addresses while preserving input order."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in addresses:
        address = str(raw).strip()
        if not _ADDRESS_RE.fullmatch(address):
            raise TestnetPreflightError(f"invalid EVM address: {address!r}")
        key = address.lower()
        if key not in seen:
            result.append(address)
            seen.add(key)
            if len(result) > MAX_ADDRESSES:
                raise TestnetPreflightError(
                    f"at most {MAX_ADDRESSES} addresses may be checked per run"
                )
    if not result:
        raise TestnetPreflightError(
            "provide at least one address with --address or BASE_SEPOLIA_TEST_ADDRESSES"
        )
    if len(result) > MAX_ADDRESSES:
        raise TestnetPreflightError(f"at most {MAX_ADDRESSES} addresses may be checked per run")
    return tuple(result)


def rpc_providers(override_url: str | None = None) -> tuple[tuple[str, str], ...]:
    """Return an optional operator endpoint followed by documented public RPCs.

    HTTPS is required. The URL itself is intentionally never included in
    errors or normal output because provider URLs may contain access tokens.
    """
    candidates: list[tuple[str, str]] = []
    if override_url:
        url = override_url.strip()
        if not url.lower().startswith("https://"):
            raise TestnetPreflightError("BASE_SEPOLIA_RPC_URL must use HTTPS")
        candidates.append(("configured", url))
    candidates.extend((name, url) for name, url in DEFAULT_RPC_PROVIDERS
                      if url not in {candidate_url for _, candidate_url in candidates})
    return tuple(candidates)


def _rpc_call(
    url: str,
    method: str,
    params: list[object],
    *,
    opener: Callable = urllib.request.urlopen,
    timeout_s: float = 5.0,
) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with opener(request, timeout=timeout_s) as response:
            raw = response.read(MAX_RPC_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RPC_RESPONSE_BYTES:
                raise RpcRequestError("RPC response exceeded the configured size limit")
            payload = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RpcRequestError(f"RPC request failed ({type(exc).__name__})") from None
    if not isinstance(payload, dict) or payload.get("error") is not None or "result" not in payload:
        raise RpcRequestError("RPC returned an invalid or error response")
    return payload["result"]


def inspect_balances(
    addresses: Iterable[str],
    *,
    minimum_wei: int,
    override_url: str | None = None,
    opener: Callable = urllib.request.urlopen,
) -> BalanceReport:
    """Read address balances from Base Sepolia, trying bounded RPC fallbacks.

    An explicitly configured endpoint returning a wrong chain ID is a hard
    configuration error; the checker does not silently mask it with fallback.
    Transport/provider failures may fall through to the next public endpoint.
    """
    validated = validate_addresses(addresses)
    if minimum_wei < 0:
        raise TestnetPreflightError("minimum balance cannot be negative")

    last_error: TestnetPreflightError | None = None
    for provider, url in rpc_providers(override_url):
        try:
            raw_chain_id = _rpc_call(url, "eth_chainId", [], opener=opener)
            try:
                chain_id = int(str(raw_chain_id), 16)
            except (TypeError, ValueError) as exc:
                raise RpcRequestError("RPC returned a malformed chain ID") from exc
            if chain_id != BASE_SEPOLIA_CHAIN_ID:
                raise WrongNetworkError(
                    f"RPC endpoint {provider} returned chain ID {chain_id}; "
                    f"expected {BASE_SEPOLIA_CHAIN_ID}"
                )
            balances: dict[str, int] = {}
            for address in validated:
                raw = _rpc_call(
                    url, "eth_getBalance", [address, "latest"], opener=opener
                )
                try:
                    balances[address] = int(str(raw), 16)
                except (TypeError, ValueError) as exc:
                    raise RpcRequestError("RPC returned a malformed balance") from exc
            return BalanceReport(provider, chain_id, minimum_wei, balances)
        except WrongNetworkError:
            raise
        except TestnetPreflightError as exc:
            last_error = exc

    detail = str(last_error) if last_error else "no RPC providers configured"
    raise RpcRequestError(f"Base Sepolia RPC preflight failed: {detail}")


def enforce_relayer_testnet_gas(w3: object, address: str, minimum_wei: int) -> int:
    """Fail closed before signing if a relayer is configured for Sepolia.

    ``AUCTION_REQUIRE_BASE_SEPOLIA=true`` additionally rejects any non-Sepolia
    chain. The check is read-only and does not top up the wallet.
    """
    from os import environ

    eth = getattr(w3, "eth", None)
    if eth is None:
        raise TestnetPreflightError("Web3 client is missing eth interface")
    if minimum_wei < 0:
        raise TestnetPreflightError("minimum balance cannot be negative")
    try:
        chain_id = int(eth.chain_id)
    except Exception as exc:
        raise TestnetPreflightError("could not verify RPC chain ID") from exc

    required = environ.get("AUCTION_REQUIRE_BASE_SEPOLIA", "false").strip().lower()
    if required not in {"true", "false", "1", "0"}:
        raise TestnetPreflightError("AUCTION_REQUIRE_BASE_SEPOLIA must be true or false")
    if required in {"true", "1"} and chain_id != BASE_SEPOLIA_CHAIN_ID:
        raise WrongNetworkError(
            f"auction requires Base Sepolia ({BASE_SEPOLIA_CHAIN_ID}); RPC is chain {chain_id}"
        )
    if chain_id != BASE_SEPOLIA_CHAIN_ID:
        return chain_id

    try:
        balance = int(eth.get_balance(address))
    except Exception as exc:
        raise TestnetPreflightError("could not read Base Sepolia relayer balance") from exc
    if balance < minimum_wei:
        raise TestnetPreflightError(
            "Base Sepolia relayer balance below minimum gas threshold "
            f"({balance} wei available; {minimum_wei} wei required); "
            "transaction was not signed or broadcast"
        )
    return chain_id
