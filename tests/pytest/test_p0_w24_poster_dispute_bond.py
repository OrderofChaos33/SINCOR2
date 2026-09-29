"""P0/W-24 regression tests: poster disputes must bond.

The poster used to dispute for free (zero bond), stalling the worker's payout
through the whole adjudication window at gas-only cost. Now the poster posts
the same challengerBond with the same economics as any challenger, and
evidence-free disputes can be fast-rejected after a short evidence window
without waiting out the full adjudication window.

Self-contained: compiles contracts/ExecutionEscrowManager.sol (plus its
IExecutionEscrowManager.sol interface) with solc 0.8.24, optimizer 200 runs,
viaIR, then drives the contracts with web3 + eth-tester (PyEVM backend).

Deliberately does NOT import sincor2 and does not rely on the repo-root
conftest. Run with:

    /tmp/evmtest/bin/python -m pytest --confcutdir=tests/pytest \\
        tests/pytest/test_p0_w24_poster_dispute_bond.py -q
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

GAS = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    srcs = {}
    for name in ("ExecutionEscrowManager.sol", "IExecutionEscrowManager.sol"):
        with open(os.path.join(CONTRACTS_DIR, name)) as f:
            srcs[name] = {"content": f.read()}
    with open(os.path.join(CONTRACTS_DIR, "security", "ScopedPausable.sol")) as f:
        srcs["security/ScopedPausable.sol"] = {"content": f.read()}
    std = {
        "language": "Solidity",
        "sources": srcs,
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION, allow_paths=CONTRACTS_DIR)
    c = out["contracts"]["ExecutionEscrowManager.sol"]["ExecutionEscrowManager"]
    return c["abi"], c["evm"]["bytecode"]["object"]


ABI, BYTECODE = _compile()


class Env:
    """Fresh chain + deployed manager + named accounts per test."""

    def __init__(self):
        self.tester = EthereumTester(PyEVMBackend())
        self.w3 = Web3(EthereumTesterProvider(self.tester))
        accts = self.tester.get_accounts()
        self.deployer, self.core, self.adjudicator = accts[0], accts[1], accts[2]
        self.poster, self.agent, self.challenger, self.anyone = accts[3], accts[4], accts[5], accts[6]
        self.guardian = accts[7]
        self.challenger_bond = self.w3.to_wei(0.1, "ether")
        factory = self.w3.eth.contract(abi=ABI, bytecode=BYTECODE)
        txh = factory.constructor(
            self.core, self.adjudicator, MIN_STAKE_BPS, self.challenger_bond,
            self.guardian,
        ).transact({"from": self.deployer, **GAS})
        addr = self.w3.eth.get_transaction_receipt(txh).contractAddress
        self.mgr = self.w3.eth.contract(address=addr, abi=ABI)

    def travel_past(self, timestamp, buffer=300):
        self.tester.time_travel(timestamp + buffer)
        self.tester.mine_block()

    def init_escrow(self, aid, poster, agent, bid, credit, value,
                    exec_dur=600, dispute_dur=600, adj_dur=600, stake_window=600,
                    sender=None):
        txh = self.mgr.functions.initializeEscrow(
            aid, poster, agent, bid, credit,
            exec_dur, dispute_dur, adj_dur, stake_window,
        ).transact({"from": sender or self.core, "value": value, **GAS})
        return self.w3.eth.get_transaction_receipt(txh)

    def run_to_dispute(self, aid, bid, stake):
        """Fresh escrow with stake deposited and result submitted."""
        w3, mgr = self.w3, self.mgr
        self.init_escrow(aid, self.poster, self.agent, bid, 0, bid)
        mgr.functions.depositStake(aid).transact({"from": self.agent, "value": stake, **GAS})
        mgr.functions.submitResult(aid, b"\x11" * 32).transact({"from": self.agent, **GAS})


_aid_seq = itertools.count()


def new_aid(w3, tag):
    return w3.keccak(text=f"w24-{tag}-{next(_aid_seq)}")


@pytest.fixture()
def env():
    return Env()


# ---------------------------------------------------------------------------
# 1. Zero-bond poster dispute reverts
# ---------------------------------------------------------------------------

def test_poster_zero_bond_dispute_reverts(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "zerobond")
    env.run_to_dispute(aid, bid, stake)
    # poster with zero bond -> reverts (P0/W-24)
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid, b"\x22" * 32).transact(
            {"from": env.poster, "value": 0, **GAS})
    # under-bonded poster -> reverts
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid, b"\x22" * 32).transact(
            {"from": env.poster, "value": env.challenger_bond - 1, **GAS})
    # no dispute was opened
    assert mgr.functions.getEscrow(aid).call()[14] is False


def test_poster_bonded_dispute_opens_and_bond_is_recorded(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "bonded")
    env.run_to_dispute(aid, bid, stake)
    mgr.functions.openQualityDispute(aid, b"\x33" * 32).transact(
        {"from": env.poster, "value": env.challenger_bond, **GAS})
    d = mgr.functions.getDispute(aid).call()
    assert d[0] == env.poster
    assert d[1] == env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[14] is True  # disputeActive
    # a second dispute on the same escrow still reverts
    with pytest.raises(TransactionFailed):
        mgr.functions.openQualityDispute(aid, b"\x44" * 32).transact(
            {"from": env.challenger, "value": env.challenger_bond, **GAS})


# ---------------------------------------------------------------------------
# 2. Bond economics for poster disputes (same destination as challenger bonds)
# ---------------------------------------------------------------------------

def test_poster_bond_returned_when_dispute_upheld(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "upheld")
    env.run_to_dispute(aid, bid, stake)
    poster_before = w3.eth.get_balance(env.poster)
    mgr.functions.openQualityDispute(aid, b"\x55" * 32).transact(
        {"from": env.poster, "value": env.challenger_bond, **GAS})
    poster_after_open = w3.eth.get_balance(env.poster)
    mgr.functions.resolveQualityDispute(aid, True).transact(
        {"from": env.adjudicator, **GAS})
    # bond returned to the poster (they were right); dispute cleared;
    # poster was also refunded the 2 ETH payment on uphold
    assert w3.eth.get_balance(env.poster) == poster_after_open + env.challenger_bond + bid
    assert poster_before - w3.eth.get_balance(env.poster) < bid + stake  # sanity: poster refunded payment too
    assert mgr.functions.getDispute(aid).call()[0] == "0x0000000000000000000000000000000000000000"
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_poster_bond_slashed_to_poster_fund_when_rejected(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "rejected")
    env.run_to_dispute(aid, bid, stake)
    agent_before = w3.eth.get_balance(env.agent)
    mgr.functions.openQualityDispute(aid, b"\x66" * 32).transact(
        {"from": env.poster, "value": env.challenger_bond, **GAS})
    mgr.functions.resolveQualityDispute(aid, False).transact(
        {"from": env.adjudicator, **GAS})
    # worker paid in full (bid + stake)
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    # poster's own bond is slashed to the poster's re-auction fund --
    # the SAME destination challenger bonds already go to
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_poster_bond_returned_on_dark_adjudicator_timeout(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "dark")
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid, adj_dur=600)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, **GAS})
    mgr.functions.submitResult(aid, b"\x77" * 32).transact({"from": env.agent, **GAS})
    txh = mgr.functions.openQualityDispute(aid, b"\x88" * 32).transact(
        {"from": env.poster, "value": env.challenger_bond, **GAS})
    filing_ts = w3.eth.get_block(w3.eth.get_transaction_receipt(txh).blockNumber).timestamp
    poster_before = w3.eth.get_balance(env.poster)
    env.travel_past(filing_ts + 600)  # adjudicationWindowDuration
    mgr.functions.timeout(aid).transact({"from": env.anyone, **GAS})
    # bond returned: the adjudicator's silence was not the poster's fault
    assert w3.eth.get_balance(env.poster) == poster_before + env.challenger_bond


# ---------------------------------------------------------------------------
# 3. Fast path: evidence-free disputes resolve without the full 48h wait
# ---------------------------------------------------------------------------

EVIDENCE_WINDOW = 6 * 3600  # must match DISPUTE_EVIDENCE_WINDOW


def test_fast_reject_evidence_free_dispute(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "fastreject")
    # 48h adjudication window: the fast path must not need to wait it out
    env.init_escrow(aid, env.poster, env.agent, bid, 0, bid, adj_dur=48 * 3600)
    mgr.functions.depositStake(aid).transact({"from": env.agent, "value": stake, **GAS})
    mgr.functions.submitResult(aid, b"\x99" * 32).transact({"from": env.agent, **GAS})
    # poster files a bonded but EVIDENCE-FREE dispute (digest == 0)
    txh = mgr.functions.openQualityDispute(aid, bytes(32)).transact(
        {"from": env.poster, "value": env.challenger_bond, **GAS})
    filing_ts = w3.eth.get_block(w3.eth.get_transaction_receipt(txh).blockNumber).timestamp
    # fast reject BEFORE the evidence window passes -> reverts
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    # adjudicator stays dark; anyone fast-rejects after the evidence window
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    assert w3.eth.get_block("latest").timestamp < filing_ts + 48 * 3600  # nowhere near the 48h deadline
    mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    esc = mgr.functions.getEscrow(aid).call()
    assert esc[13] == FINALIZED and esc[14] is False
    # worker paid in full without waiting 48h; poster's bond slashed to the fund
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    # timeout() after fast rejection has nothing left to do
    with pytest.raises(TransactionFailed):
        mgr.functions.timeout(aid).transact({"from": env.anyone, **GAS})


def test_fast_reject_reverts_when_evidence_present(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "withevd")
    env.run_to_dispute(aid, bid, stake)
    # dispute WITH evidence attached
    txh = mgr.functions.openQualityDispute(aid, b"\xaa" * 32).transact(
        {"from": env.poster, "value": env.challenger_bond, **GAS})
    filing_ts = w3.eth.get_block(w3.eth.get_transaction_receipt(txh).blockNumber).timestamp
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    assert mgr.functions.getEscrow(aid).call()[14] is True  # still active


def test_fast_reject_reverts_without_dispute(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "nodisp")
    env.run_to_dispute(aid, bid, stake)
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})


def test_adjudicator_can_reject_poster_dispute_immediately(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "adjfast")
    env.run_to_dispute(aid, bid, stake)
    mgr.functions.openQualityDispute(aid, bytes(32)).transact(
        {"from": env.poster, "value": env.challenger_bond, **GAS})
    agent_before = w3.eth.get_balance(env.agent)
    # adjudicator needs no evidence window: immediate rejection
    mgr.functions.resolveQualityDispute(aid, False).transact({"from": env.adjudicator, **GAS})
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


# ---------------------------------------------------------------------------
# 4. Honest challenger flow is unchanged
# ---------------------------------------------------------------------------

def test_honest_challenger_uphold_unchanged(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "chal-up")
    env.run_to_dispute(aid, bid, stake)
    mgr.functions.openQualityDispute(aid, b"\xbb" * 32).transact(
        {"from": env.challenger, "value": env.challenger_bond, **GAS})
    chal_before = w3.eth.get_balance(env.challenger)
    mgr.functions.resolveQualityDispute(aid, True).transact({"from": env.adjudicator, **GAS})
    # honest challenger bond returned; worker slashed 50% of stake
    assert w3.eth.get_balance(env.challenger) == chal_before + env.challenger_bond
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == stake // 2
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_honest_challenger_reject_unchanged(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "chal-rej")
    env.run_to_dispute(aid, bid, stake)
    agent_before = w3.eth.get_balance(env.agent)
    mgr.functions.openQualityDispute(aid, b"\xcc" * 32).transact(
        {"from": env.challenger, "value": env.challenger_bond, **GAS})
    mgr.functions.resolveQualityDispute(aid, False).transact({"from": env.adjudicator, **GAS})
    # worker paid in full; false challenger bond slashed to poster fund
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond


def test_fast_reject_ignores_challenger_evidence_free_dispute_too(env):
    # the fast path is role-agnostic: a non-poster evidence-free dispute
    # is fast-rejectable as well
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "chalfree")
    env.run_to_dispute(aid, bid, stake)
    txh = mgr.functions.openQualityDispute(aid, bytes(32)).transact(
        {"from": env.challenger, "value": env.challenger_bond, **GAS})
    filing_ts = w3.eth.get_block(w3.eth.get_transaction_receipt(txh).blockNumber).timestamp
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED
