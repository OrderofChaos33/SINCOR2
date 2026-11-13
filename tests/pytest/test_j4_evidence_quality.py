"""J4 regression tests: evidence-QUALITY bar for dispute fast paths.

Red-team PoC (2026-10-06, poc8_j4_evidence_fastpath_bypass.py, CONFIRMED):
`rejectEvidenceFreeDispute` reverted DisputeHasEvidence on ANY non-zero
batchDigest, so a griefer attached a junk digest, kept the full 48h dispute
delay, and got their bond REFUNDED by the dark-adjudicator timeout --
a ~zero-cost (gas-only) 48h stall of the worker's payout.

The fix has two parts:
  1. Evidence commitment: a batchDigest is only a commitment. It counts as
     evidence ONLY once the matching preimage bytes are registered on-chain
     via submitEvidence() within DISPUTE_EVIDENCE_WINDOW. A junk digest with
     no registered preimage is fast-rejectable as unsubstantiated via
     rejectUnsubstantiatedDispute() -- the digest alone no longer stalls.
  2. Cost-bearing dark timeout: timeout() on a dispute the adjudicator left
     to die now FORFEITS the challenger bond (same routing as a rejected
     dispute) instead of refunding it. Stalling costs the full bond.

Self-contained: compiles contracts/ExecutionEscrowManager.sol (plus its
IExecutionEscrowManager.sol interface) with solc 0.8.24, optimizer 200 runs,
viaIR, then drives the contracts with web3 + eth-tester (PyEVM backend).

Deliberately does NOT import sincor2 and does not rely on the repo-root
conftest. Run with:

    /tmp/evmtest/bin/python -m pytest --confcutdir=tests/pytest \\
        tests/pytest/test_j4_evidence_quality.py -q
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
EVIDENCE_WINDOW = 6 * 3600  # must match DISPUTE_EVIDENCE_WINDOW
ADJUDICATION_WINDOW = 48 * 3600


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

    def run_to_dispute(self, aid, bid, stake, adj_dur=600):
        """Fresh escrow with stake deposited and result submitted."""
        w3, mgr = self.w3, self.mgr
        self.init_escrow(aid, self.poster, self.agent, bid, 0, bid, adj_dur=adj_dur)
        mgr.functions.depositStake(aid).transact({"from": self.agent, "value": stake, **GAS})
        mgr.functions.submitResult(aid, b"\x11" * 32).transact({"from": self.agent, **GAS})


_aid_seq = itertools.count()


def new_aid(w3, tag):
    return w3.keccak(text=f"j4-{tag}-{next(_aid_seq)}")


def events_of(mgr, name, receipt):
    return mgr.events[name]().process_receipt(receipt)


@pytest.fixture()
def env():
    return Env()


def open_dispute(env, aid, digest, filer):
    """Open a dispute; returns the filing timestamp."""
    txh = env.mgr.functions.openQualityDispute(aid, digest).transact(
        {"from": filer, "value": env.challenger_bond, **GAS})
    return env.w3.eth.get_block(env.w3.eth.get_transaction_receipt(txh).blockNumber).timestamp


# ---------------------------------------------------------------------------
# (a) PoC defeated: junk digest no longer stalls free
# ---------------------------------------------------------------------------

def test_junk_digest_without_evidence_is_fast_rejected(env):
    """Lazy griefer: junk non-zero digest, no preimage ever registered.
    After the evidence window, rejectUnsubstantiatedDispute() fires --
    worker paid in full, bond slashed to the poster's fund. The J4 stall
    dies at 6h instead of 48h, and it costs the griefer the full bond."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "junk-lazy")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    junk = w3.keccak(text="junk-digest-nobody-can-open")
    griefer_before = w3.eth.get_balance(env.challenger)
    filing_ts = open_dispute(env, aid, junk, env.challenger)
    # the digest was recorded but no evidence was ever submitted
    d = mgr.functions.getDispute(aid).call()
    assert d[2] == junk and d[4] is False
    # fast path must not fire inside the window
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS})
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    # the legacy zero-digest entry point still refuses (digest != 0) --
    # its semantics are unchanged; the QUALITY bar is the new function.
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS}))
    esc = mgr.functions.getEscrow(aid).call()
    assert esc[13] == FINALIZED and esc[14] is False
    # worker paid in full without waiting 48h; griefer bond -> poster fund
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    # griefer net loss >= the full bond (bond slashed, not refunded)
    assert w3.eth.get_balance(env.challenger) <= griefer_before - env.challenger_bond
    resolved = events_of(mgr, "QualityDisputeResolved", receipt)
    assert len(resolved) == 1 and resolved[0]["args"]["slashUpheld"] is False
    # timeout() after the fast rejection has nothing left to do
    with pytest.raises(TransactionFailed):
        mgr.functions.timeout(aid).transact({"from": env.anyone, **GAS})


