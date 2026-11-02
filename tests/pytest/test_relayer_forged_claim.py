"""Dev-watch 306: a relayer is transport, never authority.

Grounding (verified on main 4de2e165): this codebase has NO production
bridge/relayer path that accepts a third-party submitted batch root or
claim and releases funds on it.

  * ``OptimisticBatchSettlement.submitRoot`` (the only batch-root surface)
    is operator-only (``msg.sender != operator`` reverts) and the contract
    holds/releases no funds at all.
  * ``AuctionRelayer`` (``onchain/auction_relayer.py``) is outbound only:
    it signs the platform's own poster transactions with the platform key;
    the winner/price are derived onchain by ``vickreyResult`` and
    ``CommitRevealAuction.selectWinnerAndFund`` is poster-only onchain.
  * The nearest real relayer-submitted claim that DOES move funds is
    ``StakeSlashManager.slash()``, submitted through
    ``stake_bridge.build_slash_tx``: submission is permissionless ("anyone
    may submit; the contract enforces the adjudicator signature on-chain"),
    and on success it transfers ``treasuryCut`` to the treasury and credits
    the poster's re-auction balance.

These tests prove the constraint at that surface: a forged claim — wrong
signer, tampered payload (amount / poster / treasury cut / agent / reason
changed after signing), a zero signature, or a replayed signature —
submitted by an arbitrary relayer is REJECTED (reverts / dry-run refuses,
never a permissive fallback) and releases ZERO funds: every balance,
credit, stake and nonce is byte-identical before and after the attempt.
A forged batch root from a non-operator is likewise rejected and stores
nothing.
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
    sources = {}
    for name in ("StakeSlashManager.sol", "OptimisticBatchSettlement.sol"):
        with open(os.path.join(CONTRACTS_DIR, name)) as f:
            sources[name] = {"content": f.read()}
    sources["MockAXM.sol"] = {"content": MOCK_ERC20_SRC}
    std = {
        "language": "Solidity",
        "sources": sources,
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION,
                                 allow_paths=CONTRACTS_DIR)
    contracts = out["contracts"]

    def entry(file, name):
        c = contracts[file][name]
        return c["abi"], c["evm"]["bytecode"]["object"]

    return {
        "mgr": entry("StakeSlashManager.sol", "StakeSlashManager"),
        "batch": entry("OptimisticBatchSettlement.sol",
                       "OptimisticBatchSettlement"),
        "token": entry("MockAXM.sol", "MockAXM"),
    }


COMPILED = _compile()

from sincor2.onchain.stake_bridge import (  # noqa: E402
    SlashRuling,
    StakeSlashBridge,
    StakeSlashConfig,
    canonicalize_signature,
    slash_struct_hash,
)

MIN_STAKE = 10 ** 18
TIMELOCK = 7 * 24 * 3600


@pytest.fixture()
def env():
    from eth_account import Account

    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    accts = tester.get_accounts()
    deployer, admin, treasury = accts[0], accts[1], accts[2]
    agent, poster, relayer = accts[3], accts[4], accts[5]
    agent2, attacker_poster, operator = accts[6], accts[7], accts[8]
    adjudicator_acct = Account.create()  # throwaway key, test-only

    tok_abi, tok_bin = COMPILED["token"]
    tok_addr = w3.eth.get_transaction_receipt(
        w3.eth.contract(abi=tok_abi, bytecode=tok_bin).constructor()
        .transact({"from": deployer})).contractAddress
    token = w3.eth.contract(address=tok_addr, abi=tok_abi)

    mgr_abi, mgr_bin = COMPILED["mgr"]
    mgr_addr = w3.eth.get_transaction_receipt(
        w3.eth.contract(abi=mgr_abi, bytecode=mgr_bin).constructor(
            admin, adjudicator_acct.address, tok_addr, treasury,
            MIN_STAKE, TIMELOCK).transact({"from": deployer})
    ).contractAddress
    mgr = w3.eth.contract(address=mgr_addr, abi=mgr_abi)

    batch_abi, batch_bin = COMPILED["batch"]
    batch_addr = w3.eth.get_transaction_receipt(
        w3.eth.contract(abi=batch_abi, bytecode=batch_bin).constructor(
            treasury, operator).transact({"from": deployer})
    ).contractAddress
    batch = w3.eth.contract(address=batch_addr, abi=batch_abi)

    for who in (agent, agent2):
        token.functions.mint(who, 100 * MIN_STAKE).transact(
            {"from": deployer})

    cfg = StakeSlashConfig(contract_address=mgr_addr,
                           rpc_url="http://localhost:8545",
                           chain_id=w3.eth.chain_id)
    return {
        "w3": w3, "tester": tester, "mgr": mgr, "token": token,
        "batch": batch, "batch_addr": batch_addr,
        "bridge": StakeSlashBridge(config=cfg, w3=w3),
        "admin": admin, "treasury": treasury, "agent": agent,
        "agent2": agent2, "poster": poster, "relayer": relayer,
        "attacker_poster": attacker_poster, "operator": operator,
        "adjudicator": adjudicator_acct,
        "chain_id": w3.eth.chain_id, "mgr_addr": mgr_addr,
    }


# ── helpers ─────────────────────────────────────────────────────────────────

def _stake(env, agent, amount):
    env["token"].functions.approve(env["mgr_addr"], amount).transact(
        {"from": agent})
    env["mgr"].functions.stake(amount).transact({"from": agent})


def _b32(value: int) -> str:
    return "0x" + hex(value)[2:].zfill(64)


def _ruling(env, **kw):
    d = dict(agent=env["agent"], poster=env["poster"],
             amount_wei=5 * MIN_STAKE, treasury_cut_wei=MIN_STAKE,
             nonce=0, expiry=int(time.time()) + 3600, reason="ghost")
    d.update(kw)
    return SlashRuling(**d)


def _sign(env, ruling, key=None):
    """Canonical (low-S) signature over the ruling's struct hash."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    digest = slash_struct_hash(env["mgr_addr"], env["chain_id"], ruling)
    signed = Account.sign_message(
        encode_defunct(hexstr=digest.hex()),
        private_key=(key or env["adjudicator"]).key)
    return canonicalize_signature((signed.v, _b32(signed.r), _b32(signed.s)))


