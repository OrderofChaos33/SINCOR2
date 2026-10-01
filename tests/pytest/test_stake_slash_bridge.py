"""Stake/slash design verification (wave 27): StakeSlashManager + stake_bridge.

Self-contained: compiles contracts/StakeSlashManager.sol (plus an inline
mock ERC-20) with solc 0.8.24 via py-solc-x and runs the full money-path
scenario on eth-tester. Nothing is deployed anywhere real; the Python
bridge under test never imports eth_account (signing happens here, in the
test, with throwaway keys).
"""
import os
import sys
import time

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SOLC_VERSION = "0.8.24"
CONTRACTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "contracts")

MOCK_ERC20_SRC = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
contract MockAXM {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }
    function approve(address s, uint256 a) external returns (bool) {
        allowance[msg.sender][s] = a; return true;
    }
    function transfer(address to, uint256 a) external returns (bool) {
        require(balanceOf[msg.sender] >= a, "bal");
        balanceOf[msg.sender] -= a; balanceOf[to] += a; return true;
    }
    function transferFrom(address f, address t, uint256 a) external returns (bool) {
        require(balanceOf[f] >= a, "bal");
        uint256 al = allowance[f][msg.sender];
        if (al != type(uint256).max) {
            require(al >= a, "allow");
            allowance[f][msg.sender] = al - a;
        }
        balanceOf[f] -= a; balanceOf[t] += a; return true;
    }
}
"""


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    with open(os.path.join(CONTRACTS_DIR, "StakeSlashManager.sol")) as f:
        mgr_src = f.read()
    std = {
        "language": "Solidity",
        "sources": {
            "StakeSlashManager.sol": {"content": mgr_src},
            "MockAXM.sol": {"content": MOCK_ERC20_SRC},
        },
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION, allow_paths=CONTRACTS_DIR)
    contracts = out["contracts"]
    return {
        "mgr": (
            contracts["StakeSlashManager.sol"]["StakeSlashManager"]["abi"],
            contracts["StakeSlashManager.sol"]["StakeSlashManager"]["evm"]["bytecode"]["object"],
        ),
        "token": (
            contracts["MockAXM.sol"]["MockAXM"]["abi"],
            contracts["MockAXM.sol"]["MockAXM"]["evm"]["bytecode"]["object"],
        ),
    }


COMPILED = _compile()

from sincor2.onchain import stake_bridge as bridge_mod  # noqa: E402
from sincor2.onchain.stake_bridge import (  # noqa: E402
    SlashRuling,
    StakeSlashBridge,
    StakeSlashConfig,
    canonicalize_signature,
    slash_struct_hash,
)

MIN_STAKE = 10**18
TIMELOCK = 7 * 24 * 3600


@pytest.fixture()
def env():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    accts = tester.get_accounts()
    deployer, admin, treasury = accts[0], accts[1], accts[2]
    agent, poster, relayer = accts[3], accts[4], accts[5]

    from eth_account import Account

    adjudicator_acct = Account.create()  # throwaway key, test-only

    mgr_abi, mgr_bin = COMPILED["mgr"]
    tok_abi, tok_bin = COMPILED["token"]
    tok = w3.eth.contract(abi=tok_abi, bytecode=tok_bin)
    tok_addr = w3.eth.get_transaction_receipt(
        tok.constructor().transact({"from": deployer})
    ).contractAddress
    token = w3.eth.contract(address=tok_addr, abi=tok_abi)

    mgr = w3.eth.contract(abi=mgr_abi, bytecode=mgr_bin)
    mgr_addr = w3.eth.get_transaction_receipt(
        mgr.constructor(
            admin, adjudicator_acct.address, tok_addr, treasury, MIN_STAKE, TIMELOCK
        ).transact({"from": deployer})
    ).contractAddress
    mgr = w3.eth.contract(address=mgr_addr, abi=mgr_abi)

    # Fund the agent with mock AXM.
    token.functions.mint(agent, 100 * MIN_STAKE).transact({"from": deployer})

    # The throwaway adjudicator key needs gas for setAdjudicator calls.
    w3.eth.send_transaction(
        {"from": deployer, "to": adjudicator_acct.address,
         "value": w3.to_wei(1, "ether")}
    )

    cfg = StakeSlashConfig(
        contract_address=mgr_addr, rpc_url="http://localhost:8545",
        chain_id=w3.eth.chain_id,
    )
    bridge = StakeSlashBridge(config=cfg, w3=w3)
    return {
        "w3": w3, "tester": tester, "mgr": mgr, "token": token,
        "bridge": bridge, "cfg": cfg,
        "admin": admin, "treasury": treasury, "agent": agent,
        "poster": poster, "relayer": relayer,
        "adjudicator": adjudicator_acct,
        "chain_id": w3.eth.chain_id, "mgr_addr": mgr_addr,
    }


def _stake(env, amount):
    env["token"].functions.approve(
        env["mgr_addr"], amount
    ).transact({"from": env["agent"]})
    env["mgr"].functions.stake(amount).transact({"from": env["agent"]})


def _b32(value: int) -> str:
    """Zero-padded 32-byte hex (hex() alone drops leading zero bytes)."""
    return "0x" + hex(value)[2:].zfill(64)


def _sign_ruling(env, ruling):
    from eth_account import Account
    from eth_account.messages import encode_defunct

    digest = slash_struct_hash(env["mgr_addr"], env["chain_id"], ruling)
    signed = Account.sign_message(
        encode_defunct(hexstr=digest.hex()), private_key=env["adjudicator"].key
    )
    # The contract enforces low-S; the adjudicator's signing flow must
    # canonicalize (see stake_bridge.canonicalize_signature).
    return canonicalize_signature(
        (signed.v, _b32(signed.r), _b32(signed.s))
    )


def _ruling(env, **kw):
    now = int(time.time())
    d = dict(
        agent=env["agent"], poster=env["poster"], amount_wei=5 * MIN_STAKE,
        treasury_cut_wei=MIN_STAKE, nonce=0, expiry=now + 3600, reason="ghost",
    )
    d.update(kw)
    return SlashRuling(**d)


# --- staking -----------------------------------------------------------------
def test_stake_records_and_emits(env):
    _stake(env, 3 * MIN_STAKE)
    assert env["mgr"].functions.stakeOf(env["agent"]).call() == 3 * MIN_STAKE
    assert env["mgr"].functions.canParticipate(env["agent"]).call() is True


def test_deposit_calldata_exact_and_dry_run_green(env):
    _amt = 2 * MIN_STAKE
    env["token"].functions.approve(
        env["mgr_addr"], _amt
    ).transact({"from": env["agent"]})
    tx = env["bridge"].build_stake_tx(env["agent"], _amt, sender=env["agent"])
    assert tx["to"].lower() == env["mgr_addr"].lower()
    # selector == stake(uint256)
    from eth_hash.auto import keccak

    assert tx["data"][:10].lower() == "0x" + keccak(b"stake(uint256)")[:4].hex()
    assert env["bridge"].dry_run(tx)["ok"] is True
    # module never imports key-handling libraries (prose mentions are fine)
    src = open(bridge_mod.__file__).read()
    assert "import eth_account" not in src
    assert "from eth_account" not in src


def test_unstake_below_minstake_reverts(env):
    _stake(env, 3 * MIN_STAKE)
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.requestUnstake(3 * MIN_STAKE - MIN_STAKE + 1).transact(
            {"from": env["agent"]}
        )


def test_unstake_timelock_enforced_then_honored(env):
    _stake(env, 3 * MIN_STAKE)
    env["mgr"].functions.requestUnstake(3 * MIN_STAKE).transact(
        {"from": env["agent"]}
    )
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.finalizeUnstake().transact(
            {"from": env["agent"]}
        )
    # travel deterministically past the recorded readyAt (no wall-clock math)
    ready_at = env["mgr"].functions.unstakeReadyAt(env["agent"]).call()
    env["tester"].time_travel(ready_at + 10)
    env["tester"].mine_blocks(1)
    env["mgr"].functions.finalizeUnstake().transact(
        {"from": env["agent"]}
    )
    assert env["mgr"].functions.stakeOf(env["agent"]).call() == 0


# --- slashing ----------------------------------------------------------------
def test_slash_with_adjudicator_signature_executes(env):
    _stake(env, 10 * MIN_STAKE)
    ruling = _ruling(env)
    v, r, s = _sign_ruling(env, ruling)
    txh = env["mgr"].functions.slash(
        ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
        ruling.nonce, ruling.expiry,
        Web3.keccak(text=ruling.reason), v, r, s,
    ).transact({"from": env["relayer"]})  # permissionless relayer
    rcpt = env["w3"].eth.get_transaction_receipt(txh)
    assert rcpt["status"] == 1
    assert env["mgr"].functions.stakeOf(env["agent"]).call() == 5 * MIN_STAKE
    # subsidy-extraction split: treasury cut reclaimed, rest credited to poster
    assert env["token"].functions.balanceOf(env["treasury"]).call() == MIN_STAKE
    assert (
        env["mgr"].functions.posterReAuctionCredits(env["poster"]).call()
        == 4 * MIN_STAKE
    )
    assert env["mgr"].functions.slashNonceOf(env["agent"]).call() == 1
    logs = env["mgr"].events.SlashExecuted().process_receipt(rcpt)
    assert len(logs) == 1 and logs[0]["args"]["amount"] == 5 * MIN_STAKE


def test_slash_replay_reverts(env):
    _stake(env, 10 * MIN_STAKE)
    ruling = _ruling(env)
    v, r, s = _sign_ruling(env, ruling)
    args = (ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
            ruling.nonce, ruling.expiry, Web3.keccak(text=ruling.reason), v, r, s)
    env["mgr"].functions.slash(*args).transact({"from": env["relayer"]})
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(*args).transact({"from": env["relayer"]})


def test_slash_wrong_signer_reverts(env):
    from eth_account import Account
    from eth_account.messages import encode_defunct

    _stake(env, 10 * MIN_STAKE)
    ruling = _ruling(env)
    digest = slash_struct_hash(env["mgr_addr"], env["chain_id"], ruling)
    impostor = Account.create()
    signed = Account.sign_message(
        encode_defunct(hexstr=digest.hex()), private_key=impostor.key
    )
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(
            ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
            ruling.nonce, ruling.expiry, Web3.keccak(text=ruling.reason),
            signed.v, _b32(signed.r), _b32(signed.s),
        ).transact({"from": env["relayer"]})


def test_slash_expired_ruling_reverts(env):
    _stake(env, 10 * MIN_STAKE)
    ruling = _ruling(env, expiry=int(time.time()) - 1)
    v, r, s = _sign_ruling(env, ruling)
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(
            ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
            ruling.nonce, ruling.expiry, Web3.keccak(text=ruling.reason),
            v, r, s,
        ).transact({"from": env["relayer"]})


def test_slash_malleated_signature_reverts(env):
    from eth_account import Account
    from eth_account.messages import encode_defunct

    _stake(env, 10 * MIN_STAKE)
    ruling = _ruling(env)
    digest = slash_struct_hash(env["mgr_addr"], env["chain_id"], ruling)
    signed = Account.sign_message(
        encode_defunct(hexstr=digest.hex()), private_key=env["adjudicator"].key
    )
    # Start from the canonical low-S form, then flip s to its high-S
    # malleated twin (same signer) -- the guard must reject it.
    n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    v0, r0, s0 = canonicalize_signature(
        (signed.v, _b32(signed.r), _b32(signed.s))
    )
    s_high = "0x" + hex(n - int(s0, 16))[2:].zfill(64)
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(
            ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
            ruling.nonce, ruling.expiry, Web3.keccak(text=ruling.reason),
            v0, r0, s_high,
        ).transact({"from": env["relayer"]})


def test_slash_cross_contract_replay_reverts(env):
    """A ruling signed for a different contract address must not verify."""
    _stake(env, 10 * MIN_STAKE)
    ruling = _ruling(env)
    other = "0x" + "11" * 20
    digest = slash_struct_hash(other, env["chain_id"], ruling)
    from eth_account import Account
    from eth_account.messages import encode_defunct

    signed = Account.sign_message(
        encode_defunct(hexstr=digest.hex()), private_key=env["adjudicator"].key
    )
    # Canonicalize so the revert is about the wrong contract, not high-S.
    v, r, s = canonicalize_signature(
        (signed.v, _b32(signed.r), _b32(signed.s))
    )
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(
            ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
            ruling.nonce, ruling.expiry, Web3.keccak(text=ruling.reason),
            v, r, s,
        ).transact({"from": env["relayer"]})


def _send_as_key_holder(w3, key_acct, fn):
    """Send a contract tx from an address whose key the tester doesn't hold
    (test-only raw signing; the bridge module itself never does this)."""
    from eth_account import Account

    tx = fn.build_transaction({
        "from": key_acct.address,
        "nonce": w3.eth.get_transaction_count(key_acct.address),
        "gas": 200_000,
        "gasPrice": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = Account.sign_transaction(tx, private_key=key_acct.key)
    return w3.eth.send_raw_transaction(signed.raw_transaction)


def test_adjudicator_rotation(env):
    from eth_account import Account

    w3 = env["w3"]
    new_adj = Account.create()
    # non-adjudicator cannot rotate
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.setAdjudicator(new_adj.address).transact(
            {"from": env["agent"]}
        )
    # zero address rejected (eth_call: the tester cannot sign for the
    # throwaway adjudicator key, so assert the revert off the pending state)
    with pytest.raises(Exception):
        env["mgr"].functions.setAdjudicator(
            "0x0000000000000000000000000000000000000000"
        ).call({"from": env["adjudicator"].address})
    _stake(env, 10 * MIN_STAKE)
    old_env = dict(env)
    txh = _send_as_key_holder(
        w3, env["adjudicator"],
        env["mgr"].functions.setAdjudicator(new_adj.address),
    )
    assert w3.eth.get_transaction_receipt(txh)["status"] == 1
    assert env["mgr"].functions.adjudicator().call() == new_adj.address
    # old adjudicator's ruling no longer verifies
    ruling = _ruling(old_env)
    v, r, s = _sign_ruling(old_env, ruling)
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(
            ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
            ruling.nonce, ruling.expiry, Web3.keccak(text=ruling.reason),
            v, r, s,
        ).transact({"from": env["relayer"]})
    # new adjudicator's ruling verifies
    env2 = dict(env)
    env2["adjudicator"] = new_adj
    ruling = _ruling(env2)
    v, r, s = _sign_ruling(env2, ruling)
    txh = env["mgr"].functions.slash(
        ruling.agent, ruling.poster, ruling.amount_wei, ruling.treasury_cut_wei,
        ruling.nonce, ruling.expiry, Web3.keccak(text=ruling.reason),
        v, r, s,
    ).transact({"from": env["relayer"]})
    assert env["w3"].eth.get_transaction_receipt(txh)["status"] == 1


def test_build_slash_tx_dry_run(env):
    _stake(env, 10 * MIN_STAKE)
    ruling = _ruling(env)
    v, r, s = _sign_ruling(env, ruling)
    tx = env["bridge"].build_slash_tx(ruling, (v, r, s), sender=env["relayer"])
    assert tx["to"].lower() == env["mgr_addr"].lower()
    assert env["bridge"].dry_run(tx)["ok"] is True