def test_adaptive_griefer_junk_bytes_still_costs_bond_at_dark_timeout(env):
    """Adaptive griefer: junk digest + junk preimage bytes registered (cheap),
    so the 6h fast path cannot fire. Adjudicator goes dark for the full 48h.
    This is the exact PoC scenario -- and now timeout() FORFEITS the bond
    instead of refunding it: the stall costs the full bond, not gas only."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "junk-adaptive")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    junk_bytes = b"junk evidence, not really evidence" * 4
    junk_digest = w3.keccak(junk_bytes)
    griefer_pre = w3.eth.get_balance(env.challenger)
    filing_ts = open_dispute(env, aid, junk_digest, env.challenger)
    # griefer registers junk bytes: preimage matches, so the dispute is
    # "substantiated" by the on-chain test and survives the 6h fast path
    mgr.functions.submitEvidence(aid, junk_bytes).transact({"from": env.challenger, **GAS})
    assert mgr.functions.getDispute(aid).call()[4] is True
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS})
    assert mgr.functions.getEscrow(aid).call()[14] is True  # still active
    # adjudicator stays dark; worker waits out the full 48h clock
    env.travel_past(filing_ts + ADJUDICATION_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    griefer_before = w3.eth.get_balance(env.challenger)
    mgr.functions.timeout(aid).transact({"from": env.anyone, **GAS})
    # worker paid in full (optimistic default)
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    # J4: bond FORFEITED to the poster's fund -- NOT refunded to the griefer.
    # Net cost of the 48h stall: the full bond, not gas only.
    assert w3.eth.get_balance(env.challenger) == griefer_before
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    assert w3.eth.get_balance(env.challenger) <= griefer_pre - env.challenger_bond


def test_dark_timeout_forfeits_bond_even_without_any_evidence(env):
    """Belt-and-braces: even a bare junk digest that somehow survives to the
    dark timeout (e.g. nobody bothered to fast-reject at 6h) forfeits its
    bond instead of being refunded. There is no path left where the PoC's
    ~zero-cost stall survives."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "junk-dark")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    filing_ts = open_dispute(env, aid, w3.keccak(text="bare-junk"), env.challenger)
    # nobody fast-rejects; adjudicator dark; full 48h passes
    env.travel_past(filing_ts + ADJUDICATION_WINDOW)
    griefer_before = w3.eth.get_balance(env.challenger)
    mgr.functions.timeout(aid).transact({"from": env.anyone, **GAS})
    assert w3.eth.get_balance(env.challenger) == griefer_before  # not refunded
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


# ---------------------------------------------------------------------------
# (b) Legitimate evidence-free fast path still works
# ---------------------------------------------------------------------------

def test_evidence_free_fast_path_unchanged(env):
    """Zero-digest dispute: the original rejectEvidenceFreeDispute() path
    fires after the window exactly as before -- worker paid, bond slashed."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "free")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    filing_ts = open_dispute(env, aid, bytes(32), env.challenger)
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_unsubstantiated_fast_path_also_fires_on_zero_digest(env):
    """The new quality-bar function is a superset: it also fires on a
    zero-digest dispute, with identical economics."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "free2")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    filing_ts = open_dispute(env, aid, bytes(32), env.challenger)
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS})
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


# ---------------------------------------------------------------------------
# (c) Real evidence still blocks the fast path
# ---------------------------------------------------------------------------

