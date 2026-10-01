"""eth-tester integration tests for ``sincor2.auction_bridge``.

Drives the real contracts (``contracts/CommitRevealAuction.sol`` +
``contracts/ExecutionEscrowManager.sol``, solc 0.8.24, viaIR) through the
bridge's two entry points:

  - ``select_winner_and_fund`` — poster-only Vickrey selection + atomic
    escrow funding, signed with eth_account (ephemeral in-memory test
    accounts only; nothing is written to disk).
  - ``initialize_escrow`` — calldata build + eth_call dry-run of the exact
    call the auction core makes (``onlyAuctionCore`` on live deploys).

Covers: happy path with event/state assertions, dry-run mode, wrong-signer
rejection, tampered-signature rejection, funding-mismatch fail-closed,
credit-overdraw fail-closed, invalid reveal price (> uint96.max), and the
escrow access-control invariant.
"""

import itertools
import os

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from sincor2 import auction_bridge
from sincor2.auction_bridge import (
    BridgeError,
    BridgeSimulationError,
    UINT96_MAX,
)

CONTRACTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
)

SOLC_VERSION = "0.8.24"
MIN_STAKE_BPS = 5000  # ratified 2026-09-25
CHALLENGER_BOND = Web3.to_wei(0.02, "ether")  # ratified 2026-09-25

# ExecutionState enum indices (must match IExecutionEscrowManager.sol)
UNINITIALIZED, AWAITING_STAKE = 0, 1

TX = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}
ONE_ETH = Web3.to_wei(1, "ether")


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


def new_aid(w3):
    return w3.keccak(text=f"bridge-test-{next(_aid_seq)}")


class Env:
    """Fresh chain + wired contracts per test. Poster is an ephemeral
    eth_account Account (in-memory only)."""

    def __init__(self):
        self.tester = EthereumTester(PyEVMBackend())
        self.w3 = Web3(EthereumTesterProvider(self.tester))
        w3 = self.w3
        accts = self.tester.get_accounts()
        self.deployer, self.adjudicator = accts[0], accts[1]
        self.bidders = accts[2:7]
        self.guardian = accts[9]

        # Ephemeral poster key — never written anywhere.
        self.poster_acct = w3.eth.account.create()
        self.tester.add_account(self.poster_acct.key.hex())
        w3.eth.send_transaction({
            "from": self.deployer, "to": self.poster_acct.address,
            "value": w3.to_wei(50, "ether"), **TX})
        self.poster = self.poster_acct.address

        abi_a, bin_a = COMPILED["auction"]
        txh = w3.eth.contract(abi=abi_a, bytecode=bin_a).constructor(
            self.guardian).transact({"from": self.deployer, **TX})
        self.auction = w3.eth.contract(
            address=w3.eth.get_transaction_receipt(txh).contractAddress, abi=abi_a)

        abi_e, bin_e = COMPILED["escrow"]
        txh = w3.eth.contract(abi=abi_e, bytecode=bin_e).constructor(
            self.auction.address, self.adjudicator, MIN_STAKE_BPS, CHALLENGER_BOND,
            self.guardian
        ).transact({"from": self.deployer, **TX})
        self.escrow = w3.eth.contract(
            address=w3.eth.get_transaction_receipt(txh).contractAddress, abi=abi_e)

        self.auction.functions.setEscrowManager(self.escrow.address).transact(
            {"from": self.deployer, **TX})
        self.secrets = {}
        # eth-tester's chain id == the contract's block.chainid on this chain
        self.chain_id = self.w3.eth.chain_id

    # -- lifecycle helpers ------------------------------------------------
    def travel_past(self, timestamp, buffer=5):
        self.tester.time_travel(timestamp + buffer)
        self.tester.mine_block()

    def open(self, commit_w=60, reveal_w=60):
        aid = new_aid(self.w3)
        self.auction.functions.openAuction(aid, commit_w, reveal_w).transact(
            {"from": self.poster, **TX})
        return aid

    def commit(self, aid, bidder, price):
        salt = self.w3.keccak(text=f"salt-{aid.hex()}-{bidder}")
        agent_id = self.w3.keccak(text=f"agent-{bidder}")
        # W-4: preimage binds auctionId + chainId
        commitment = self.w3.solidity_keccak(
            ["bytes32", "uint256", "uint256", "bytes32", "bytes32"],
            [aid, self.chain_id, price, salt, agent_id])
        self.auction.functions.commit(aid, commitment).transact(
            {"from": bidder, **TX})
        self.secrets[(aid, bidder)] = (price, salt, agent_id)

    def reveal(self, aid, bidder):
        price, salt, agent_id = self.secrets[(aid, bidder)]
        self.auction.functions.reveal(aid, price, salt, agent_id).transact(
            {"from": bidder, **TX})

    def run_auction(self, prices, commit_w=60, reveal_w=60):
        """Open, commit, reveal for [(bidder, price_wei)]; returns aid with
        the reveal window closed."""
        aid = self.open(commit_w=commit_w, reveal_w=reveal_w)
        for bidder, price in prices:
            self.commit(aid, bidder, price)
        a = self.auction.functions.auctions(aid).call()
        self.travel_past(a[3])  # commitDeadline
        for bidder, _ in prices:
            self.reveal(aid, bidder)
        self.travel_past(a[4])  # revealDeadline
        return aid


