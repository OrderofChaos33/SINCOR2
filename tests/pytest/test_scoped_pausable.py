"""ScopedPausable: guardian-gated emergency pause with immutable sunset.

Covers:
  - Guardian wiring (constructor-injected, immutable) and sunset arithmetic
    (SUNSET_BLOCK = deploy block + 7_776_000 = 180 days at 2s Base blocks).
  - Non-guardian setPause reverts.
  - Pause freezes ONLY state/capital injection: openAuction, commit,
    depositStake.
  - Pause NEVER blocks settlement: reveal, selectWinnerAndFund, timeout.
  - Unpause restores injection; events emitted.

Self-contained: compiles contracts/CommitRevealAuction.sol (+ security/
ScopedPausable.sol) and contracts/ExecutionEscrowManager.sol with solcx
and drives them on eth-tester (py-evm).
"""
from __future__ import annotations

import itertools
import os

import pytest
import solcx
from eth_tester import EthereumTester
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

SOLC_VERSION = "0.8.24"
CONTRACTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
MIN_STAKE_BPS = 5000
CHALLENGER_BOND = 10**16  # 0.01 ETH
SUNSET_DURATION = 7_776_000  # 180 days at 2s/block

TX = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    srcs = {}
    for name in ("CommitRevealAuction.sol", "ExecutionEscrowManager.sol",
                 "IExecutionEscrowManager.sol",
                 os.path.join("security", "ScopedPausable.sol")):
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


COMPILED = _compile()
_aid_seq = itertools.count()


def new_aid(w3, tag):
    return w3.keccak(text=f"pause-{tag}-{next(_aid_seq)}")


class Env:
    """Fresh chain + wired auction core and escrow manager per test."""

    def __init__(self):
        self.tester = EthereumTester()
        self.w3 = Web3(EthereumTesterProvider(self.tester))
        accts = self.tester.get_accounts()
        self.deployer, self.poster, self.adjudicator = accts[0], accts[1], accts[2]
        self.bidder = accts[3]
        self.anyone = accts[4]
        self.guardian = accts[5]

        abi_a, bin_a = COMPILED["auction"]
        txh = self.w3.eth.contract(abi=abi_a, bytecode=bin_a).constructor(
            self.guardian).transact({"from": self.deployer, **TX})
        r = self.w3.eth.get_transaction_receipt(txh)
        self.auction = self.w3.eth.contract(address=r.contractAddress, abi=abi_a)
        self.auction_deploy_block = r.blockNumber

        abi_e, bin_e = COMPILED["escrow"]
        txh = self.w3.eth.contract(abi=abi_e, bytecode=bin_e).constructor(
            self.auction.address, self.adjudicator, MIN_STAKE_BPS,
            CHALLENGER_BOND, self.guardian,
        ).transact({"from": self.deployer, **TX})
        r = self.w3.eth.get_transaction_receipt(txh)
        self.escrow = self.w3.eth.contract(address=r.contractAddress, abi=abi_e)
        self.escrow_deploy_block = r.blockNumber

        self.auction.functions.setEscrowManager(self.escrow.address).transact(
            {"from": self.deployer, **TX})
        self.secrets = {}

    # -- helpers ---------------------------------------------------------
    def travel_past(self, timestamp, buffer=5):
        self.tester.time_travel(timestamp + buffer)
        self.tester.mine_block()

    def open(self, commit_w=60, reveal_w=60, sender=None):
        aid = new_aid(self.w3, "auction")
        self.auction.functions.openAuction(aid, commit_w, reveal_w).transact(
            {"from": sender or self.poster, **TX})
        return aid

    def commit(self, aid, price, bidder=None):
        bidder = bidder or self.bidder
        salt = self.w3.keccak(text=f"salt-{aid.hex()}-{bidder}")
        agent_id = self.w3.keccak(text=f"agent-{bidder}")
        commitment = self.w3.solidity_keccak(
            ["uint256", "bytes32", "bytes32"], [price, salt, agent_id])
        self.auction.functions.commit(aid, commitment).transact(
            {"from": bidder, **TX})
        self.secrets[(aid, bidder)] = (price, salt, agent_id)

    def reveal(self, aid, bidder=None):
        bidder = bidder or self.bidder
        price, salt, agent_id = self.secrets[(aid, bidder)]
        self.auction.functions.reveal(aid, price, salt, agent_id).transact(
            {"from": bidder, **TX})

    def pause(self, state=True, sender=None):
        return self.w3.eth.get_transaction_receipt(
            self.auction.functions.setPause(state).transact(
                {"from": sender or self.guardian, **TX}))


