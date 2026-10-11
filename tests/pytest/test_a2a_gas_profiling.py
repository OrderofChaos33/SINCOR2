"""Base Sepolia gas regression tripwires for the A2A auction contracts.

Self-contained: compiles contracts with solc 0.8.24 (optimizer 200 runs,
viaIR), drives them with web3 + eth-tester (PyEVM backend), and asserts
each function's gas stays under its bound. Bounds were set 2026-09-29
from measured eth-tester values plus ~25% headroom — they are regression
tripwires, not L2 fee estimates (Base gas pricing differs from PyEVM).

Run with:  pytest tests/pytest/test_a2a_gas_profiling.py -q --confcutdir=tests/pytest
"""

import itertools
import os

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

CONTRACTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
)
SOLC_VERSION = "0.8.24"
MIN_STAKE_BPS = 5000  # ratified 2026-09-25
CHALLENGER_BOND = Web3.to_wei(0.02, "ether")  # ratified 2026-09-25
TX = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}

# Measured 2026-09-29 (eth-tester) + ~25% headroom, rounded.
BOUNDS = {
    "openAuction": 90_000,                    # measured 70,721
    "commit": 125_000,                        # measured 100,515 first / 83,415 10th
    "reveal": 95_000,                         # measured 76,087
    "selectWinnerAndFund_n10": 300_000,        # measured 240,713 (scales ~+6.9k/bidder)
    "auction_timeout": 40_000,                # measured 30,249
    "depositStake": 50_000,                   # measured 39,189
    "escrow_timeout_ghost": 100_000,          # measured 78,397
    "escrow_timeout_no_stake": 70_000,        # measured 52,610
    "submitResult": 100_000,                  # measured 78,558
    "openQualityDispute": 135_000,            # measured 105,647
    "resolveQualityDispute_slash": 110_000,   # measured 85,963
    "resolveQualityDispute_reject": 85_000,   # measured 67,984
}