def _slash_args(env, ruling, v, r, s, **overrides):
    fields = dict(agent=ruling.agent, poster=ruling.poster,
                  amount=ruling.amount_wei, cut=ruling.treasury_cut_wei,
                  nonce=ruling.nonce, expiry=ruling.expiry,
                  reason=ruling.reason)
    fields.update(overrides)
    return (fields["agent"], fields["poster"], fields["amount"],
            fields["cut"], fields["nonce"], fields["expiry"],
            Web3.keccak(text=fields["reason"]), v, r, s)


def _snapshot(env):
    """Every value a successful slash could move or mutate."""
    mgr, token = env["mgr"], env["token"]
    return {
        "stake_agent": mgr.functions.stakeOf(env["agent"]).call(),
        "stake_agent2": mgr.functions.stakeOf(env["agent2"]).call(),
        "nonce_agent": mgr.functions.slashNonceOf(env["agent"]).call(),
        "nonce_agent2": mgr.functions.slashNonceOf(env["agent2"]).call(),
        "credit_poster": mgr.functions.posterReAuctionCredits(
            env["poster"]).call(),
        "credit_attacker": mgr.functions.posterReAuctionCredits(
            env["attacker_poster"]).call(),
        "bal_treasury": token.functions.balanceOf(env["treasury"]).call(),
        "bal_mgr": token.functions.balanceOf(env["mgr_addr"]).call(),
        "bal_agent": token.functions.balanceOf(env["agent"]).call(),
        "bal_poster": token.functions.balanceOf(env["poster"]).call(),
        "bal_attacker": token.functions.balanceOf(
            env["attacker_poster"]).call(),
    }


def _assert_forged_rejected_zero_release(env, args):
    """Submit ``args`` as the (untrusted) relayer: must revert, and the
    full money snapshot must be unchanged — zero funds released."""
    before = _snapshot(env)
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(*args).transact(
            {"from": env["relayer"]})
    assert _snapshot(env) == before


# ── forged claims through the relayer path ──────────────────────────────────

def test_forged_wrong_signer_releases_zero_funds(env):
    from eth_account import Account

    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)
    v, r, s = _sign(env, ruling, key=Account.create())  # impostor
    _assert_forged_rejected_zero_release(
        env, _slash_args(env, ruling, v, r, s))


def test_forged_zero_signature_releases_zero_funds(env):
    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)
    _assert_forged_rejected_zero_release(
        env, _slash_args(env, ruling, 27, _b32(0), _b32(0)))


def test_tampered_amount_after_signing_releases_zero_funds(env):
    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)  # adjudicator signs amount = 5, cut = 1
    v, r, s = _sign(env, ruling)
    # Relayer inflates the claim to 9 (still within stake, cut <= amount,
    # nonce/expiry untouched) — only the signature binding can stop it.
    _assert_forged_rejected_zero_release(
        env, _slash_args(env, ruling, v, r, s, amount=9 * MIN_STAKE))


def test_tampered_poster_redirect_releases_zero_funds(env):
    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)
    v, r, s = _sign(env, ruling)
    # Relayer redirects the re-auction credit to itself-controlled poster.
    _assert_forged_rejected_zero_release(
        env, _slash_args(env, ruling, v, r, s,
                         poster=env["attacker_poster"]))