def test_real_evidence_blocks_fast_path_and_uphold_returns_bond(env):
    """Honest challenger: non-zero digest + matching preimage registered in
    the window. Both fast paths revert after the window; the adjudicator
    upholds; the honest bond is returned and the worker takes the 50% slash."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "real")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    evidence = b"batch-7 delivery manifest: 3 of 12 items missing, photos attached" * 3
    digest = w3.keccak(evidence)
    filing_ts = open_dispute(env, aid, digest, env.challenger)
    # anyone may register the preimage (permissionless evidence availability)
    receipt = w3.eth.get_transaction_receipt(
        mgr.functions.submitEvidence(aid, evidence).transact({"from": env.anyone, **GAS}))
    d = mgr.functions.getDispute(aid).call()
    assert d[2] == digest and d[4] is True
    evts = events_of(mgr, "EvidenceSubmitted", receipt)
    assert len(evts) == 1
    assert evts[0]["args"]["submitter"] == env.anyone and evts[0]["args"]["evidenceHash"] == digest
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    # real (substantiated) evidence blocks BOTH fast paths
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectEvidenceFreeDispute(aid).transact({"from": env.anyone, **GAS})
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS})
    assert mgr.functions.getEscrow(aid).call()[14] is True  # still active
    # adjudicator upholds: honest challenger bond returned, worker slashed 50%
    chal_before = w3.eth.get_balance(env.challenger)
    mgr.functions.resolveQualityDispute(aid, True).transact({"from": env.adjudicator, **GAS})
    assert w3.eth.get_balance(env.challenger) == chal_before + env.challenger_bond
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == stake // 2
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_real_evidence_rejected_dispute_slashes_bond(env):
    """Substantiated dispute the adjudicator rejects: normal reject
    economics -- worker paid in full, false challenger bond to poster fund."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "real-rej")
    env.run_to_dispute(aid, bid, stake)
    evidence = b"delivery was actually fine, see signed receipt" * 4
    filing_ts = open_dispute(env, aid, w3.keccak(evidence), env.challenger)
    mgr.functions.submitEvidence(aid, evidence).transact({"from": env.challenger, **GAS})
    agent_before = w3.eth.get_balance(env.agent)
    mgr.functions.resolveQualityDispute(aid, False).transact({"from": env.adjudicator, **GAS})
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


# ---------------------------------------------------------------------------
# Evidence-submission edge cases
# ---------------------------------------------------------------------------

def test_submit_evidence_wrong_preimage_reverts(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "mismatch")
    env.run_to_dispute(aid, bid, stake)
    open_dispute(env, aid, w3.keccak(b"the real evidence"), env.challenger)
    with pytest.raises(TransactionFailed):
        mgr.functions.submitEvidence(aid, b"some other bytes").transact(
            {"from": env.challenger, **GAS})
    assert mgr.functions.getDispute(aid).call()[4] is False  # still unsubstantiated


def test_submit_evidence_after_window_reverts(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "late")
    env.run_to_dispute(aid, bid, stake)
    evidence = b"too little, too late" * 8
    filing_ts = open_dispute(env, aid, w3.keccak(evidence), env.challenger)
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    with pytest.raises(TransactionFailed):
        mgr.functions.submitEvidence(aid, evidence).transact({"from": env.challenger, **GAS})
    # and the now-unsubstantiated dispute is fast-rejectable
    mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS})
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_submit_evidence_without_active_dispute_reverts(env):
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "nodisp")
    env.run_to_dispute(aid, bid, stake)
    with pytest.raises(TransactionFailed):
        mgr.functions.submitEvidence(aid, b"evidence for nothing").transact(
            {"from": env.challenger, **GAS})


def test_submit_evidence_on_zero_digest_dispute_reverts(env):
    """A zero-digest dispute committed to no evidence: no preimage can match
    bytes32(0), so submitEvidence always reverts and the dispute stays
    fast-rejectable."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "zerod")
    env.run_to_dispute(aid, bid, stake)
    open_dispute(env, aid, bytes(32), env.challenger)
    with pytest.raises(TransactionFailed):
        mgr.functions.submitEvidence(aid, b"anything").transact({"from": env.challenger, **GAS})
    with pytest.raises(TransactionFailed):
        mgr.functions.submitEvidence(aid, b"").transact({"from": env.challenger, **GAS})


def test_double_evidence_submission_is_idempotent(env):
    """Registering the preimage twice is harmless: flag stays set, no state
    corruption, dispute still shielded from the fast path."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "twice")
    env.run_to_dispute(aid, bid, stake)
    evidence = b"idempotent evidence" * 8
    filing_ts = open_dispute(env, aid, w3.keccak(evidence), env.challenger)
    mgr.functions.submitEvidence(aid, evidence).transact({"from": env.challenger, **GAS})
    mgr.functions.submitEvidence(aid, evidence).transact({"from": env.anyone, **GAS})
    assert mgr.functions.getDispute(aid).call()[4] is True
    env.travel_past(filing_ts + EVIDENCE_WINDOW)
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS})
    assert mgr.functions.getEscrow(aid).call()[14] is True