@pytest.fixture()
def env():
    return Env()


# ---------------------------------------------------------------------------
# Wiring and sunset
# ---------------------------------------------------------------------------

def test_guardian_and_sunset_wiring(env):
    for contract, deploy_block in (
        (env.auction, env.auction_deploy_block),
        (env.escrow, env.escrow_deploy_block),
    ):
        assert contract.functions.guardianMultisig().call() == env.guardian
        assert contract.functions.SUNSET_DURATION_BLOCKS().call() == SUNSET_DURATION
        assert contract.functions.SUNSET_BLOCK().call() == deploy_block + SUNSET_DURATION
        assert contract.functions.paused().call() is False


def test_non_guardian_cannot_pause(env):
    with pytest.raises(TransactionFailed):
        env.auction.functions.setPause(True).transact(
            {"from": env.anyone, **TX})
    with pytest.raises(TransactionFailed):
        env.escrow.functions.setPause(True).transact(
            {"from": env.deployer, **TX})
    assert env.auction.functions.paused().call() is False


def test_pause_emits_event(env):
    receipt = env.pause(True)
    evts = env.auction.events.ProtocolPaused().process_receipt(receipt)
    assert len(evts) == 1
    assert evts[0]["args"]["caller"] == env.guardian


# ---------------------------------------------------------------------------
# Scope: injection frozen, settlement live
# ---------------------------------------------------------------------------

def test_pause_freezes_auction_injection(env):
    aid = env.open()
    env.pause(True)

    with pytest.raises(TransactionFailed):
        env.auction.functions.openAuction(new_aid(env.w3, "x"), 60, 60).transact(
            {"from": env.poster, **TX})
    with pytest.raises(TransactionFailed):
        env.auction.functions.commit(
            aid, env.w3.keccak(text="commit")).transact(
            {"from": env.bidder, **TX})


def test_pause_does_not_block_settlement(env):
    price = env.w3.to_wei(1, "ether")
    aid = env.open()
    env.commit(aid, price)
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[3])  # commitDeadline

    env.pause(True)

    # Reveal, selection/funding and timeout all stay live while paused.
    env.reveal(aid)
    env.travel_past(env.auction.functions.auctions(aid).call()[4])  # revealDeadline
    _, vickrey_price = env.auction.functions.vickreyResult(aid).call()
    env.auction.functions.selectWinnerAndFund(aid, 0).transact(
        {"from": env.poster, "value": vickrey_price, **TX})
    assert env.auction.functions.auctions(aid).call()[1] is True  # finalized


def test_pause_does_not_block_timeout_escape_hatch(env):
    aid = env.open()
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[4])  # revealDeadline
    env.pause(True)
    env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})
    assert env.auction.functions.auctions(aid).call()[1] is True


def test_pause_freezes_stake_deposit_not_settlement(env):
    price = env.w3.to_wei(1, "ether")
    aid = env.open()
    env.commit(aid, price)
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[3])
    env.reveal(aid)
    env.travel_past(env.auction.functions.auctions(aid).call()[4])
    _, vickrey_price = env.auction.functions.vickreyResult(aid).call()
    env.auction.functions.selectWinnerAndFund(aid, 0).transact(
        {"from": env.poster, "value": vickrey_price, **TX})
    # Escrow is now AwaitingStake.
    stake = vickrey_price * MIN_STAKE_BPS // 10_000

    env.escrow.functions.setPause(True).transact({"from": env.guardian, **TX})
    with pytest.raises(TransactionFailed):
        env.escrow.functions.depositStake(aid).transact(
            {"from": env.bidder, "value": stake, **TX})

    env.escrow.functions.setPause(False).transact({"from": env.guardian, **TX})
    env.escrow.functions.depositStake(aid).transact(
        {"from": env.bidder, "value": stake, **TX})


def test_unpause_restores_injection(env):
    env.pause(True)
    env.pause(False)
    assert env.auction.functions.paused().call() is False
    aid = env.open()  # works again
    assert env.auction.functions.auctions(aid).call()[0] is True  # opened
