"""eth-tester integration tests for ExecutionEscrowManager.

Self-contained: compiles contracts/ExecutionEscrowManager.sol (plus its
IExecutionEscrowManager.sol interface) with solc 0.8.24, optimizer 200 runs,
viaIR, then drives the contracts with web3 + eth-tester (PyEVM backend).

Deliberately does NOT import sincor2 and does not rely on the repo-root
conftest. Run with:

    /tmp/evmtest/bin/python -m pytest --confcutdir=tests/pytest \\
        tests/pytest/test_execution_escrow.py -q
"""

import itertools
import os

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

CONTRACTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
)

SOLC_VERSION = "0.8.24"
MIN_STAKE_BPS = 2000  # 20% of bidAmount

# ExecutionState enum indices (must match IExecutionEscrowManager.sol)
UNINITIALIZED, AWAITING_STAKE, EXECUTION_PHASE, DISPUTE_WINDOW, FINALIZED, SLASHED, TIMED_OUT = range(7)

RECEIVERS_SRC = """
// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract RevertingReceiver {
    receive() external payable { revert("nope"); }
}

contract OkReceiver {
    receive() external payable {}
}

contract GriefWorker {
    // A worker that can stake/submit but reverts on ANY incoming ETH.
    receive() external payable { revert("grief"); }

    function stake(address mgr, bytes32 aid) external payable {
        (bool ok, ) = mgr.call{value: msg.value}(
            abi.encodeWithSignature("depositStake(bytes32)", aid)
        );
        require(ok, "stake fwd failed");
    }

    function submit(address mgr, bytes32 aid, bytes32 h) external {
        (bool ok, ) = mgr.call(
            abi.encodeWithSignature("submitResult(bytes32,bytes32)", aid, h)
        );
        require(ok, "submit fwd failed");
    }
}
"""


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    with open(os.path.join(CONTRACTS_DIR, "ExecutionEscrowManager.sol")) as f:
        esc_src = f.read()
    with open(os.path.join(CONTRACTS_DIR, "IExecutionEscrowManager.sol")) as f:
        iface_src = f.read()
    std = {
        "language": "Solidity",
        "sources": {
            "ExecutionEscrowManager.sol": {"content": esc_src},
            "IExecutionEscrowManager.sol": {"content": iface_src},
            "TestReceivers.sol": {"content": RECEIVERS_SRC},
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
        "escrow": (
            contracts["ExecutionEscrowManager.sol"]["ExecutionEscrowManager"]["abi"],
            contracts["ExecutionEscrowManager.sol"]["ExecutionEscrowManager"]["evm"]["bytecode"]["object"],
        ),
        "reverting": (
            contracts["TestReceivers.sol"]["RevertingReceiver"]["abi"],
            contracts["TestReceivers.sol"]["RevertingReceiver"]["evm"]["bytecode"]["object"],
        ),
        "ok": (
            contracts["TestReceivers.sol"]["OkReceiver"]["abi"],
            contracts["TestReceivers.sol"]["OkReceiver"]["evm"]["bytecode"]["object"],
        ),
        "grief": (
            contracts["TestReceivers.sol"]["GriefWorker"]["abi"],
            contracts["TestReceivers.sol"]["GriefWorker"]["evm"]["bytecode"]["object"],
        ),
    }


COMPILED = _compile()


class Env:
    """Fresh chain + deployed manager + named accounts per test."""

    def __init__(self):
        self.tester = EthereumTester(PyEVMBackend())  # default chain: London+ base fee; txs use EIP-1559 w/ zero priority fee
        self.w3 = Web3(EthereumTesterProvider(self.tester))
        accts = self.tester.get_accounts()
        self.deployer, self.core, self.adjudicator = accts[0], accts[1], accts[2]
        self.poster, self.agent, self.challenger, self.anyone = accts[3], accts[4], accts[5], accts[6]
        self.challenger_bond = self.w3.to_wei(0.1, "ether")
        abi, bytecode = COMPILED["escrow"]
        factory = self.w3.eth.contract(abi=abi, bytecode=bytecode)
        txh = factory.constructor(
            self.core, self.adjudicator, MIN_STAKE_BPS, self.challenger_bond
        ).transact({"from": self.deployer, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
        addr = self.w3.eth.get_transaction_receipt(txh).contractAddress
        self.mgr = self.w3.eth.contract(address=addr, abi=abi)
        self.receivers = {}
        for name in ("reverting", "ok", "grief"):
            abi_r, bin_r = COMPILED[name]
            f = self.w3.eth.contract(abi=abi_r, bytecode=bin_r)
            txh = f.constructor().transact({"from": self.deployer, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
            self.receivers[name] = self.w3.eth.get_transaction_receipt(txh).contractAddress

    # -- helpers ---------------------------------------------------------
    def travel_past(self, timestamp, buffer=300):
        """Move chain time strictly past `timestamp`."""
        self.tester.time_travel(timestamp + buffer)
        self.tester.mine_block()

    def init_escrow(self, aid, poster, agent, bid, credit, value,
                    exec_dur=600, dispute_dur=600, adj_dur=600, stake_window=600,
                    sender=None):
        txh = self.mgr.functions.initializeEscrow(
            aid, poster, agent, bid, credit,
            exec_dur, dispute_dur, adj_dur, stake_window,
        ).transact({"from": sender or self.core, "value": value, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
        return self.w3.eth.get_transaction_receipt(txh)

    def get_escrow(self, aid):
        return self.mgr.functions.getEscrow(aid).call()

    def fund(self, aid):
        return self.mgr.functions.getPosterReAuctionBalance(aid).call()


_aid_seq = itertools.count()


def new_aid(w3, tag):
    return w3.keccak(text=f"escrow-{tag}-{next(_aid_seq)}")


def events_of(mgr, name, receipt):
    return getattr(mgr.events, name)().process_receipt(receipt)


@pytest.fixture()
def env():
    return Env()


def credit_poster_fund(env, poster, amount):
    """Build `poster`'s re-auction ledger to exactly `amount` via the
    rejected-dispute path: an external challenger's bond is slashed into the
    poster's fund. Uses a throwaway escrow."""
    w3, mgr = env.w3, env.mgr
    aid = new_aid(w3, "fund")
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    env.init_escrow(aid, poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\x11" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.openQualityDispute(aid, b"\x22" * 32).transact(
        {"from": env.challenger, "value": amount, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}
    )
    mgr.functions.resolveQualityDispute(aid, False).transact({"from": env.adjudicator, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    assert mgr.functions.getPosterReAuctionBalance(poster).call() == amount
    return aid


# ---------------------------------------------------------------------------
# 1. initializeEscrow: two-sided funding, exactness, access control
# ---------------------------------------------------------------------------

def test_initialize_exact_two_sided_funding(env):
    w3, mgr = env.w3, env.mgr
    credit_poster_fund(env, env.poster, w3.to_wei(1, "ether"))
    aid = new_aid(w3, "init")
    bid = w3.to_wei(2, "ether")
    fresh = w3.to_wei(1.5, "ether")
    credit = w3.to_wei(0.5, "ether")
    receipt = env.init_escrow(aid, env.poster, env.agent, bid, credit, fresh)

    esc = env.get_escrow(aid)
    assert esc[2] == bid            # bidAmount
    assert esc[3] == fresh          # ethDeposited
    assert esc[4] == credit         # creditBacking
    assert esc[5] == 0              # agentStake
    assert esc[13] == AWAITING_STAKE
    assert esc[14] is False         # disputeActive
    # fund ledger drawn down by exactly the credit applied
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == w3.to_wei(0.5, "ether")
    # full-ETH funding path also works
    aid2 = new_aid(w3, "init2")
    env.init_escrow(aid2, env.poster, env.agent, bid, 0, bid)
    esc2 = env.get_escrow(aid2)
    assert esc2[3] == bid and esc2[4] == 0

    evts = events_of(mgr, "EscrowInitialized", receipt)
    assert len(evts) == 1
    e = evts[0]["args"]
    assert e["auctionId"] == aid and e["poster"] == env.poster
    assert e["selectedAgent"] == env.agent and e["bidAmount"] == bid
    assert e["ethDeposited"] == fresh and e["creditApplied"] == credit
    drawn = events_of(mgr, "ReAuctionFundDrawn", receipt)
    assert len(drawn) == 1 and drawn[0]["args"]["creditUsed"] == credit


def test_initialize_reverts(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")

    # zero bid
    with pytest.raises(TransactionFailed):
        env.init_escrow(new_aid(w3, "z"), env.poster, env.agent, 0, 0, 0)
    # non-auctionCore caller
    with pytest.raises(TransactionFailed):
        env.init_escrow(new_aid(w3, "nc"), env.poster, env.agent, bid, 0, bid, sender=env.poster)
    # overfunding
    with pytest.raises(TransactionFailed):
        env.init_escrow(new_aid(w3, "over"), env.poster, env.agent, bid, 0, bid + 1)
    # underfunding
    with pytest.raises(TransactionFailed):
        env.init_escrow(new_aid(w3, "under"), env.poster, env.agent, bid, 0, bid - 1)
    # credit draw exceeds poster fund (fund is 0 here)
    with pytest.raises(TransactionFailed):
        env.init_escrow(new_aid(w3, "cred"), env.poster, env.agent, bid, w3.to_wei(1, "ether"),
                        w3.to_wei(1, "ether"))
    # double init
    aid = new_aid(w3, "dbl")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    with pytest.raises(TransactionFailed):
        env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)


# ---------------------------------------------------------------------------
# 2. Credit non-extractability
# ---------------------------------------------------------------------------

def test_no_withdraw_function_in_abi(env):
    names = {e["name"] for e in env.mgr.abi if e.get("type") == "function"}
    for bad in ("withdraw", "claim", "drawReAuctionCredit", "rescue", "sweep", "drain"):
        assert not any(bad in n.lower() for n in names), f"extractive fn present: {sorted(names)}"
    assert "posterReAuctionBalances" in names  # ledger is view-only


def test_constructor_rejects_out_of_range_min_stake_bps(env):
    w3 = env.w3
    abi, bytecode = COMPILED["escrow"]
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    for bad_bps in (0, 10001, 10**18):
        with pytest.raises(TransactionFailed):
            factory.constructor(
                env.core, env.adjudicator, bad_bps, env.challenger_bond
            ).transact({"from": env.deployer, "maxFeePerGas": 10_000_000_000,
                        "maxPriorityFeePerGas": 0})
    # boundary values deploy fine
    for ok_bps in (1, 10000):
        txh = factory.constructor(
            env.core, env.adjudicator, ok_bps, env.challenger_bond
        ).transact({"from": env.deployer, "maxFeePerGas": 10_000_000_000,
                    "maxPriorityFeePerGas": 0})
        addr = w3.eth.get_transaction_receipt(txh).contractAddress
        assert w3.eth.contract(address=addr, abi=abi).functions.minStakeBps().call() == ok_bps


def test_set_challenger_bond_emits_event_and_is_adjudicator_only(env):
    w3, mgr = env.w3, env.mgr
    new_bond = w3.to_wei(0.5, "ether")
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.setChallengerBond(new_bond).transact(
            {"from": env.adjudicator, "maxFeePerGas": 10_000_000_000,
             "maxPriorityFeePerGas": 0}))
    assert mgr.functions.challengerBond().call() == new_bond
    evts = events_of(mgr, "ChallengerBondUpdated", receipt)
    assert len(evts) == 1 and evts[0]["args"]["newBond"] == new_bond
    with pytest.raises(TransactionFailed):
        mgr.functions.setChallengerBond(new_bond).transact(
            {"from": env.anyone, "maxFeePerGas": 10_000_000_000,
             "maxPriorityFeePerGas": 0})
    # uint96-recordable bound still enforced
    with pytest.raises(TransactionFailed):
        mgr.functions.setChallengerBond(2**96).transact(
            {"from": env.adjudicator, "maxFeePerGas": 10_000_000_000,
             "maxPriorityFeePerGas": 0})


def _init_credited(env, tag, bid, credit, poster=None):
    """Init an escrow funded fresh+credit; returns (aid, fresh)."""
    w3 = env.w3
    poster = poster or env.poster
    aid = new_aid(w3, tag)
    fresh = bid - credit
    env.init_escrow(aid, poster, env.agent, bid, credit, fresh)
    return aid, fresh


def test_credit_survives_unstaked_timeout_as_ledger(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    credit = w3.to_wei(1, "ether")
    credit_poster_fund(env, env.poster, credit)
    aid, fresh = _init_credited(env, "timeout-credit", bid, credit)

    bal_before = w3.eth.get_balance(env.poster)
    esc = env.get_escrow(aid)
    env.travel_past(esc[6])  # stakeDepositDeadline
    mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    esc2 = env.get_escrow(aid)
    assert esc2[13] == TIMED_OUT
    # poster got back exactly the fresh ETH, nothing more
    assert w3.eth.get_balance(env.poster) == bal_before + fresh
    # the credit went back to the ledger, not to the poster's wallet
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == credit


def test_credit_survives_ghosting_timeout_as_ledger(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    credit = w3.to_wei(1, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    credit_poster_fund(env, env.poster, credit)
    aid, fresh = _init_credited(env, "ghost-credit", bid, credit)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    bal_before = w3.eth.get_balance(env.poster)
    env.travel_past(env.get_escrow(aid)[8])  # executionDeadline
    mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    assert env.get_escrow(aid)[13] == SLASHED
    assert w3.eth.get_balance(env.poster) == bal_before + fresh
    # fund = restored credit + 100% slashed stake; poster wallet saw only fresh ETH
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == credit + stake


def test_credit_survives_upheld_dispute_as_ledger_and_is_reusable(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    credit = w3.to_wei(1, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    credit_poster_fund(env, env.poster, credit)
    aid, fresh = _init_credited(env, "upheld-credit", bid, credit)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\x33" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.openQualityDispute(aid, b"\x44" * 32).transact(
        {"from": env.challenger, "value": env.challenger_bond, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}
    )

    bal_before = w3.eth.get_balance(env.poster)
    mgr.functions.resolveQualityDispute(aid, True).transact({"from": env.adjudicator, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    slash = stake // 2
    # fund = 0 (drawn) + 50% slash + restored credit
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == slash + credit
    # poster wallet received exactly the fresh ETH portion
    assert w3.eth.get_balance(env.poster) == bal_before + fresh

    # credits are re-appliable to a future escrow...
    aid2, fresh2 = _init_credited(env, "reuse", bid, credit)
    assert env.get_escrow(aid2)[4] == credit
    # ...but there is still no way to pull them out as ETH (no withdraw fn;
    # every exit path above returned them to the ledger only).


# ---------------------------------------------------------------------------
# 3. Stake deposit
# ---------------------------------------------------------------------------

def test_deposit_stake_rules(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    required = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "stake")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)

    # below required -> revert
    with pytest.raises(TransactionFailed):
        mgr.functions.depositStake(aid).transact(
            {"from": env.agent, "value": required - 1, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    # wrong sender -> revert
    with pytest.raises(TransactionFailed):
        mgr.functions.depositStake(aid).transact(
            {"from": env.anyone, "value": required, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    # exact deposit starts the execution clock
    txh = mgr.functions.depositStake(aid).transact(
        {"from": env.agent, "value": required, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    receipt = w3.eth.get_transaction_receipt(txh)
    blk_ts = w3.eth.get_block(receipt.blockNumber).timestamp
    esc = env.get_escrow(aid)
    assert esc[5] == required
    assert esc[8] == blk_ts + 600  # executionDeadline = deposit time + executionDuration
    assert esc[13] == EXECUTION_PHASE
    evts = events_of(mgr, "StakeDeposited", receipt)
    assert len(evts) == 1
    assert evts[0]["args"]["stakeAmount"] == required
    assert evts[0]["args"]["executionDeadline"] == blk_ts + 600
    # double deposit -> revert
    with pytest.raises(TransactionFailed):
        mgr.functions.depositStake(aid).transact(
            {"from": env.agent, "value": required, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})


def test_deposit_stake_after_deadline_reverts(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    required = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "stake-late")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid, stake_window=600)
    env.travel_past(env.get_escrow(aid)[6])
    with pytest.raises(TransactionFailed):
        mgr.functions.depositStake(aid).transact(
            {"from": env.agent, "value": required, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})


def test_deposit_stake_overpayment_is_accepted_not_reverted(env):
    # OBSERVED BEHAVIOR (deviation from an "exact amount only" reading):
    # depositStake accepts msg.value ABOVE the required minimum and records
    # the FULL overpaid amount as agentStake. Documented here; the funds are
    # not lost (returned on reject / halved on upheld dispute).
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    required = bid * MIN_STAKE_BPS // 10000
    over = required + w3.to_wei(0.25, "ether")
    aid = new_aid(w3, "stake-over")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": over, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    assert env.get_escrow(aid)[5] == over


# ---------------------------------------------------------------------------
# 4. Ghosting: 100% slash
# ---------------------------------------------------------------------------

def test_ghosting_slashes_full_stake_to_poster_fund(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "ghost")
    receipt = env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    # timeout before the execution deadline must not fire
    with pytest.raises(TransactionFailed):
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    agent_before = w3.eth.get_balance(env.agent)
    poster_before = w3.eth.get_balance(env.poster)
    env.travel_past(env.get_escrow(aid)[8])
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    )

    esc = env.get_escrow(aid)
    assert esc[13] == SLASHED
    assert esc[5] == 0 and esc[3] == 0  # stake + deposits zeroed
    # 100% of stake -> poster fund; agent lost everything
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == stake
    assert w3.eth.get_balance(env.agent) == agent_before
    # poster refunded their full fresh ETH
    assert w3.eth.get_balance(env.poster) == poster_before + bid

    slashes = events_of(mgr, "SlashExecuted", receipt)
    assert len(slashes) == 1
    s = slashes[0]["args"]
    assert s["agent"] == env.agent and s["poster"] == env.poster
    assert int(s["reason"]) == 0  # SlashReason.ExecutionGhosting
    assert s["amountSlashed"] == stake and s["amountToReAuctionFund"] == stake
    credited = events_of(mgr, "ReAuctionFundCredited", receipt)
    assert len(credited) == 1 and credited[0]["args"]["amount"] == stake


# ---------------------------------------------------------------------------
# 5. Upheld quality dispute: 50% slash
# ---------------------------------------------------------------------------

def test_upheld_dispute_splits_stake_and_restores_credit(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    credit = w3.to_wei(1, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    credit_poster_fund(env, env.poster, credit)
    aid, fresh = _init_credited(env, "upheld", bid, credit)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\x55" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    # external challenger posts the bond
    mgr.functions.openQualityDispute(aid, b"\x66" * 32).transact(
        {"from": env.challenger, "value": env.challenger_bond, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}
    )

    poster_before = w3.eth.get_balance(env.poster)
    agent_before = w3.eth.get_balance(env.agent)
    chal_before = w3.eth.get_balance(env.challenger)
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.resolveQualityDispute(aid, True).transact(
            {"from": env.adjudicator, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    )

    slash, refund = stake // 2, stake - stake // 2
    esc = env.get_escrow(aid)
    assert esc[13] == FINALIZED and esc[14] is False
    assert env.mgr.functions.getDispute(aid).call()[0] == "0x0000000000000000000000000000000000000000"
    # money math: checkpoints are taken after setup txs, and the final tx is sent
    # by an account whose balance is never asserted, so balances are exact
    assert w3.eth.get_balance(env.poster) == poster_before + fresh   # fresh ETH refunded
    assert w3.eth.get_balance(env.agent) == agent_before + refund     # unslashed half back
    assert w3.eth.get_balance(env.challenger) == chal_before + env.challenger_bond  # bond returned
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == slash + credit

    resolved = events_of(mgr, "QualityDisputeResolved", receipt)
    assert len(resolved) == 1
    assert resolved[0]["args"]["slashUpheld"] is True
    assert resolved[0]["args"]["slashedAmount"] == slash


def test_poster_dispute_is_free(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "poster-dispute")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\x77" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    # poster disputes with zero value -> opens fine
    mgr.functions.openQualityDispute(aid, b"\x88" * 32).transact(
        {"from": env.poster, "value": 0, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    d = mgr.functions.getDispute(aid).call()
    assert d[0] == env.poster and d[1] == 0
    # poster accidentally sending ETH with a dispute -> revert (no accidental lockup)
    aid2 = new_aid(w3, "poster-dispute2")
    env.init_escrow(aid2, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid2).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid2, b"\x99" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid2, b"\xaa" * 32).transact(
            {"from": env.poster, "value": 1, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})


# ---------------------------------------------------------------------------
# 6. Rejected dispute: worker paid, challenger bond slashed
# ---------------------------------------------------------------------------

def test_rejected_dispute_pays_worker_and_slashes_bond(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "reject")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\xbb" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.openQualityDispute(aid, b"\xcc" * 32).transact(
        {"from": env.challenger, "value": env.challenger_bond, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}
    )

    agent_before = w3.eth.get_balance(env.agent)
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.resolveQualityDispute(aid, False).transact(
            {"from": env.adjudicator, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    )

    esc = env.get_escrow(aid)
    assert esc[13] == FINALIZED
    # worker paid bid + full stake
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    # false challenger's bond -> poster fund
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond

    finalized = events_of(mgr, "EscrowFinalized", receipt)
    assert len(finalized) == 1
    assert finalized[0]["args"]["payoutAmount"] == bid
    assert finalized[0]["args"]["stakeReturned"] == stake


# ---------------------------------------------------------------------------
# 7. Dispute gating
# ---------------------------------------------------------------------------

def test_dispute_gating(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000

    # external challenger without bond -> revert
    aid = new_aid(w3, "gate")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\xdd" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid, b"\xee" * 32).transact(
            {"from": env.challenger, "value": 0, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid, b"\xee" * 32).transact(
            {"from": env.challenger, "value": env.challenger_bond - 1, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    # dispute after the challenge window -> revert
    env.travel_past(env.get_escrow(aid)[11] + 600)  # resultSubmittedTimestamp + disputeWindowDuration
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid, b"\xee" * 32).transact(
            {"from": env.challenger, "value": env.challenger_bond, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    # second dispute on the same auction -> revert
    aid2 = new_aid(w3, "gate2")
    env.init_escrow(aid2, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid2).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid2, b"\xff" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.openQualityDispute(aid2, b"\x01" * 32).transact(
        {"from": env.poster, "value": 0, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid2, b"\x02" * 32).transact(
            {"from": env.challenger, "value": env.challenger_bond, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    # non-adjudicator cannot resolve
    with pytest.raises(TransactionFailed):
        mgr.functions.resolveQualityDispute(aid2, True).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})


# ---------------------------------------------------------------------------
# 8. Adjudicator-dark timeout: optimistic payout + bond refund
# ---------------------------------------------------------------------------

def test_adjudicator_dark_timeout_pays_worker_and_refunds_bond(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "dark")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\x03" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    txh = mgr.functions.openQualityDispute(aid, b"\x04" * 32).transact(
        {"from": env.challenger, "value": env.challenger_bond, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    filing_ts = w3.eth.get_block(w3.eth.get_transaction_receipt(txh).blockNumber).timestamp

    # timeout BEFORE the resolution deadline must not fire (dispute is open)
    with pytest.raises(TransactionFailed):
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    agent_before = w3.eth.get_balance(env.agent)
    chal_before = w3.eth.get_balance(env.challenger)
    env.travel_past(filing_ts + 600)  # adjudicationWindowDuration
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    )

    esc = env.get_escrow(aid)
    assert esc[13] == FINALIZED and esc[14] is False
    # optimistic worker payout: bid + full stake
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    # challenger bond returned (silence was not their fault); nothing slashed
    assert w3.eth.get_balance(env.challenger) == chal_before + env.challenger_bond
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == 0


# ---------------------------------------------------------------------------
# 9. Unstaked timeout: refund, no slash, TimedOut
# ---------------------------------------------------------------------------

def test_unstaked_timeout_refunds_with_no_slash(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    aid = new_aid(w3, "nostake")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid, stake_window=600)

    # premature timeout must not fire
    with pytest.raises(TransactionFailed):
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    poster_before = w3.eth.get_balance(env.poster)
    env.travel_past(env.get_escrow(aid)[6])
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    )

    esc = env.get_escrow(aid)
    assert esc[13] == TIMED_OUT  # NOT Slashed: nothing was ever at risk
    assert w3.eth.get_balance(env.poster) == poster_before + bid
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == 0
    assert len(events_of(mgr, "SlashExecuted", receipt)) == 0
    timed = events_of(mgr, "EscrowTimedOut", receipt)
    assert len(timed) == 1 and timed[0]["args"]["ethRefunded"] == bid


# ---------------------------------------------------------------------------
# 10. Reverting-recipient robustness
# ---------------------------------------------------------------------------

def test_reverting_poster_bricks_ghosting_timeout(env):
    """FINDING: payouts use push-style _safeTransfer which reverts the whole
    transaction (TransferFailed) when the recipient's receive() reverts.
    A reverting poster permanently bricks the escrow: ghosting timeout can
    never complete, the agent's stake and the poster's own ETH are locked
    with no recovery path (no rescue/pull function exists)."""
    w3, mgr = env.w3, env.mgr
    poster = env.receivers["reverting"]
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "revert-poster")
    env.init_escrow(aid, poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    env.travel_past(env.get_escrow(aid)[8])

    with pytest.raises(TransactionFailed):
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    # atomic revert: nothing moved, escrow stuck in ExecutionPhase
    esc = env.get_escrow(aid)
    assert esc[13] == EXECUTION_PHASE and esc[5] == stake and esc[3] == bid


def test_reverting_poster_bricks_upheld_dispute_resolution(env):
    """FINDING: same push-payment brittleness on the dispute path. With a
    reverting poster, the adjudicator can NEVER finalize an upheld dispute:
    every resolveQualityDispute call reverts, so the challenger's bond and
    the worker's stake are locked indefinitely."""
    w3, mgr = env.w3, env.mgr
    poster = env.receivers["reverting"]
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "revert-poster2")
    env.init_escrow(aid, poster, env.agent, bid, 0, bid)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.submitResult(aid, b"\x05" * 32).transact({"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    mgr.functions.openQualityDispute(aid, b"\x06" * 32).transact(
        {"from": env.challenger, "value": env.challenger_bond, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    with pytest.raises(TransactionFailed):
        mgr.functions.resolveQualityDispute(aid, True).transact(
            {"from": env.adjudicator, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    assert env.get_escrow(aid)[13] == DISPUTE_WINDOW


def test_griefing_worker_bricks_optimistic_timeout(env):
    """FINDING: a worker contract that reverts on receive bricks the
    optimistic (no-dispute) timeout too. The payout is all-or-nothing, so a
    griefing worker can permanently lock its own bid+stake AND deny the
    poster any further progress on the auction."""
    w3, mgr = env.w3, env.mgr
    grief = env.receivers["grief"]
    grief_worker = w3.eth.contract(address=grief, abi=COMPILED["grief"][0])
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "grief-worker")
    env.init_escrow(aid, env.poster, grief, bid, 0, bid)
    # stake/submit through the grief contract; the stake() call itself carries
    # the ETH (function call, not a bare transfer, so receive() isn't hit)
    grief_worker.functions.stake(mgr.address, aid).transact(
        {"from": env.deployer, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    grief_worker.functions.submit(mgr.address, aid, b"\x07" * 32).transact(
        {"from": env.deployer, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})

    env.travel_past(env.get_escrow(aid)[11] + 600)
    with pytest.raises(TransactionFailed):
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    assert env.get_escrow(aid)[13] == DISPUTE_WINDOW


def test_contract_wallet_poster_payout_succeeds(env):
    """Sanity: a well-behaved contract wallet (empty receive) CAN receive
    payouts, validating the .call-over-.transfer design choice for the
    non-griefing case."""
    w3, mgr = env.w3, env.mgr
    poster = env.receivers["ok"]
    bid = w3.to_wei(2, "ether")
    aid = new_aid(w3, "ok-poster")
    env.init_escrow(aid, poster, env.agent, bid, 0, bid, stake_window=600)
    env.travel_past(env.get_escrow(aid)[6])
    mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0})
    assert env.get_escrow(aid)[13] == TIMED_OUT
    assert w3.eth.get_balance(poster) == bid


# ---------------------------------------------------------------------------
# 11. Happy-path event spot-checks
# ---------------------------------------------------------------------------

def test_happy_path_events_and_funds_math(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "happy")

    r_init = env.init_escrow(aid, env.poster, env.agent, bid, 0, bid)
    e_init = events_of(mgr, "EscrowInitialized", r_init)[0]["args"]
    assert e_init["bidAmount"] == bid and e_init["ethDeposited"] == bid
    assert e_init["creditApplied"] == 0
    deadline = e_init["stakeDepositDeadline"]
    assert env.get_escrow(aid)[6] == deadline

    r_stake = w3.eth.get_transaction_receipt(
        mgr.functions.depositStake(aid).transact(
            {"from": env.agent, "value": stake, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}))
    e_stake = events_of(mgr, "StakeDeposited", r_stake)[0]["args"]
    assert e_stake["agent"] == env.agent and e_stake["stakeAmount"] == stake

    r_sub = w3.eth.get_transaction_receipt(
        mgr.functions.submitResult(aid, b"\x08" * 32).transact(
            {"from": env.agent, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}))
    e_sub = events_of(mgr, "ResultSubmitted", r_sub)[0]["args"]
    assert e_sub["resultHash"] == b"\x08" * 32

    # no dispute; optimistic timeout pays the worker
    env.travel_past(e_sub["disputeDeadline"])
    agent_before = w3.eth.get_balance(env.agent)
    r_fin = w3.eth.get_transaction_receipt(
        mgr.functions.timeout(aid).transact({"from": env.anyone, "maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}))
    e_fin = events_of(mgr, "EscrowFinalized", r_fin)[0]["args"]
    assert e_fin["agent"] == env.agent
    assert e_fin["payoutAmount"] == bid and e_fin["stakeReturned"] == stake
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake

    # contract holds no residual ETH for this auction
    assert w3.eth.get_balance(mgr.address) == 0