@pytest.fixture
def env():
    return Env()


def _escrow_params(env, aid):
    winner, price = env.auction.functions.vickreyResult(aid).call()
    p = env.auction.functions.executionParams().call()
    return {
        "auction_id": aid,
        "poster": env.poster,
        "selected_agent": winner,
        "bid_amount_wei": int(price),
        "execution_duration_s": int(p[0]),
        "dispute_window_s": int(p[1]),
        "adjudication_window_s": int(p[2]),
        "stake_deposit_window_s": int(p[3]),
    }


def _fund_core(env, value_wei):
    """Credit the auction core with ETH (test-only scaffolding).

    The core has no receive(), so plain transfers revert; in production it
    only holds msg.value within the selectWinnerAndFund call frame. A
    constructor selfdestruct (EIP-6780: same-tx creation) force-feeds it,
    mirroring that in-call-frame balance for the eth_call dry-run.
    """
    src = """
    // SPDX-License-Identifier: MIT
    pragma solidity ^0.8.24;
    contract CoreFunder {
        constructor(address payable target) payable {
            selfdestruct(target);
        }
    }
    """
    # Fund value + a gas buffer: the dry-run eth_call debits gas from `from`.
    gas_buffer = env.w3.to_wei(1, "ether")
    solcx.set_solc_version(SOLC_VERSION)
    out = solcx.compile_source(src, solc_version=SOLC_VERSION,
                               output_values=["abi", "bin"])
    abi, bytecode = out["<stdin>:CoreFunder"]["abi"], out["<stdin>:CoreFunder"]["bin"]
    env.w3.eth.contract(abi=abi, bytecode=bytecode).constructor(
        env.auction.address
    ).transact({"from": env.deployer, "value": int(value_wei) + gas_buffer, **TX})


# ---------------------------------------------------------------------------
# select_winner_and_fund
# ---------------------------------------------------------------------------

