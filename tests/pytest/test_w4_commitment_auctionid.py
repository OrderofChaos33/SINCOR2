"""P0 W-4 regression tests: the commitment preimage binds auctionId + chainId.

Covers the cross-auction commitment replay that the W-4 red-team PoC
(poc_w4_commit_replay.py) confirmed: reveal() used to check
keccak(price || salt || agentIdHash) with NO auctionId binding, so a
commitment copied onto a second auction accepted the victim's disclosed
preimage. The fixed preimage is
    keccak256(abi.encodePacked(auctionId, block.chainid, bytes32(price),
                               salt, agentIdHash))

Tests:
  1. cross-auction replay REVERTS (BadReveal) — commit on A, copy the
     public hash onto B, reveal the disclosed preimage on B fails.
  2. honest commit -> reveal on the SAME auction still succeeds.
  3. a commitment hashed with the wrong chain id REVERTS on reveal.

Self-contained: compiles contracts/CommitRevealAuction.sol with solc 0.8.24
and drives it with web3 + eth-tester (PyEVM backend). Run with:

    ~/.venvs/sincor2/bin/python -m pytest --noconftest \\
        tests/pytest/test_w4_commitment_auctionid.py -q
"""

import os

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

CONTRACTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "contracts"))
SOLC_VERSION = "0.8.24"
TX = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    srcs = {}
    for name in ("CommitRevealAuction.sol",
                 os.path.join("security", "ScopedPausable.sol"),
                 "IExecutionEscrowManager.sol"):
        with open(os.path.join(CONTRACTS_DIR, name)) as f:
            srcs[name] = {"content": f.read()}
    std = {
        "language": "Solidity",
        "sources": srcs,
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION,
                                 allow_paths=CONTRACTS_DIR)
    node = out["contracts"]["CommitRevealAuction.sol"]["CommitRevealAuction"]
    return node["abi"], node["evm"]["bytecode"]["object"]


ABI, BYTECODE = _compile()


@pytest.fixture()
def env():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    deployer, poster, victim, attacker = tester.get_accounts()[:4]
    guardian = tester.get_accounts()[4]
    txh = w3.eth.contract(abi=ABI, bytecode=BYTECODE).constructor(
        guardian).transact({"from": deployer, **TX})
    auction = w3.eth.contract(
        address=w3.eth.get_transaction_receipt(txh).contractAddress, abi=ABI)
    return tester, w3, auction, poster, victim, attacker


def _commitment(w3, aid, chain_id, price, salt, agent_id_hash):
    """The contract's preimage:
    keccak256(abi.encodePacked(auctionId, block.chainid, bytes32(price),
                               salt, agentIdHash))."""
    return w3.solidity_keccak(
        ["bytes32", "uint256", "uint256", "bytes32", "bytes32"],
        [aid, chain_id, price, salt, agent_id_hash])


def _travel_reveal(tester, auction, aid):
    ts = auction.functions.auctions(aid).call()[3]  # commitDeadline
    tester.time_travel(ts + 5)
    tester.mine_block()


def test_cross_auction_replay_reverts(env):
    # W-4 attack: victim commits H on auction A; the attacker copies the
    # public hash onto auction B blind, then replays the disclosed preimage
    # after the victim reveals. Reveal on B must be rejected by the auction
    # binding (BadReveal), not accepted at the victim's price.
    tester, w3, auction, poster, victim, attacker = env
    chain_id = w3.eth.chain_id
    aid_a = bytes.fromhex("0a" * 32)
    aid_b = bytes.fromhex("0b" * 32)
    auction.functions.openAuction(aid_a, 60, 600).transact({"from": poster, **TX})
    auction.functions.openAuction(aid_b, 60, 600).transact({"from": poster, **TX})

    price = 1_000_000
    salt = bytes.fromhex("99" * 32)
    agent_id_hash = bytes.fromhex("77" * 32)
    h = _commitment(w3, aid_a, chain_id, price, salt, agent_id_hash)

    # Victim commits on A; attacker mirrors the PUBLIC hash on B (preimage
    # unknown at commit time).
    auction.functions.commit(aid_a, h).transact({"from": victim, **TX})
    auction.functions.commit(aid_b, h).transact({"from": attacker, **TX})

    _travel_reveal(tester, auction, aid_a)
    _travel_reveal(tester, auction, aid_b)

    # Victim reveals on A: preimage is now public.
    auction.functions.reveal(aid_a, price, salt, agent_id_hash).transact(
        {"from": victim, **TX})
    assert auction.functions.commits(aid_a, victim).call()[2] == price

    # Attacker replays the same preimage on B: MUST revert.
    with pytest.raises(TransactionFailed):
        auction.functions.reveal(aid_b, price, salt, agent_id_hash).transact(
            {"from": attacker, **TX})
    entry = auction.functions.commits(aid_b, attacker).call()
    assert entry[1] is False  # not revealed
    assert entry[2] == 0      # no price recorded


def test_honest_commit_reveal_same_auction_succeeds(env):
    # The binding must not break the honest flow: commit and reveal on the
    # same auction id with the correct chain id still succeed.
    tester, w3, auction, poster, bidder = env[:5]
    chain_id = w3.eth.chain_id
    aid = bytes.fromhex("0c" * 32)
    auction.functions.openAuction(aid, 60, 600).transact({"from": poster, **TX})

    price = 2_500_000
    salt = w3.keccak(text="honest-salt")
    agent_id_hash = w3.keccak(text="honest-agent")
    h = _commitment(w3, aid, chain_id, price, salt, agent_id_hash)

    auction.functions.commit(aid, h).transact({"from": bidder, **TX})
    _travel_reveal(tester, auction, aid)
    auction.functions.reveal(aid, price, salt, agent_id_hash).transact(
        {"from": bidder, **TX})
    entry = auction.functions.commits(aid, bidder).call()
    assert entry[1] is True and entry[2] == price


def test_wrong_chain_id_preimage_reverts(env):
    # A commitment hashed with a different chain id is not revealable on
    # this chain — cross-chain replay blocked.
    tester, w3, auction, poster, bidder = env[:5]
    chain_id = w3.eth.chain_id
    aid = bytes.fromhex("0d" * 32)
    auction.functions.openAuction(aid, 60, 600).transact({"from": poster, **TX})

    price = 3_000_000
    salt = w3.keccak(text="chain-salt")
    agent_id_hash = w3.keccak(text="chain-agent")
    h = _commitment(w3, aid, chain_id + 1, price, salt, agent_id_hash)

    auction.functions.commit(aid, h).transact({"from": bidder, **TX})
    _travel_reveal(tester, auction, aid)
    with pytest.raises(TransactionFailed):
        auction.functions.reveal(aid, price, salt, agent_id_hash).transact(
            {"from": bidder, **TX})
    assert auction.functions.commits(aid, bidder).call()[1] is False


def test_commitment_binds_auction_distinct_hashes(env):
    # Sanity: identical (price, salt, agent) on different auctions produce
    # different commitments, so there is nothing portable to copy.
    _, w3, _, _, _, _ = env
    chain_id = w3.eth.chain_id
    salt = w3.keccak(text="same-salt")
    agent_id_hash = w3.keccak(text="same-agent")
    h_a = _commitment(w3, bytes.fromhex("0a" * 32), chain_id, 100, salt, agent_id_hash)
    h_b = _commitment(w3, bytes.fromhex("0b" * 32), chain_id, 100, salt, agent_id_hash)
    assert h_a != h_b
