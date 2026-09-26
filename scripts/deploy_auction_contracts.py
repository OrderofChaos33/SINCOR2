#!/usr/bin/env python3
"""Deploy the SINCOR2 sealed-bid auction contracts to Base.

Deploys (in order):
  1. CommitRevealAuction (no constructor args; deployer becomes owner)
  2. ExecutionEscrowManager(auction, adjudicator, minStakeBps=5000,
     challengerBond=0.02 ETH)
  3. auction.setEscrowManager(escrow)  (owner-only link)
Then verifies the wiring on-chain and writes a deployment manifest to
``onchain/deployments/base-<chainid>-auction.json``.

Usage:
  export DEPLOYER_KEY=0x...            # NEVER commit; Secure Vault in prod
  export ADJUDICATOR_ADDRESS=0x...     # single-key for now (decentralization open)
  python scripts/deploy_auction_contracts.py [--sepolia] [--dry-run]

  --sepolia  : rehearse on Base Sepolia (chain 84532) instead of mainnet (8453)
  --dry-run  : compile + print the deployment plan without sending anything

The deployer needs a little Base ETH for gas (~$2-5 total). The script
refuses to run on mainnet without --i-understand-mainnet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACTS_DIR = os.path.join(REPO, "contracts")
sys.path.insert(0, os.path.join(REPO, "src"))

SOLC_VERSION = "0.8.24"
MIN_STAKE_BPS = 5000
CHALLENGER_BOND_WEI = int(0.02 * 10**18)

BASE_MAINNET_RPC = "https://base-rpc.publicnode.com"
BASE_SEPOLIA_RPC = "https://sepolia.base.org"
BASE_MAINNET_CHAIN = 8453
BASE_SEPOLIA_CHAIN = 84532


def compile_contracts():
    import solcx

    solcx.set_solc_version(SOLC_VERSION)
    with open(os.path.join(CONTRACTS_DIR, "CommitRevealAuction.sol")) as f:
        auction_src = f.read()
    with open(os.path.join(CONTRACTS_DIR, "ExecutionEscrowManager.sol")) as f:
        escrow_src = f.read()
    with open(os.path.join(CONTRACTS_DIR, "IExecutionEscrowManager.sol")) as f:
        iface_src = f.read()
    std = {
        "language": "Solidity",
        "sources": {
            "CommitRevealAuction.sol": {"content": auction_src},
            "ExecutionEscrowManager.sol": {"content": escrow_src},
            "IExecutionEscrowManager.sol": {"content": iface_src},
        },
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION,
                                 allow_paths=CONTRACTS_DIR)
    contracts = out["contracts"]
    return {
        "auction": (
            contracts["CommitRevealAuction.sol"]["CommitRevealAuction"]["abi"],
            contracts["CommitRevealAuction.sol"]["CommitRevealAuction"]["evm"]["bytecode"]["object"],
        ),
        "escrow": (
            contracts["ExecutionEscrowManager.sol"]["ExecutionEscrowManager"]["abi"],
            contracts["ExecutionEscrowManager.sol"]["ExecutionEscrowManager"]["evm"]["bytecode"]["object"],
        ),
    }


def deploy(w3, account, abi, bytecode, args=(), value=0):
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor(*args).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gasPrice": w3.eth.gas_price,
        "value": value,
        "chainId": w3.eth.chain_id,
    })
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt.status != 1:
        raise RuntimeError(f"deployment reverted: {tx_hash.hex()}")
    return receipt.contractAddress, tx_hash.hex()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sepolia", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--i-understand-mainnet", action="store_true")
    args = ap.parse_args()

    chain_id = BASE_SEPOLIA_CHAIN if args.sepolia else BASE_MAINNET_CHAIN
    rpc = os.environ.get("BASE_RPC",
                         BASE_SEPOLIA_RPC if args.sepolia else BASE_MAINNET_RPC)
    key = os.environ.get("DEPLOYER_KEY", "").strip()
    adjudicator = os.environ.get("ADJUDICATOR_ADDRESS", "").strip()

    print(f"Compiling with solc {SOLC_VERSION} (via-IR)...")
    compiled = compile_contracts()
    print("  CommitRevealAuction bytecode:",
          len(compiled["auction"][1]) // 2, "bytes")
    print("  ExecutionEscrowManager bytecode:",
          len(compiled["escrow"][1]) // 2, "bytes")

    if args.dry_run:
        print("\n[dry-run] Deployment plan:")
        print(f"  1. CommitRevealAuction() on chain {chain_id}")
        print(f"  2. ExecutionEscrowManager(auction, {adjudicator or '<ADJUDICATOR_ADDRESS>'}, "
              f"{MIN_STAKE_BPS}, {CHALLENGER_BOND_WEI} wei)")
        print("  3. auction.setEscrowManager(escrow)")
        print("  4. on-chain verification reads + manifest write")
        return 0

    if not key:
        print("ERROR: DEPLOYER_KEY is not set (export it; never commit it).",
              file=sys.stderr)
        return 2
    if not adjudicator:
        print("ERROR: ADJUDICATOR_ADDRESS is not set.", file=sys.stderr)
        return 2
    if not args.sepolia and not args.i_understand_mainnet:
        print("ERROR: mainnet deploy requires --i-understand-mainnet.",
              file=sys.stderr)
        return 2

    from web3 import Web3
    from eth_account import Account

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if w3.eth.chain_id != chain_id:
        print(f"ERROR: RPC chain {w3.eth.chain_id} != expected {chain_id}",
              file=sys.stderr)
        return 2
    account = Account.from_key(key)
    balance = w3.eth.get_balance(account.address)
    print(f"Deployer: {account.address}  balance: {w3.from_wei(balance, 'ether')} ETH")
    if balance < w3.to_wei(0.001, "ether"):
        print("ERROR: deployer needs Base ETH for gas.", file=sys.stderr)
        return 2

    print("\n[1/3] Deploying CommitRevealAuction...")
    auction_addr, h1 = deploy(w3, account, *compiled["auction"])
    print(f"  -> {auction_addr}  ({h1})")

    print("[2/3] Deploying ExecutionEscrowManager...")
    escrow_addr, h2 = deploy(
        w3, account, *compiled["escrow"],
        args=(auction_addr, adjudicator, MIN_STAKE_BPS, CHALLENGER_BOND_WEI),
    )
    print(f"  -> {escrow_addr}  ({h2})")

    print("[3/3] Linking auction -> escrow...")
    auction = w3.eth.contract(address=auction_addr, abi=compiled["auction"][0])
    tx = auction.functions.setEscrowManager(escrow_addr).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gasPrice": w3.eth.gas_price,
        "chainId": chain_id,
    })
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    signed = account.sign_transaction(tx)
    h3 = w3.eth.send_raw_transaction(getattr(signed, "raw_transaction", None) or signed.rawTransaction).hex()
    receipt = w3.eth.wait_for_transaction_receipt(h3, timeout=300)
    if receipt.status != 1:
        raise RuntimeError(f"setEscrowManager reverted: {h3}")
    print(f"  -> {h3}")

    # --- on-chain verification ------------------------------------------------
    escrow = w3.eth.contract(address=escrow_addr, abi=compiled["escrow"][0])
    linked = auction.functions.escrowManager().call()
    core = escrow.functions.auctionCore().call()
    bps = escrow.functions.minStakeBps().call()
    bond = escrow.functions.challengerBond().call()
    assert linked.lower() == escrow_addr.lower(), "escrow link mismatch"
    assert core.lower() == auction_addr.lower(), "auction core mismatch"
    assert bps == MIN_STAKE_BPS, "minStakeBps mismatch"
    assert bond == CHALLENGER_BOND_WEI, "challengerBond mismatch"
    print("\nVerification OK: contracts linked, params match ratified values.")

    manifest = {
        "network": "base-sepolia" if args.sepolia else "base",
        "chainId": chain_id,
        "deployedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deployer": account.address,
        "adjudicator": adjudicator,
        "minStakeBps": MIN_STAKE_BPS,
        "challengerBondWei": str(CHALLENGER_BOND_WEI),
        "contracts": {
            "CommitRevealAuction": {"address": auction_addr, "deployTx": h1},
            "ExecutionEscrowManager": {"address": escrow_addr, "deployTx": h2},
        },
        "linkTx": h3,
        "solc": SOLC_VERSION,
    }
    out_dir = os.path.join(REPO, "onchain", "deployments")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"base-{chain_id}-auction.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"Manifest: {out_path}")

    print("\nExport for the platform (Railway env):")
    print(f"  COMMIT_REVEAL_AUCTION_ADDRESS={auction_addr}")
    print(f"  EXECUTION_ESCROW_ADDRESS={escrow_addr}")
    print(f"  AUCTION_RPC_URL={rpc}")
    print("  AUCTION_ONCHAIN_ANCHOR=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