def test_select_winner_and_fund_happy_path(env):
    b0, b1, b2 = env.bidders[:3]
    aid = env.run_auction([(b0, ONE_ETH), (b1, 3 * ONE_ETH // 2), (b2, 2 * ONE_ETH)])

    # dry-run first: simulates everything, signs/broadcasts nothing
    before = env.w3.eth.block_number
    preview = auction_bridge.select_winner_and_fund(
        env.w3, env.auction, aid, signer=env.poster_acct, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["winner"] == Web3.to_checksum_address(b0)  # lowest wins
    assert preview["price_wei"] == 3 * ONE_ETH // 2  # second-lowest price
    assert preview["value_wei"] + preview["credit_to_apply_wei"] == preview["price_wei"]
    assert env.w3.eth.block_number == before  # nothing broadcast

    result = auction_bridge.select_winner_and_fund(
        env.w3, env.auction, aid, signer=env.poster_acct)
    assert result["tx_hash"].startswith("0x")
    assert result["winner"] == Web3.to_checksum_address(b0)
    assert result["price_wei"] == 3 * ONE_ETH // 2

    # onchain effects: escrow initialized, awaiting winner stake
    esc = auction_bridge.read_escrow(env.w3, env.escrow, aid)
    assert esc["state"] == AWAITING_STAKE
    assert esc["selected_agent"] == Web3.to_checksum_address(b0)
    assert esc["bid_amount_wei"] == 3 * ONE_ETH // 2
    assert esc["eth_deposited_wei"] == 3 * ONE_ETH // 2
    assert esc["poster"] == Web3.to_checksum_address(env.poster)


def test_select_winner_and_fund_wrong_signer_rejected(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    impostor = env.w3.eth.account.create()  # ephemeral
    before = env.w3.eth.block_number
    with pytest.raises(BridgeError, match="not the poster"):
        auction_bridge.select_winner_and_fund(
            env.w3, env.auction, aid, signer=impostor)
    assert env.w3.eth.block_number == before  # nothing signed or sent


def test_select_winner_and_fund_tampered_signature_rejected(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    preview = auction_bridge.select_winner_and_fund(
        env.w3, env.auction, aid, signer=env.poster_acct, dry_run=True)
    signed = env.poster_acct.sign_transaction(preview["unsigned_tx"])
    raw = bytearray(getattr(signed, "raw_transaction", None) or signed.rawTransaction)
    raw[-1] ^= 0x01  # corrupt the signature
    with pytest.raises(Exception):
        env.w3.eth.send_raw_transaction(bytes(raw))


def test_select_winner_and_fund_funding_mismatch_fail_closed(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    _, price = env.auction.functions.vickreyResult(aid).call()
    with pytest.raises(BridgeError, match="funding mismatch"):
        auction_bridge.select_winner_and_fund(
            env.w3, env.auction, aid, signer=env.poster_acct,
            value_wei=int(price) - 1, dry_run=True)


def test_select_winner_and_fund_credit_overdraw_fail_closed(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    # Pool 2 is empty for this poster: any nonzero credit must fail pre-sign.
    with pytest.raises(BridgeError, match="Pool 2"):
        auction_bridge.select_winner_and_fund(
            env.w3, env.auction, aid, signer=env.poster_acct,
            credit_to_apply_wei=ONE_ETH, escrow=env.escrow, dry_run=True)


def test_select_winner_and_fund_rejects_uint96_overflow(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    with pytest.raises(BridgeError, match="uint96"):
        auction_bridge.select_winner_and_fund(
            env.w3, env.auction, aid, signer=env.poster_acct,
            credit_to_apply_wei=UINT96_MAX + 1, dry_run=True)


# ---------------------------------------------------------------------------
# initialize_escrow
# ---------------------------------------------------------------------------

def test_initialize_escrow_dry_run_then_live_flow(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    params = _escrow_params(env, aid)

    # Pre-selection: dry-run the exact call the core will make — succeeds,
    # broadcasts nothing.
    _fund_core(env, params["bid_amount_wei"])
    sim = auction_bridge.initialize_escrow(
        env.w3, env.escrow, auction_core=env.auction.address, **params)
    assert sim["dry_run"] is True
    assert sim["data"].startswith("0x")
    assert sim["value_wei"] + sim["credit_to_apply_wei"] == sim["bid_amount_wei"]

    # Live selection initializes the escrow atomically...
    auction_bridge.select_winner_and_fund(
        env.w3, env.auction, aid, signer=env.poster_acct)

    # ...so the same dry-run now reverts (already initialized).
    with pytest.raises(BridgeSimulationError):
        auction_bridge.initialize_escrow(
            env.w3, env.escrow, auction_core=env.auction.address, **params)


def test_initialize_escrow_non_core_sender_reverts(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    params = _escrow_params(env, aid)
    intruder = env.w3.eth.account.create()  # ephemeral
    env.w3.eth.send_transaction({
        "from": env.deployer, "to": intruder.address,
        "value": env.w3.to_wei(10, "ether"), **TX})
    _fund_core(env, params["bid_amount_wei"])
    # Dry-run from the core passes, but broadcasting from a non-core account
    # reverts onchain (onlyAuctionCore) — the bridge surfaces it.
    with pytest.raises(BridgeError, match="estimation failed"):
        auction_bridge.initialize_escrow(
            env.w3, env.escrow, auction_core=env.auction.address,
            signer=intruder, dry_run=False, **params)


def test_initialize_escrow_rejects_bad_inputs(env):
    aid = env.run_auction([(env.bidders[0], ONE_ETH), (env.bidders[1], 2 * ONE_ETH)])
    params = _escrow_params(env, aid)
    base = dict(params, auction_core=env.auction.address)
    with pytest.raises(BridgeError, match="positive"):
        auction_bridge.initialize_escrow(env.w3, env.escrow, **{**base, "bid_amount_wei": 0})
    with pytest.raises(BridgeError, match="nonzero"):
        auction_bridge.initialize_escrow(
            env.w3, env.escrow, **{**base, "selected_agent": "0x" + "00" * 20})
    with pytest.raises(BridgeError, match="uint96"):
        auction_bridge.initialize_escrow(
            env.w3, env.escrow, **{**base, "bid_amount_wei": UINT96_MAX + 1})
    with pytest.raises(BridgeError, match="32-byte"):
        auction_bridge.initialize_escrow(env.w3, env.escrow, **{**base, "auction_id": "0x1234"})


# ---------------------------------------------------------------------------
# contract-level guard: invalid reveal price
# ---------------------------------------------------------------------------

def test_reveal_price_above_uint96_max_reverts(env):
    aid = env.open()
    bidder = env.bidders[0]
    salt = env.w3.keccak(text="oversize-salt")
    agent_id = env.w3.keccak(text="oversize-agent")
    too_big = UINT96_MAX + 1
    commitment = env.w3.solidity_keccak(
        ["bytes32", "uint256", "uint256", "bytes32", "bytes32"],
        [aid, env.chain_id, too_big, salt, agent_id])
    env.auction.functions.commit(aid, commitment).transact({"from": bidder, **TX})
    a = env.auction.functions.auctions(aid).call()
    env.travel_past(a[3])
    with pytest.raises(TransactionFailed):
        env.auction.functions.reveal(aid, too_big, salt, agent_id).transact(
            {"from": bidder, **TX})
