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
    """Compile both contracts; returns (compiled, standard_json_input).

    The standard JSON input is the exact compiler input — saving it is
    what makes the deployment byte-for-byte verifiable later.
    """
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
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object",
                                            "evm.deployedBytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION,
                                 allow_paths=CONTRACTS_DIR)
    contracts = out["contracts"]

    def _triple(source_unit: str, name: str):
        c = contracts[source_unit][name]
        return (c["abi"],
                c["evm"]["bytecode"]["object"],
                c["evm"]["deployedBytecode"]["object"])

    compiled = {
        "auction": _triple("CommitRevealAuction.sol", "CommitRevealAuction"),
        "escrow": _triple("ExecutionEscrowManager.sol",
                          "ExecutionEscrowManager"),
    }
    return compiled, std


def deploy(w3, account, compiled_triple, args=(), value=0):
    abi, bytecode, _runtime = compiled_triple
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor(*args).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
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
    ap.add_argument("--verify", action="store_true",
                    help="attempt Basescan source verification (needs BASESCAN_API_KEY)")
    ap.add_argument("--i-understand-mainnet", action="store_true")
    args = ap.parse_args()

    chain_id = BASE_SEPOLIA_CHAIN if args.sepolia else BASE_MAINNET_CHAIN
    rpc = os.environ.get("BASE_RPC",
                         BASE_SEPOLIA_RPC if args.sepolia else BASE_MAINNET_RPC)
    key = os.environ.get("DEPLOYER_KEY", "").strip()
    adjudicator = os.environ.get("ADJUDICATOR_ADDRESS", "").strip()

    print(f"Compiling with solc {SOLC_VERSION} (via-IR, optimizer 200 runs)...")
    compiled, std_input = compile_contracts()
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

    # --- compliance checkpoint: read every deploy, not just the first ------
    print("""
    == COMPLIANCE CHECKPOINT ==
    - The SINCOR token is platform access for the A2A ecosystem, NOT an
      investment. Nothing in this ceremony promises returns, price floors,
      or yield.
    - These contracts are ETH-settled auction/escrow mechanics, not a sale.
    - Adjudication is single-key for now; decentralization is an OPEN item.
      Do not represent dispute resolution as decentralized.
    - Fee policy: 5% to treasury, converted to USDC/WETH, NO burn.
      Do not state otherwise in any copy around this deploy.
    - Legal review is still advisable before any token/equity sale.
    """)
    if not args.sepolia:
        print("Mainnet: the --i-understand-mainnet flag confirms the above.\n")

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
    auction_addr, h1 = deploy(w3, account, compiled["auction"])
    print(f"  -> {auction_addr}  ({h1})")

    print("[2/3] Deploying ExecutionEscrowManager...")
    escrow_args = (auction_addr, adjudicator, MIN_STAKE_BPS, CHALLENGER_BOND_WEI)
    escrow_addr, h2 = deploy(
        w3, account, compiled["escrow"],
        args=escrow_args,
    )
    print(f"  -> {escrow_addr}  ({h2})")

    print("[3/3] Linking auction -> escrow...")
    auction = w3.eth.contract(address=auction_addr, abi=compiled["auction"][0])
    tx = auction.functions.setEscrowManager(escrow_addr).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address, "pending"),
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

    # Byte-for-byte: the code actually on chain must equal this exact
    # compile. Catches toolchain drift, proxy games, and wrong-artifact
    # deploys before any money touches the contracts.
    for label, addr in (("auction", auction_addr), ("escrow", escrow_addr)):
        onchain = bytes(w3.eth.get_code(addr)).hex()
        expected = compiled[label][2].removeprefix("0x")
        assert onchain.lower() == expected.lower(), \
            f"{label}: onchain runtime bytecode differs from local compile"
    print("Verification OK: onchain runtime bytecode matches local compile.")

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
        "optimizer": {"enabled": True, "runs": 200},
        "viaIR": True,
        # ABI-encoded constructor args (Basescan "constructor arguments" field).
        "constructorArgs": {
            "CommitRevealAuction": "0x",
            "ExecutionEscrowManager": "0x" + _encode_constructor_args(
                w3, compiled["escrow"][0], escrow_args).hex(),
        },
    }
    out_dir = os.path.join(REPO, "onchain", "deployments")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"base-{chain_id}-auction.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"Manifest: {out_path}")

    # --- verification artifacts -------------------------------------------
    # Everything needed to verify on Basescan/Sourcify later, even from a
    # different machine: the exact standard-JSON compiler input (reproduces
    # the bytecode byte-for-byte), the ABI + creation bytecode per
    # contract, and the constructor args. The runtime ABI files under
    # src/sincor2/onchain/abis/ are refreshed from this exact compile so
    # the platform always talks to the deployed bytecode with a matching ABI.
    art_dir = os.path.join(out_dir, "artifacts", f"base-{chain_id}-auction")
    os.makedirs(art_dir, exist_ok=True)
    with open(os.path.join(art_dir, "standard-json-input.json"), "w",
              encoding="utf-8") as fh:
        json.dump(std_input, fh, indent=2)
    for label, fname in (("auction", "CommitRevealAuction"),
                         ("escrow", "ExecutionEscrowManager")):
        abi, bytecode = compiled[label]
        with open(os.path.join(art_dir, f"{fname}.abi.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(abi, fh, indent=2)
        with open(os.path.join(art_dir, f"{fname}.bytecode.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("0x" + bytecode)
        abis_dir = os.path.join(REPO, "src", "sincor2", "onchain", "abis")
        os.makedirs(abis_dir, exist_ok=True)
        with open(os.path.join(abis_dir, f"{fname}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"abi": abi}, fh, indent=2)
    print(f"Verification artifacts: {art_dir}/")

    if args.verify:
        _verify_on_basescan(chain_id, manifest, art_dir)

    print("\nExport for the platform (Railway env):")
    print(f"  COMMIT_REVEAL_AUCTION_ADDRESS={auction_addr}")
    print(f"  EXECUTION_ESCROW_ADDRESS={escrow_addr}")
    print(f"  AUCTION_RPC_URL={rpc}")
    print("  AUCTION_ONCHAIN_ANCHOR=1")
    print("\nBasescan verification (manual fallback):")
    print("  https://basescan.org/verifyContract "
          "-> Standard JSON input, solc 0.8.24, optimizer 200 runs, via-IR")
    print(f"  input file: {art_dir}/standard-json-input.json")
    return 0


def _encode_constructor_args(w3, abi, args) -> bytes:
    ctor = next((e for e in abi if e.get("type") == "constructor"), None)
    if ctor is None:
        return b""
    types = [i["type"] for i in ctor.get("inputs", [])]
    from eth_abi import encode

    return encode(types, args)


def _solc_version_string() -> str:
    """Full Basescan compiler string, e.g. v0.8.24+commit.e11b9ed9."""
    import solcx
    import solcx.install
    import subprocess

    solcx.set_solc_version(SOLC_VERSION)
    full = f"v{SOLC_VERSION}"
    try:
        out = subprocess.run(
            [str(solcx.install.get_executable(version=SOLC_VERSION)),
             "--version"],
            capture_output=True, text=True, timeout=15)
        for line in out.stdout.splitlines():
            if "Version:" in line and "commit." in line:
                # 'Version: 0.8.24+commit.e11b9ed9.Linux.g++'
                commit = line.split("commit.")[1].split(".")[0]
                return f"v{SOLC_VERSION}+commit.{commit}"
    except Exception:  # noqa: BLE001 - fall back to the short form
        pass
    return full


def _verify_on_basescan(chain_id: int, manifest: dict, art_dir: str) -> None:
    """Best-effort source verification via the Basescan API.

    Needs BASESCAN_API_KEY. Failures are reported, never fatal — the
    artifacts above are sufficient for manual verification.
    """
    import urllib.parse
    import urllib.request

    api_key = os.environ.get("BASESCAN_API_KEY", "").strip()
    if not api_key:
        print("[verify] BASESCAN_API_KEY not set; skipping auto-verification.")
        return
    api_url = ("https://api-sepolia.basescan.org/api"
               if chain_id == BASE_SEPOLIA_CHAIN
               else "https://api.basescan.org/api")
    with open(os.path.join(art_dir, "standard-json-input.json"),
              encoding="utf-8") as fh:
        source = fh.read()
    for name, key in (("CommitRevealAuction", "CommitRevealAuction"),
                      ("ExecutionEscrowManager", "ExecutionEscrowManager")):
        info = manifest["contracts"][name]
        payload = {
            "module": "contract",
            "action": "verifysourcecode",
            "contractaddress": info["address"],
            "sourceCode": source,
            "codeformat": "solidity-standard-json-input",
            "contractname": f"{name}.sol:{name}",
            "compilerversion": _solc_version_string(),
            "optimizationUsed": 1,
            "runs": 200,
            "constructorArguements":  # sic: Basescan's misspelled param name
                manifest["constructorArgs"][name].removeprefix("0x"),
            "apikey": api_key,
        }
        req = urllib.request.Request(
            api_url, data=urllib.parse.urlencode(payload).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
        except Exception as err:  # noqa: BLE001 - best effort
            print(f"[verify] {name}: request failed: {err}")
            continue
        print(f"[verify] {name}: {result.get('message')} "
              f"-> {result.get('result')}")
        print("         check status with action=checkverifystatus&guid=<result>")


if __name__ == "__main__":
    raise SystemExit(main())
