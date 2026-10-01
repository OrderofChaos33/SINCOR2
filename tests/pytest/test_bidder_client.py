"""Tests for the bidder wallet flow.

- Unit: commitment scheme byte-exactness, price-domain bounds, salt
  validation, agent-id hashing, auction-id derivation parity with the
  platform relayer.
- End-to-end (eth-tester): compile the real CommitRevealAuction.sol and
  drive a full commit -> reveal -> vickreyResult lifecycle through the
  reference BidderClient from two independent wallets.

Run with the sincor2 venv: PYTHONPATH=src:. <venv>/python -m pytest
tests/pytest/test_bidder_client.py -q
"""

import os

import pytest
import solcx
from eth_hash.auto import keccak
from eth_tester import EthereumTester, PyEVMBackend
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from sincor2.onchain.auction_relayer import auction_id_for as relayer_id
from sincor2.onchain.bidder_client import (
    UINT96_MAX,
    BidderClient,
    agent_id_hash,
    auction_id_for,
    commitment,
    random_salt,
)

CONTRACTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "contracts"))
SOLC_VERSION = "0.8.24"
TX = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}

SALT_A = bytes.fromhex("aa" * 32)
SALT_B = bytes.fromhex("bb" * 32)


# -- unit ------------------------------------------------------------------

def test_agent_id_hash_is_keccak_of_utf8():
    assert agent_id_hash("agent-7") == keccak(b"agent-7")


def test_commitment_matches_contract_scheme():
    # keccak256(abi.encodePacked(auctionId, block.chainid, bytes32(price),
    #                           salt, agentIdHash))
    price = 1_250_000
    aid = bytes.fromhex("0a" * 32)
    chain_id = 84532
    expected = keccak(
        aid + chain_id.to_bytes(32, "big") + price.to_bytes(32, "big")
        + SALT_A + keccak(b"agent-7"))
    assert commitment(price, SALT_A, "agent-7", aid, chain_id) == expected


def test_commitment_binds_auction_and_chain():
    # Same price/salt/agent, different auction or chain -> different hash.
    aid_a, aid_b = bytes.fromhex("0a" * 32), bytes.fromhex("0b" * 32)
    h_a = commitment(100, SALT_A, "a", aid_a, 84532)
    assert commitment(100, SALT_A, "a", aid_b, 84532) != h_a
    assert commitment(100, SALT_A, "a", aid_a, 1) != h_a


def test_commitment_domain_bounds():
    aid = bytes.fromhex("0a" * 32)
    commitment(UINT96_MAX, SALT_A, "a", aid, 84532)  # max is legal
    commitment(0, SALT_A, "a", aid, 84532)          # zero price is legal
    commitment(100, SALT_A, "a", aid, 0)             # chain 0 is legal
    with pytest.raises(ValueError):
        commitment(UINT96_MAX + 1, SALT_A, "a", aid, 84532)
    with pytest.raises(ValueError):
        commitment(-1, SALT_A, "a", aid, 84532)
    with pytest.raises(ValueError):
        commitment(100, b"short", "a", aid, 84532)
    with pytest.raises(ValueError):
        commitment(100, b"\x00" * 33, "a", aid, 84532)
    with pytest.raises(ValueError):
        commitment(100, SALT_A, "a", b"\x00" * 31, 84532)
    with pytest.raises(ValueError):
        commitment(100, SALT_A, "a", aid, -1)


def test_random_salt_is_32_bytes_and_unique():
    s1, s2 = random_salt(), random_salt()
    assert len(s1) == len(s2) == 32
    assert s1 != s2


def test_auction_id_parity_with_relayer():
    assert auction_id_for("task-123") == relayer_id("task-123")
    assert len(auction_id_for("task-123")) == 32


# -- end-to-end --------------------------------------------------------------

def _deploy_auction():
    solcx.set_solc_version(SOLC_VERSION)
    srcs = {}
    for name in ("CommitRevealAuction.sol", "IExecutionEscrowManager.sol",
                 os.path.join("security", "ScopedPausable.sol")):
        srcs[name] = {"content": open(
            os.path.join(CONTRACTS_DIR, name)).read()}
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


ABI, BYTECODE = _deploy_auction()


@pytest.fixture()
def chain():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    deployer = tester.get_accounts()[0]
    guardian = tester.get_accounts()[1]
    contract = w3.eth.contract(abi=ABI, bytecode=BYTECODE)
    txh = contract.constructor(guardian).transact({"from": deployer, **TX})
    address = w3.eth.get_transaction_receipt(txh).contractAddress
    return tester, w3, w3.eth.contract(address=address, abi=ABI)


def _funded_bidder(tester, w3, address, amount_eth=1.0):
    acct = w3.eth.account.create()
    tester.add_account(acct.key.hex())
    w3.eth.send_transaction({"from": tester.get_accounts()[0],
                             "to": acct.address,
                             "value": w3.to_wei(amount_eth, "ether"), **TX})
    return BidderClient(w3, address, acct.key.hex()), acct.address


def test_full_bidder_lifecycle(chain):
    tester, w3, auction = chain
    aid = auction_id_for("task-e2e")
    auction.functions.openAuction(aid, 60, 60).transact(
        {"from": tester.get_accounts()[0], **TX})

    alice, alice_addr = _funded_bidder(tester, w3, auction.address)
    bob, bob_addr = _funded_bidder(tester, w3, auction.address)
    carol, _ = _funded_bidder(tester, w3, auction.address)

    alice_price = w3.to_wei(1.0, "ether")
    bob_price = w3.to_wei(2.0, "ether")
    alice_salt, bob_salt = random_salt(), random_salt()
    carol_salt = random_salt()

    r1 = alice.commit(aid, alice_price, alice_salt, "alice-agent")
    r2 = bob.commit(aid, bob_price, bob_salt, "bob-agent")
    r3 = carol.commit(aid, w3.to_wei(3.0, "ether"), carol_salt,
                      "carol-agent")
    assert r1["status"] == 1 and r2["status"] == 1 and r3["status"] == 1
    assert r1["commitment"] != r2["commitment"]  # sealed: no leakage

    state = alice.auction_state(aid)
    assert state["opened"] and not state["finalized"]
    tester.time_travel(state["commit_deadline"] + 1)
    tester.mine_block()

    rr1 = alice.reveal(aid, alice_price, alice_salt, "alice-agent")
    rr2 = bob.reveal(aid, bob_price, bob_salt, "bob-agent")
    assert rr1["status"] == 1 and rr2["status"] == 1

    # Wrong salt must not reveal (would revert BadReveal).
    with pytest.raises(Exception):
        carol.reveal(aid, w3.to_wei(3.0, "ether"), random_salt(),
                     "carol-agent")

    winner, price = alice.vickrey_result(aid)
    assert winner == alice_addr  # lowest bid wins...
    assert price == bob_price    # ...at the second-lowest price (Vickrey)


def test_reveal_before_commit_deadline_reverts(chain):
    tester, w3, auction = chain
    aid = auction_id_for("task-early")
    auction.functions.openAuction(aid, 600, 600).transact(
        {"from": tester.get_accounts()[0], **TX})
    alice, _ = _funded_bidder(tester, w3, auction.address)
    salt = random_salt()
    assert alice.commit(aid, 100, salt, "alice-agent")["status"] == 1
    with pytest.raises(Exception):  # RevealTooEarly
        alice.reveal(aid, 100, salt, "alice-agent")