_aid_seq = itertools.count()


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    srcs = {}
    for name in ("CommitRevealAuction.sol", "ExecutionEscrowManager.sol",
                 "IExecutionEscrowManager.sol",
                 os.path.join("security", "ScopedPausable.sol")):
        with open(os.path.join(CONTRACTS_DIR, name)) as f:
            srcs[name] = {"content": f.read()}
    std = {"language": "Solidity", "sources": srcs, "settings": {
        "optimizer": {"enabled": True, "runs": 200}, "viaIR": True,
        "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}}}}
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION,
                                 allow_paths=CONTRACTS_DIR)["contracts"]
    return ((out["CommitRevealAuction.sol"]["CommitRevealAuction"]["abi"],
             out["CommitRevealAuction.sol"]["CommitRevealAuction"]["evm"]["bytecode"]["object"]),
            (out["ExecutionEscrowManager.sol"]["ExecutionEscrowManager"]["abi"],
             out["ExecutionEscrowManager.sol"]["ExecutionEscrowManager"]["evm"]["bytecode"]["object"]))


COMPILED = _compile()


@pytest.fixture(scope="module")
def env():
    (abi_a, bin_a), (abi_e, bin_e) = COMPILED
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    accts = tester.get_accounts()
    deployer, poster, adjudicator = accts[0], accts[1], accts[2]
    extra = []
    for _ in range(6):
        acct = w3.eth.account.create()
        tester.add_account(acct.key.hex())
        w3.eth.send_transaction({"from": deployer, "to": acct.address,
                                 "value": w3.to_wei(5, "ether"), **TX})
        extra.append(acct.address)
    bidders = accts[3:10] + extra[:3]
    anyone, guardian = extra[3], extra[4]

    def deploy(abi, bin, args):
        c = w3.eth.contract(abi=abi, bytecode=bin)
        txh = c.constructor(*args).transact({"from": deployer, **TX})
        return w3.eth.contract(
            address=w3.eth.get_transaction_receipt(txh).contractAddress, abi=abi)

    auction = deploy(abi_a, bin_a, [guardian])
    escrow = deploy(abi_e, bin_e,
                    [auction.address, adjudicator, MIN_STAKE_BPS, CHALLENGER_BOND, guardian])
    auction.functions.setEscrowManager(escrow.address).transact({"from": deployer, **TX})
    return {"w3": w3, "tester": tester, "auction": auction, "escrow": escrow,
            "poster": poster, "adjudicator": adjudicator, "bidders": bidders,
            "anyone": anyone, "secrets": {}}


def _aid(w3, tag):
    return w3.keccak(text=f"gas-{tag}-{next(_aid_seq)}")


def _travel(env, ts, buf=5):
    env["tester"].time_travel(ts + buf)
    env["tester"].mine_block()


def _gas(w3, fn, txp):
    return w3.eth.get_transaction_receipt(fn.transact(txp)).gasUsed


def _commit(env, aid, bidder, price):
    w3 = env["w3"]
    salt = w3.keccak(text=f"salt-{aid.hex()}-{bidder}")
    agent_id = w3.keccak(text=f"agent-{bidder}")
    # Keep this benchmark in sync with CommitRevealAuction.reveal:
    # keccak256(abi.encodePacked(auctionId, chainId, bytes32(price), salt,
    #                           agentIdHash)).
    com = w3.solidity_keccak(
        ["bytes32", "uint256", "uint256", "bytes32", "bytes32"],
        [aid, w3.eth.chain_id, price, salt, agent_id],
    )
    env["secrets"][(aid, bidder)] = (price, salt, agent_id)
    return _gas(w3, env["auction"].functions.commit(aid, com), {"from": bidder, **TX})


def _reveal(env, aid, bidder):
    w3 = env["w3"]
    price, salt, agent_id = env["secrets"][(aid, bidder)]
    return _gas(w3, env["auction"].functions.reveal(aid, price, salt, agent_id),
                {"from": bidder, **TX})


def _run_auction(env, tag, n):
    """Open, commit, reveal n bids; travel past revealDeadline; return (aid, price)."""
    w3, auction = env["w3"], env["auction"]
    aid = _aid(w3, tag)
    auction.functions.openAuction(aid, 60, 60).transact({"from": env["poster"], **TX})
    for i, b in enumerate(env["bidders"][:n]):
        _commit(env, aid, b, w3.to_wei(1, "ether") + i)
    a = auction.functions.auctions(aid).call()
    _travel(env, a[3])
    for b in env["bidders"][:n]:
        _reveal(env, aid, b)
    a = auction.functions.auctions(aid).call()
    _travel(env, a[4])
    _, price = auction.functions.vickreyResult(aid).call()
    return aid, price


def test_gas_open_commit_reveal(env):
    w3, auction = env["w3"], env["auction"]
    aid = _aid(w3, "ocr")
    g = _gas(w3, auction.functions.openAuction(aid, 60, 60), {"from": env["poster"], **TX})
    assert g < BOUNDS["openAuction"], g
    g_first = _commit(env, aid, env["bidders"][0], w3.to_wei(1, "ether"))
    assert g_first < BOUNDS["commit"], g_first
    for b in env["bidders"][1:10]:
        g = _commit(env, aid, b, w3.to_wei(1, "ether"))
        assert g < BOUNDS["commit"], g
    a = auction.functions.auctions(aid).call()
    _travel(env, a[3])
    for b in env["bidders"][:10]:
        g = _reveal(env, aid, b)
        assert g < BOUNDS["reveal"], g


def test_gas_select_winner_scales_linearly(env):
    w3, auction = env["w3"], env["auction"]
    for n, key in ((1, None), (5, None), (10, "selectWinnerAndFund_n10")):
        aid, price = _run_auction(env, f"sel{n}", n)
        g = _gas(w3, auction.functions.selectWinnerAndFund(aid, 0),
                 {"from": env["poster"], "value": price, **TX})
        if key:
            assert g < BOUNDS[key], g


def test_gas_auction_timeout(env):
    w3, auction = env["w3"], env["auction"]
    aid = _aid(w3, "atimeout")
    auction.functions.openAuction(aid, 60, 60).transact({"from": env["poster"], **TX})
    a = auction.functions.auctions(aid).call()
    _travel(env, a[4])
    g = _gas(w3, auction.functions.timeout(aid), {"from": env["anyone"], **TX})
    assert g < BOUNDS["auction_timeout"], g


def _escrow_with_stake(env, tag, stake_it=True):
    aid, price = _run_auction(env, tag, 2)
    w3, auction, escrow = env["w3"], env["auction"], env["escrow"]
    _gas(w3, auction.functions.selectWinnerAndFund(aid, 0),
         {"from": env["poster"], "value": price, **TX})
    if stake_it:
        stake = price * MIN_STAKE_BPS // 10000
        g = _gas(w3, escrow.functions.depositStake(aid),
                 {"from": env["bidders"][0], "value": stake, **TX})
        assert g < BOUNDS["depositStake"], g
    return aid


def test_gas_escrow_timeout_paths(env):
    w3, escrow = env["w3"], env["escrow"]
    # Ghost: staked, never submitted -> 100% slash path
    aid = _escrow_with_stake(env, "ghost")
    esc = escrow.functions.getEscrow(aid).call()
    _travel(env, esc[8])  # executionDeadline
    g = _gas(w3, escrow.functions.timeout(aid), {"from": env["anyone"], **TX})
    assert g < BOUNDS["escrow_timeout_ghost"], g
    # No stake -> poster refund, no slash
    aid2 = _escrow_with_stake(env, "nostake", stake_it=False)
    esc2 = escrow.functions.getEscrow(aid2).call()
    _travel(env, esc2[6])  # stakeDepositDeadline
    g = _gas(w3, escrow.functions.timeout(aid2), {"from": env["anyone"], **TX})
    assert g < BOUNDS["escrow_timeout_no_stake"], g


def test_gas_dispute_flow(env):
    w3, escrow = env["w3"], env["escrow"]
    for tag, slash, key in (("disp1", True, "resolveQualityDispute_slash"),
                            ("disp2", False, "resolveQualityDispute_reject")):
        aid = _escrow_with_stake(env, tag)
        winner = env["bidders"][0]
        g = _gas(w3, escrow.functions.submitResult(aid, w3.keccak(text="result")),
                 {"from": winner, **TX})
        assert g < BOUNDS["submitResult"], g
        g = _gas(w3, escrow.functions.openQualityDispute(aid, w3.keccak(text="batch")),
                 {"from": env["bidders"][1], "value": CHALLENGER_BOND, **TX})
        assert g < BOUNDS["openQualityDispute"], g
        g = _gas(w3, escrow.functions.resolveQualityDispute(aid, slash),
                 {"from": env["adjudicator"], **TX})
        assert g < BOUNDS[key], g