def test_tampered_treasury_cut_after_signing_releases_zero_funds(env):
    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)  # signs cut = 1 of 5
    v, r, s = _sign(env, ruling)
    # Relayer diverts the whole slash to the treasury leg.
    _assert_forged_rejected_zero_release(
        env, _slash_args(env, ruling, v, r, s, cut=5 * MIN_STAKE))


def test_tampered_agent_after_signing_releases_zero_funds(env):
    _stake(env, env["agent"], 10 * MIN_STAKE)
    _stake(env, env["agent2"], 10 * MIN_STAKE)
    ruling = _ruling(env)  # signed against env["agent"]
    v, r, s = _sign(env, ruling)
    # Relayer aims the same signature at a different, fully staked victim.
    _assert_forged_rejected_zero_release(
        env, _slash_args(env, ruling, v, r, s, agent=env["agent2"]))


def test_tampered_reason_after_signing_releases_zero_funds(env):
    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)  # signed reason = "ghost"
    v, r, s = _sign(env, ruling)
    _assert_forged_rejected_zero_release(
        env, _slash_args(env, ruling, v, r, s, reason="quality"))


def test_replayed_signature_releases_zero_additional_funds(env):
    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)
    v, r, s = _sign(env, ruling)
    args = _slash_args(env, ruling, v, r, s)
    # The genuine claim executes exactly once through the relayer.
    txh = env["mgr"].functions.slash(*args).transact(
        {"from": env["relayer"]})
    assert env["w3"].eth.get_transaction_receipt(txh)["status"] == 1
    after_first = _snapshot(env)
    assert after_first["bal_treasury"] == MIN_STAKE
    assert after_first["credit_poster"] == 4 * MIN_STAKE
    # Replay of the identical signature + payload: rejected (nonce
    # consumed), and releases zero additional funds.
    with pytest.raises(TransactionFailed):
        env["mgr"].functions.slash(*args).transact(
            {"from": env["relayer"]})
    assert _snapshot(env) == after_first


def test_forged_claim_via_bridge_dry_run_refuses_and_releases_zero(env):
    from eth_account import Account

    _stake(env, env["agent"], 10 * MIN_STAKE)
    ruling = _ruling(env)
    v, r, s = _sign(env, ruling, key=Account.create())  # impostor
    before = _snapshot(env)
    # The Python submission path (build_slash_tx + dry_run) is the gate a
    # relayer runs before broadcast: a forged claim must raise there —
    # fail-closed, never a permissive fallback that proceeds anyway.
    tx = env["bridge"].build_slash_tx(
        ruling, (v, r, s), sender=env["relayer"], gas=300_000,
        nonce=env["w3"].eth.get_transaction_count(env["relayer"]),
        gas_price_wei=10 ** 9)
    with pytest.raises(RuntimeError, match="dry-run reverted"):
        env["bridge"].dry_run(tx)
    assert _snapshot(env) == before
    # Control: the adjudicator's genuine claim passes the same dry-run.
    v, r, s = _sign(env, ruling)
    good = env["bridge"].build_slash_tx(
        ruling, (v, r, s), sender=env["relayer"], gas=300_000,
        nonce=env["w3"].eth.get_transaction_count(env["relayer"]),
        gas_price_wei=10 ** 9)
    assert env["bridge"].dry_run(good)["ok"] is True
    assert _snapshot(env) == before  # dry-runs alone move nothing


# ── forged batch root (the zkLink failure class, directly) ──────────────────

def test_forged_batch_root_from_non_operator_stores_nothing(env):
    batch = env["batch"]
    forged_root = Web3.keccak(text="forged-batch-root")
    before_next = batch.functions.nextBatchId().call()
    # A public relayer / attacker is not the operator: the root claim is
    # rejected outright, not accepted pending a permissive fallback.
    with pytest.raises(TransactionFailed):
        batch.functions.submitRoot(forged_root, 999).transact(
            {"from": env["relayer"]})
    assert batch.functions.nextBatchId().call() == before_next
    root, count, _block, status, _challenger = batch.functions.batches(
        before_next).call()
    assert root == b"\x00" * 32 and count == 0 and status == 0
    # The contract holds no funds, so a forged root can release none.
    assert env["w3"].eth.get_balance(env["batch_addr"]) == 0
    # Control: the operator's own root is accepted exactly once.
    txh = batch.functions.submitRoot(forged_root, 999).transact(
        {"from": env["operator"]})
    assert env["w3"].eth.get_transaction_receipt(txh)["status"] == 1
    assert batch.functions.nextBatchId().call() == before_next + 1
