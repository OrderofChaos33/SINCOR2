"""eth-tester integration tests for the auction-core selection bridge.

Covers CommitRevealAuction's post-review bridge surface:
  - selectWinnerAndFund() (poster-only, atomic escrow init, exact funding)
  - vickreyResult() (lowest wins, second-lowest price, sole-bidder
    first-price fallback, ties -> earliest committer, unrevealed ignored)
  - bidder tracking, instant timeout() races, escrowManager unset/zero paths,
    execution-params wiring, price-domain bound (uint96).

Self-contained: compiles contracts/CommitRevealAuction.sol (plus
IExecutionEscrowManager.sol and ExecutionEscrowManager.sol) with solc 0.8.24,
optimizer 200 runs, viaIR, then drives the contracts with web3 + eth-tester
(PyEVM backend). Deliberately does NOT import sincor2 or marketplace
packages. Run with:

    /tmp/bridgeevm/bin/python -m pytest --noconftest \\
        tests/pytest/test_selection_bridge.py -q
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
MIN_STAKE_BPS = 5000  # ratified 2026-09-25
CHALLENGER_BOND = Web3.to_wei(0.02, "ether")  # ratified 2026-09-25
UINT96_MAX = 2**96 - 1

# ExecutionState enum indices (must match IExecutionEscrowManager.sol)
UNINITIALIZED, AWAITING_STAKE, EXECUTION_PHASE, DISPUTE_WINDOW, FINALIZED, SLASHED, TIMED_OUT = range(7)

TX = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    srcs = {}
    for name in ("CommitRevealAuction.sol", "ExecutionEscrowManager.sol", "IExecutionEscrowManager.sol"):
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
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION, allow_paths=CONTRACTS_DIR)
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


class Env:
    """Fresh chain + wired auction core and escrow manager per test."""

    def __init__(self):
        self.tester = EthereumTester(PyEVMBackend())
        self.w3 = Web3(EthereumTesterProvider(self.tester))
        accts = self.tester.get_accounts()
        self.deployer, self.poster, self.adjudicator = accts[0], accts[1], accts[2]
        self.bidders = accts[3:8]
        self.anyone = accts[8]

        abi_a, bin_a = COMPILED["auction"]
        txh = self.w3.eth.contract(abi=abi_a, bytecode=bin_a).constructor().transact(
            {"from": self.deployer, **TX})
        self.auction = self.w3.eth.contract(
            address=self.w3.eth.get_transaction_receipt(txh).contractAddress, abi=abi_a)

        abi_e, bin_e = COMPILED["escrow"]
        txh = self.w3.eth.contract(abi=abi_e, bytecode=bin_e).constructor(
            self.auction.address, self.adjudicator, MIN_STAKE_BPS, CHALLENGER_BOND
        ).transact({"from": self.deployer, **TX})
        self.escrow = self.w3.eth.contract(
            address=self.w3.eth.get_transaction_receipt(txh).contractAddress, abi=abi_e)

        # owner (deployer) wires the bridge
        self.auction.functions.setEscrowManager(self.escrow.address).transact(
            {"from": self.deployer, **TX})

        # extra funded accounts for the scale test (each commit needs a
        # distinct funded bidder)
        self.scale_bidders = []
        for _ in range(40):
            acct = self.w3.eth.account.create()
            self.tester.add_account(acct.key.hex())
            self.w3.eth.send_transaction(
                {"from": self.deployer, "to": acct.address,
                 "value": self.w3.to_wei(1, "ether"), **TX})
            self.scale_bidders.append(acct.address)

        self.secrets = {}  # (aid, bidder) -> (price, salt, agentIdHash)

    # -- helpers ---------------------------------------------------------
    def travel_past(self, timestamp, buffer=5):
        self.tester.time_travel(timestamp + buffer)
        self.tester.mine_block()

    def now(self):
        return self.tester.get_block_by_number("latest")["timestamp"]

    def open(self, poster=None, commit_w=60, reveal_w=60):
        aid = new_aid(self.w3, "auction")
        self.auction.functions.openAuction(aid, commit_w, reveal_w).transact(
            {"from": poster or self.poster, **TX})
        return aid

    def commit(self, aid, bidder, price):
        salt = self.w3.keccak(text=f"salt-{aid.hex()}-{bidder}")
        agent_id = self.w3.keccak(text=f"agent-{bidder}")
        commitment = self.w3.solidity_keccak(
            ["uint256", "bytes32", "bytes32"], [price, salt, agent_id])
        self.auction.functions.commit(aid, commitment).transact({"from": bidder, **TX})
        self.secrets[(aid, bidder)] = (price, salt, agent_id)

    def reveal(self, aid, bidder):
        price, salt, agent_id = self.secrets[(aid, bidder)]
        self.auction.functions.reveal(aid, price, salt, agent_id).transact(
            {"from": bidder, **TX})

    def run_auction(self, prices, commit_w=60, reveal_w=60):
        """Open, commit, reveal for a list of (bidder, price); returns aid."""
        aid = self.open(commit_w=commit_w, reveal_w=reveal_w)
        for bidder, price in prices:
            self.commit(aid, bidder, price)
        a = self.auction.functions.auctions(aid).call()
        self.travel_past(a[3])  # commitDeadline
        for bidder, _ in prices:
            self.reveal(aid, bidder)
        self.travel_past(a[4])  # revealDeadline
        return aid

    def select(self, aid, poster=None, credit=0, value=None, sender=None):
        if value is None:
            _, price = self.auction.functions.vickreyResult(aid).call()
            value = price - credit
        return self.w3.eth.get_transaction_receipt(
            self.auction.functions.selectWinnerAndFund(aid, credit).transact(
                {"from": sender or poster or self.poster, "value": value, **TX}))


_aid_seq = itertools.count()


def new_aid(w3, tag):
    return w3.keccak(text=f"bridge-{tag}-{next(_aid_seq)}")


def events_of(contract, name, receipt):
    return getattr(contract.events, name)().process_receipt(receipt)


@pytest.fixture()
def env():
    return Env()


# ---------------------------------------------------------------------------
# 1. Vickrey selection semantics
# ---------------------------------------------------------------------------

def test_vickrey_lowest_wins_second_price(env):
    b0, b1, b2 = env.bidders[:3]
    aid = env.run_auction([(b0, env.w3.to_wei(3, "ether")),
                           (b1, env.w3.to_wei(1, "ether")),
                           (b2, env.w3.to_wei(2, "ether"))])
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b1
    assert price == env.w3.to_wei(2, "ether")


def test_vickrey_single_bidder_first_price_fallback(env):
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, env.w3.to_wei(5, "ether"))])
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b0
    assert price == env.w3.to_wei(5, "ether")


def test_vickrey_tie_breaks_to_earliest_committer(env):
    b0, b1 = env.bidders[:2]
    p = env.w3.to_wei(2, "ether")
    aid = env.run_auction([(b0, p), (b1, p)])
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b0  # b0 committed first
    assert price == p


def test_vickrey_ignores_unrevealed_commits(env):
    b0, b1 = env.bidders[:2]
    aid = env.open()
    env.commit(aid, b0, env.w3.to_wei(1, "ether"))  # never reveals
    env.commit(aid, b1, env.w3.to_wei(4, "ether"))
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[3])
    env.reveal(aid, b1)
    env.travel_past(a[4])
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b1 and price == env.w3.to_wei(4, "ether")


def test_vickrey_descending_and_ascending_orders_agree(env):
    b0, b1, b2 = env.bidders[:3]
    prices = [env.w3.to_wei(x, "ether") for x in (7, 5, 3)]
    aid = env.run_auction(list(zip([b0, b1, b2], prices)))
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b2 and price == env.w3.to_wei(5, "ether")
    aid2 = env.run_auction(list(zip([b0, b1, b2], list(reversed(prices)))))
    winner2, price2 = env.auction.functions.vickreyResult(aid2).call()
    assert winner2 == b0 and price2 == env.w3.to_wei(5, "ether")


def test_no_revealed_bids_select_reverts_then_timeout_works(env):
    b0 = env.bidders[0]
    aid = env.open()
    env.commit(aid, b0, env.w3.to_wei(1, "ether"))
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[4])
    with pytest.raises(TransactionFailed):
        env.select(aid)
    # nobody revealed: permissionless timeout still finalizes cleanly
    env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})
    assert env.auction.functions.auctions(aid).call()[1] is True  # finalized


# ---------------------------------------------------------------------------
# 2. selectWinnerAndFund: atomic escrow init + exact funding
# ---------------------------------------------------------------------------

def test_select_winner_full_eth_funding(env):
    b0, b1 = env.bidders[:2]
    aid = env.run_auction([(b0, env.w3.to_wei(3, "ether")),
                           (b1, env.w3.to_wei(1, "ether"))])
    r = env.select(aid)
    evts = events_of(env.auction, "WinnerSelected", r)
    assert len(evts) == 1
    # prices are 3 and 1 ETH: lowest (b1) wins at second-lowest = 3 ETH
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b1
    assert price == env.w3.to_wei(3, "ether")
    assert evts[0]["args"]["worker"] == b1
    assert evts[0]["args"]["price"] == price

    assert env.auction.functions.auctions(aid).call()[1] is True  # finalized
    esc = env.escrow.functions.getEscrow(aid).call()
    assert esc[0] == env.poster and esc[1] == b1
    assert esc[2] == price and esc[3] == price and esc[4] == 0  # all-fresh-ETH
    assert esc[13] == AWAITING_STAKE


def test_funding_mismatch_reverts_and_auction_stays_selectable(env):
    # Atomic rollback: a mispriced funding tx reverts WITHOUT finalizing,
    # so the poster can simply retry with the right amount.
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    _, price = env.auction.functions.vickreyResult(aid).call()
    with pytest.raises(TransactionFailed):
        env.select(aid, value=price - 1)  # 1 wei short
    assert env.auction.functions.auctions(aid).call()[1] is False  # not finalized
    env.select(aid)  # retry with exact funding works
    assert env.auction.functions.auctions(aid).call()[1] is True


def test_select_winner_two_sided_funding_with_credit(env):
    # Build the poster's re-auction credit via a ghosting slash on auction 1,
    # then apply it as partial funding on auction 2.
    b0, b1 = env.bidders[:2]
    stake = env.w3.to_wei(2, "ether") * MIN_STAKE_BPS // 10000  # 1 ETH

    aid1 = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    env.select(aid1)
    env.escrow.functions.depositStake(aid1).transact({"from": b0, "value": stake, **TX})
    esc1 = env.escrow.functions.getEscrow(aid1).call()
    env.travel_past(esc1[8])  # executionDeadline; worker ghosts
    env.escrow.functions.timeout(aid1).transact({"from": env.anyone, **TX})
    assert env.escrow.functions.getPosterReAuctionBalance(env.poster).call() == stake

    aid2 = env.run_auction([(b1, env.w3.to_wei(3, "ether"))])
    env.select(aid2, credit=stake)  # msg.value = 3 ETH - 1 ETH credit
    esc2 = env.escrow.functions.getEscrow(aid2).call()
    assert esc2[2] == env.w3.to_wei(3, "ether")  # bidAmount
    assert esc2[3] == env.w3.to_wei(2, "ether")  # fresh ETH
    assert esc2[4] == stake                       # credit backing
    assert env.escrow.functions.getPosterReAuctionBalance(env.poster).call() == 0


def test_credit_exceeding_balance_reverts_atomically(env):
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    with pytest.raises(TransactionFailed):
        env.select(aid, credit=env.w3.to_wei(1, "ether"), value=env.w3.to_wei(1, "ether"))
    assert env.auction.functions.auctions(aid).call()[1] is False


# ---------------------------------------------------------------------------
# 3. Access control + sequencing
# ---------------------------------------------------------------------------

def test_select_by_non_poster_reverts(env):
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    with pytest.raises(TransactionFailed):
        env.select(aid, sender=env.anyone)
    with pytest.raises(TransactionFailed):
        env.select(aid, sender=b0)  # even the winner can't self-select


def test_select_before_reveal_deadline_reverts(env):
    b0 = env.bidders[0]
    aid = env.open()
    env.commit(aid, b0, env.w3.to_wei(2, "ether"))
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[3])
    env.reveal(aid, b0)
    # reveal window still open -> too early
    with pytest.raises(TransactionFailed):
        env.select(aid, value=env.w3.to_wei(2, "ether"))


def test_select_twice_reverts(env):
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    env.select(aid)
    with pytest.raises(TransactionFailed):
        env.select(aid)


def test_timeout_before_select_blocks_selection(env):
    # Instant permissionless timeout (ratified): if timeout lands first,
    # the poster's selection reverts; the poster loses gas only.
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})
    assert env.auction.functions.auctions(aid).call()[1] is True
    with pytest.raises(TransactionFailed):
        env.select(aid)
    # double timeout also reverts
    with pytest.raises(TransactionFailed):
        env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})


def test_timeout_too_early_reverts(env):
    aid = env.open()
    with pytest.raises(TransactionFailed):
        env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})


def test_unrevealed_auction_timeout_is_clean(env):
    b0 = env.bidders[0]
    aid = env.open()
    env.commit(aid, b0, env.w3.to_wei(1, "ether"))
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[4])
    env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})
    assert env.auction.functions.auctions(aid).call()[1] is True


# ---------------------------------------------------------------------------
# 4. escrowManager wiring
# ---------------------------------------------------------------------------

def test_no_escrow_manager_reverts_but_timeout_still_works(env):
    abi_a, bin_a = COMPILED["auction"]
    bare = env.w3.eth.contract(abi=abi_a, bytecode=bin_a)
    txh = bare.constructor().transact({"from": env.deployer, **TX})
    addr = env.w3.eth.get_transaction_receipt(txh).contractAddress
    orphan = env.w3.eth.contract(address=addr, abi=abi_a)

    aid = env.w3.keccak(text="orphan-auction")
    orphan.functions.openAuction(aid, 60, 60).transact({"from": env.poster, **TX})
    salt = env.w3.keccak(text="s"); agent_id = env.w3.keccak(text="a")
    price = env.w3.to_wei(1, "ether")
    com = env.w3.solidity_keccak(["uint256", "bytes32", "bytes32"], [price, salt, agent_id])
    orphan.functions.commit(aid, com).transact({"from": env.bidders[0], **TX})
    a = orphan.functions.auctions(aid).call()
    env.travel_past(a[3])
    orphan.functions.reveal(aid, price, salt, agent_id).transact({"from": env.bidders[0], **TX})
    env.travel_past(a[4])

    with pytest.raises(TransactionFailed):  # NoEscrowManager
        orphan.functions.selectWinnerAndFund(aid, 0).transact(
            {"from": env.poster, "value": price, **TX})
    # timeout path is unaffected
    orphan.functions.timeout(aid).transact({"from": env.anyone, **TX})
    assert orphan.functions.auctions(aid).call()[1] is True


def test_escrow_manager_zero_address_bricks_select(env):
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    env.auction.functions.setEscrowManager(
        "0x0000000000000000000000000000000000000000").transact({"from": env.deployer, **TX})
    with pytest.raises(TransactionFailed):
        env.select(aid)
    # owner can repair the wiring
    env.auction.functions.setEscrowManager(env.escrow.address).transact(
        {"from": env.deployer, **TX})
    env.select(aid)
    assert env.auction.functions.auctions(aid).call()[1] is True


def test_owner_only_setters(env):
    with pytest.raises(TransactionFailed):
        env.auction.functions.setEscrowManager(env.escrow.address).transact(
            {"from": env.anyone, **TX})
    with pytest.raises(TransactionFailed):
        env.auction.functions.setExecutionParams(
            (1, 1, 1, 1)).transact({"from": env.anyone, **TX})


def test_execution_params_wiring(env):
    b0 = env.bidders[0]
    env.auction.functions.setExecutionParams((111, 222, 333, 44)).transact(
        {"from": env.deployer, **TX})
    aid = env.run_auction([(b0, env.w3.to_wei(2, "ether"))])
    env.select(aid)
    esc = env.escrow.functions.getEscrow(aid).call()
    assert esc[7] == 111    # executionDuration
    assert esc[9] == 222    # disputeWindowDuration
    assert esc[10] == 333   # adjudicationWindowDuration
    # stakeDepositDeadline ~= selection time + 44
    assert abs(esc[6] - (env.now() + 44)) <= 30


# ---------------------------------------------------------------------------
# 5. Price domain: uint96 bound (reveal-time)
# ---------------------------------------------------------------------------

def test_price_above_uint96_max_reverts_at_reveal(env):
    # Without the reveal-time bound, uint96(price) in selectWinnerAndFund
    # truncates silently and a costless grief-bid bricks selection.
    b0 = env.bidders[0]
    aid = env.open()
    env.commit(aid, b0, UINT96_MAX + 1)
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[3])
    with pytest.raises(TransactionFailed):  # PriceTooLarge
        env.reveal(aid, b0)
    # the auction is still usable: timeout finalizes cleanly
    env.travel_past(a[4])
    env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})


def test_price_at_uint96_max_boundary_accepted(env):
    b0, b1 = env.bidders[:2]
    aid = env.open()
    # boundary price reveals fine (selection would need the funds, so we
    # only assert reveal + view here)
    env.commit(aid, b0, UINT96_MAX)
    env.commit(aid, b1, env.w3.to_wei(1, "ether"))
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[3])
    env.reveal(aid, b0)
    env.reveal(aid, b1)
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b1 and price == UINT96_MAX


def test_zero_price_reveal_select_reverts_timeout_recovers(env):
    # A zero-price bid is representable but the escrow rejects bidAmount=0;
    # selection reverts atomically and the poster falls back to timeout().
    b0 = env.bidders[0]
    aid = env.run_auction([(b0, 0)])
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert winner == b0 and price == 0
    with pytest.raises(TransactionFailed):  # escrow ZeroBidAmount -> rollback
        env.select(aid, value=0)
    assert env.auction.functions.auctions(aid).call()[1] is False
    env.auction.functions.timeout(aid).transact({"from": env.anyone, **TX})
    assert env.auction.functions.auctions(aid).call()[1] is True


# ---------------------------------------------------------------------------
# 6. Gas / scale sanity
# ---------------------------------------------------------------------------

def test_many_bidders_selection_succeeds(env):
    # 30 committers: selection is O(n) but completes; documents the
    # iteration cost so future bidders-array growth stays observable.
    n = 30
    prices = [(env.scale_bidders[i], env.w3.to_wei(i + 1, "ether"))
              for i in range(n)]
    aid = env.run_auction(prices)
    winner, price = env.auction.functions.vickreyResult(aid).call()
    assert price == env.w3.to_wei(2, "ether")  # second-lowest of 1..30
    r = env.select(aid)
    assert r["gasUsed"] < 8_000_000
    assert env.auction.functions.auctions(aid).call()[1] is True
