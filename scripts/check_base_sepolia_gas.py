#!/usr/bin/env python3
"""Read-only Base Sepolia gas-balance preflight; never requests or sends funds."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sincor2.onchain.testnet_preflight import (  # noqa: E402
    DEFAULT_MINIMUM_GAS_ETH,
    TestnetPreflightError,
    eth_to_wei,
    inspect_balances,
)


def _addresses(cli_addresses: list[str]) -> list[str]:
    if cli_addresses:
        return cli_addresses
    return [
        item.strip()
        for item in os.environ.get("BASE_SEPOLIA_TEST_ADDRESSES", "").split(",")
        if item.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only Base Sepolia chain/balance check. No private keys, "
            "faucet requests, signing, or transactions are used."
        )
    )
    parser.add_argument(
        "--address", action="append", default=[],
        help="Test wallet address to inspect (repeatable; never a private key).",
    )
    parser.add_argument(
        "--minimum-eth",
        default=os.environ.get("BASE_SEPOLIA_MIN_GAS_ETH", DEFAULT_MINIMUM_GAS_ETH),
        help=f"Minimum balance per address in ETH (default: {DEFAULT_MINIMUM_GAS_ETH}).",
    )
    args = parser.parse_args(argv)

    try:
        minimum_wei = eth_to_wei(args.minimum_eth)
        report = inspect_balances(
            _addresses(args.address),
            minimum_wei=minimum_wei,
            override_url=os.environ.get("BASE_SEPOLIA_RPC_URL"),
        )
    except TestnetPreflightError as exc:
        print(f"Base Sepolia preflight ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Base Sepolia verified (chain ID {report.chain_id}; provider={report.provider})")
    print(f"Minimum: {args.minimum_eth} ETH per address")
    for address, balance in report.balances_wei.items():
        balance_eth = balance / 10**18
        status = "OK" if balance >= report.minimum_wei else "LOW"
        print(f"{status:4} {address}  {balance_eth:.8f} ETH")
    if report.underfunded:
        print(
            "Preflight failed: fund under-threshold test wallets manually from "
            "an official faucet before starting any testnet transactions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
