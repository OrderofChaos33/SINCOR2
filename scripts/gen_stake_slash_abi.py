#!/usr/bin/env python3
"""Compile contracts/StakeSlashManager.sol -> src/sincor2/onchain/abis/.

Mirrors the auction-contract pipeline (solc 0.8.24, optimizer runs 200,
via-IR) documented in src/sincor2/onchain/auction_client.py. The contract
is self-contained (no external imports), so compile_standard needs no
allow_paths beyond the contracts dir.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import solcx

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
ABIS = ROOT / "src" / "sincor2" / "onchain" / "abis"
SOLC_VERSION = "0.8.24"


def main() -> None:
    solcx.set_solc_version(SOLC_VERSION)
    src = (CONTRACTS / "StakeSlashManager.sol").read_text(encoding="utf-8")
    std = {
        "language": "Solidity",
        "sources": {"StakeSlashManager.sol": {"content": src}},
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION, allow_paths=str(CONTRACTS))
    compiled = out["contracts"]["StakeSlashManager.sol"]["StakeSlashManager"]
    payload = {
        "contractName": "StakeSlashManager",
        "source": "contracts/StakeSlashManager.sol",
        "solc": SOLC_VERSION,
        "abi": compiled["abi"],
        "bytecode": compiled["evm"]["bytecode"]["object"],
    }
    dest = ABIS / "StakeSlashManager.json"
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {dest} ({len(compiled['abi'])} abi entries)")


if __name__ == "__main__":
    sys.exit(main())
