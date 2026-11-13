"""W-84 regression tests: dark-adjudicator bond rule, reconciled with J4.

W-84 finding (verified on current main): an honest challenger who submitted
real evidence still forfeits their bond via _resolveDisputeReject when the
adjudicator goes dark -- and the stale NatSpec at openQualityDispute promised
the bond is "returned if the adjudicator goes dark", contradicting the code.

Reconciliation (see the corrected NatSpec on openQualityDispute/timeout and
the contract header): J4's forfeiture rule STANDS. On-chain evidence proves
RETRIEVABILITY, not merit -- the filer chooses both the digest and the
preimage, so a registered junk preimage is exactly as "substantiated" as real
evidence, and only a live adjudicator's ruling can tell them apart.
Conditioning a dark-timeout refund on evidenceSubmitted would re-open the
exact hole J4 closed: griefing would cost gas + lockup only, precisely when
no adjudicator is present to check it. The honest challenger's bond is
protected by a live adjudicator's uphold ruling (bond returned), not by the
timeout. These tests pin that reconciled rule -- including the exact
doc/code contradiction W-84 found.

Self-contained: compiles contracts/ExecutionEscrowManager.sol (plus its
IExecutionEscrowManager.sol interface) with solc 0.8.24, optimizer 200 runs,
viaIR, then drives the contracts with web3 + eth-tester (PyEVM backend).

Deliberately does NOT import sincor2 and does not rely on the repo-root
conftest. Run with:

    ~/.venvs/sincor2/bin/python -m pytest --confcutdir=tests/pytest \\
        tests/pytest/test_w84_dark_adjudicator.py -q
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
    return w3.keccak(text=f"w84-{tag}-{next(_aid_seq)}")


@pytest.fixture()
def env():
    return Env()


def open_dispute(env, aid, digest, filer):
    """Open a dispute; returns the filing timestamp."""
    txh = env.mgr.functions.openQualityDispute(aid, digest).transact(
        {"from": filer, "value": env.challenger_bond, **GAS})
    return env.w3.eth.get_block(env.w3.eth.get_transaction_receipt(txh).blockNumber).timestamp


# ---------------------------------------------------------------------------
# W-84 reconciled rule: dark timeout forfeits even WITH evidence submitted.
# This pins the corrected openQualityDispute NatSpec ("returned ONLY on a
# live adjudicator's uphold ruling") and directly contradicts the old stale
# promise ("returned if the adjudicator goes dark").
# ---------------------------------------------------------------------------

def test_w84_dark_timeout_forfeits_with_evidence_submitted(env):
    """W-84 scenario, reconciled rule: challenger files with a digest AND
    registers the matching preimage (evidenceSubmitted=true -- the most an
    honest challenger can do on-chain), then the adjudicator goes dark for
    the full window. timeout() FORFEITS the bond to the poster's fund.

    Rationale (see corrected NatSpec): the preimage proves retrievability,
    not merit -- the filer chose both digest and preimage, so this evidence
    is indistinguishable on-chain from a griefer's junk preimage. Only a
    live adjudicator's ruling can tell them apart."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "honest-evidence")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    evidence = b"genuine evidence of worker fault: resultHash mismatches payload"
    digest = w3.keccak(evidence)
    challenger_pre = w3.eth.get_balance(env.challenger)
    filing_ts = open_dispute(env, aid, digest, env.challenger)
    # honest challenger registers the real preimage inside the window
    mgr.functions.submitEvidence(aid, evidence).transact({"from": env.challenger, **GAS})
    assert mgr.functions.getDispute(aid).call()[4] is True  # evidenceSubmitted
    # adjudicator goes dark for the full 48h; nobody can fast-reject a
    # substantiated dispute
    with pytest.raises(TransactionFailed):
        mgr.functions.rejectUnsubstantiatedDispute(aid).transact({"from": env.anyone, **GAS})
    env.travel_past(filing_ts + ADJUDICATION_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    challenger_before = w3.eth.get_balance(env.challenger)
    mgr.functions.timeout(aid).transact({"from": env.anyone, **GAS})
    # worker paid in full (optimistic default)
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake
    # reconciled rule: bond FORFEITED to the poster's fund -- NOT returned,
    # even though evidence was submitted. This is the corrected doc.
    assert w3.eth.get_balance(env.challenger) == challenger_before
    assert mgr.functions.getPosterReAuctionBalance(env.poster).call() == env.challenger_bond
    assert w3.eth.get_balance(env.challenger) <= challenger_pre - env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_w84_poster_filer_dark_timeout_forfeits_bond_to_worker(env):
    """Poster-filed dispute with evidence submitted, adjudicator dark:
    the bond is forfeited TO THE WORKER (reject routing for self-disputes),
    not back to the poster -- otherwise the "slash" would be a self-transfer
    and grief would cost gas only."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "poster-dark")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    evidence = b"poster evidence bytes"
    digest = w3.keccak(evidence)
    poster_pre = w3.eth.get_balance(env.poster)
    filing_ts = open_dispute(env, aid, digest, env.poster)
    mgr.functions.submitEvidence(aid, evidence).transact({"from": env.poster, **GAS})
    assert mgr.functions.getDispute(aid).call()[4] is True
    env.travel_past(filing_ts + ADJUDICATION_WINDOW)
    agent_before = w3.eth.get_balance(env.agent)
    mgr.functions.timeout(aid).transact({"from": env.anyone, **GAS})
    # worker gets bid + stake + the poster's forfeited bond
    assert w3.eth.get_balance(env.agent) == agent_before + bid + stake + env.challenger_bond
    assert w3.eth.get_balance(env.poster) <= poster_pre - env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED


def test_w84_live_uphold_returns_challenger_bond(env):
    """Live-adjudicator path unchanged by the reconciliation: a live
    adjudicator upholds the dispute -> the honest challenger's bond is
    returned. This is the protection the corrected doc promises."""
    w3, mgr = env.w3, env.mgr
    bid = w3.to_wei(2, "ether")
    stake = bid * MIN_STAKE_BPS // 10000
    aid = new_aid(w3, "uphold")
    env.run_to_dispute(aid, bid, stake, adj_dur=ADJUDICATION_WINDOW)
    evidence = b"real evidence"
    digest = w3.keccak(evidence)
    challenger_pre = w3.eth.get_balance(env.challenger)
    open_dispute(env, aid, digest, env.challenger)
    mgr.functions.submitEvidence(aid, evidence).transact({"from": env.challenger, **GAS})
    mgr.functions.resolveQualityDispute(aid, True).transact({"from": env.adjudicator, **GAS})
    # bond returned: challenger is down at most gas, not the full bond
    assert w3.eth.get_balance(env.challenger) > challenger_pre - env.challenger_bond
    assert mgr.functions.getEscrow(aid).call()[13] == FINALIZED
