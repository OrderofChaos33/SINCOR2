"""Contract-Net: cosine invite, Vickrey second-price, ε-greedy juniors.

Isolated unit tests — they do not import BiddingEngine.run_auction so the
first-price path stays untouched.
"""

from __future__ import annotations

import random
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if "marketplace" not in sys.modules:
    pkg = types.ModuleType("marketplace")
    pkg.__path__ = [str(ROOT / "marketplace")]
    sys.modules["marketplace"] = pkg
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marketplace.contract_net.eip712 import (  # noqa: E402
    demo_signing_secret,
    sign_hmac,
    typed_data_digest,
    verify_hmac,
)
from marketplace.contract_net.engine import ContractNetEngine  # noqa: E402
from marketplace.contract_net.filter import filter_swarm  # noqa: E402
from marketplace.contract_net.keccak import keccak256, keccak256_hex  # noqa: E402
from marketplace.contract_net.roster import demo_roster, demo_tasks  # noqa: E402
from marketplace.contract_net.types import ContractNetConfig, TaskSpec  # noqa: E402
from marketplace.contract_net.vectors import cosine_similarity, embed_tokens  # noqa: E402
from marketplace.contract_net.vickrey import clear_vickrey, sort_valid_bids  # noqa: E402
from marketplace.contract_net.types import Invite, SealedBid  # noqa: E402


EMPTY_KECCAK = "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def test_keccak_empty_and_abc():
    assert keccak256(b"").hex() == EMPTY_KECCAK
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    assert keccak256_hex(b"").startswith("0x")


def test_embed_identical_tokens_cosine_one():
    left = embed_tokens(("research", "scrape", "analyze"))
    right = embed_tokens(("research", "scrape", "analyze"))
    assert cosine_similarity(left, right) == pytest.approx(1.0, abs=1e-9)


def test_embed_disjoint_tokens_lower_cosine():
    close = cosine_similarity(
        embed_tokens(("research", "scrape", "analyze")),
        embed_tokens(("research", "analyze")),
    )
    far = cosine_similarity(
        embed_tokens(("research", "scrape", "analyze")),
        embed_tokens(("backup", "label", "maintain")),
    )
    assert close > far


def test_invite_k_is_between_three_and_five():
    roster = demo_roster()
    task = demo_tasks()[0]
    config = ContractNetConfig(invite_k=4)
    result = filter_swarm(task, roster, config, rng=random.Random(1), force_junior=False)
    assert 3 <= len(result.invited) <= 5
    assert result.tokens_saved == (len(roster) - len(result.invited)) * config.eval_tokens_per_bid
    assert result.llm_calls_avoided == len(roster) - len(result.invited)


def test_invite_k_rejects_out_of_band():
    with pytest.raises(ValueError):
        ContractNetConfig(invite_k=2)
    with pytest.raises(ValueError):
        ContractNetConfig(epsilon=0.2)


def test_vickrey_lowest_wins_paid_second():
    engine = ContractNetEngine(ContractNetConfig(invite_k=4, epsilon=0.12))
    roster = demo_roster()
    task = demo_tasks()[1]  # develop / test / deploy
    award = engine.run(task, roster, seed=11, force_junior=False)
    valid = [bid for bid in award.bids if bid.valid]
    ordered = sorted(valid, key=lambda bid: (bid.price, bid.agent_id))
    assert award.winner_id == ordered[0].agent_id
    assert award.winner_bid_price == ordered[0].price
    if len(ordered) > 1:
        assert award.clearing_price == ordered[1].price
        assert award.clearing_price >= award.winner_bid_price
        assert award.savings_vs_first_price == ordered[1].price - ordered[0].price
    assert len(award.invites) <= 5
    assert all(bid.agent_id in {row.agent_id for row in award.invites} for bid in award.bids)


def test_hmac_signature_roundtrip():
    config = ContractNetConfig()
    digest = typed_data_digest(
        config,
        auction_id="0x" + "ab" * 32,
        task_id="cn-test",
        agent="0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
        price=1_000_000,
        estimated_tokens=400,
        nonce=1,
        deadline=1_700_000_000,
    )
    secret = demo_signing_secret("atlas-scout")
    sig = sign_hmac(digest, secret)
    assert verify_hmac(digest, sig, secret)
    assert not verify_hmac(digest, sig, demo_signing_secret("other"))


def test_bad_signature_is_rejected():
    engine = ContractNetEngine()
    roster = demo_roster()
    task = demo_tasks()[0]
    award = engine.run(task, roster, seed=3, force_junior=False)
    bid = award.bids[0]
    bid.signature = "0x" + "00" * 32
    bid.valid = True
    bid.reject_reason = ""
    engine._validate_bid(bid, task, now=bid.submitted_at, signing_secret=demo_signing_secret(bid.agent_id))
    assert bid.valid is False
    assert bid.reject_reason == "bad_signature"


def test_over_max_price_rejected():
    engine = ContractNetEngine()
    roster = demo_roster()
    task = TaskSpec(
        task_id="tiny-budget",
        goal="Cheap scrape",
        requirements=("scrape",),
        budget_tokens=100,
        max_price=100,
    )
    award = engine.run(task, roster, seed=2, force_junior=False)
    assert award.phase == "failed" or all(bid.price <= 100 or not bid.valid for bid in award.bids)


def test_uninvited_bid_rejected():
    invites = [
        Invite(
            agent_id="atlas-scout",
            name="Atlas Scout",
            cosine=0.9,
            junior=False,
            llm_invited=True,
            reason="top-k",
        )
    ]
    outsider = SealedBid(
        auction_id="0x" + "11" * 32,
        task_id="t",
        agent_id="forge-builder",
        agent_wallet="0x" + "22" * 20,
        price=1,
        estimated_tokens=1,
        nonce=1,
        deadline=9_999_999_999,
        digest="0x" + "33" * 32,
        signature="0x" + "44" * 32,
        sig_type="eip712-hmac-sha256",
    )
    award = clear_vickrey(
        auction_id=outsider.auction_id,
        task_id="t",
        bids=[outsider],
        invites=invites,
        junior_reserved=False,
        llm_calls_avoided=0,
        tokens_saved=0,
    )
    assert award.phase == "failed"
    assert award.bids[0].reject_reason == "not_invited"


def test_junior_reserved_pool_excludes_incumbents():
    roster = demo_roster()
    task = demo_tasks()[0]
    engine = ContractNetEngine()
    award = engine.run(task, roster, seed=0, force_junior=True)
    assert award.junior_reserved is True
    assert all(invite.junior for invite in award.invites)
    if award.winner_id:
        assert award.junior_winner is True


def test_junior_fallback_when_no_juniors():
    incumbents = [agent for agent in demo_roster() if agent.tasks_completed >= 3]
    task = demo_tasks()[0]
    result = filter_swarm(
        task,
        incumbents,
        ContractNetConfig(),
        rng=random.Random(0),
        force_junior=True,
    )
    assert result.junior_reserved is False
    assert result.invited
    assert not all(invite.junior for invite in result.invited)


def test_epsilon_band_over_many_auctions():
    engine = ContractNetEngine(ContractNetConfig(epsilon=0.12, invite_k=4))
    roster = demo_roster()
    tasks = demo_tasks()
    awards = engine.run_many(tasks, roster, rounds=80, seed=21)
    reserved = sum(1 for award in awards if award.junior_reserved)
    rate = reserved / len(awards)
    assert 0.04 <= rate <= 0.22  # binomial noise around 0.12
    stats = engine.stats()
    assert stats["tokens_saved"] > 0
    assert stats["auctions"] == 80


def test_shading_cannot_steal_vickrey_win():
    """Bidding above true min cannot improve the payoff versus truthful bidding."""
    roster = demo_roster()
    task = demo_tasks()[4]
    honest = ContractNetEngine()
    shaded = ContractNetEngine()
    honest_award = honest.run(task, roster, seed=5, force_junior=False)
    winner = honest_award.winner_id
    assert winner
    # Winner shades up 25% — still wins (Vickrey) or loses; never paid more profitably
    shade_award = shaded.run(
        task, roster, seed=5, force_junior=False, shade={winner: 1.25}
    )
    honest_profit = honest_award.clearing_price - honest_award.winner_bid_price
    if shade_award.winner_id == winner:
        shade_profit = shade_award.clearing_price - shade_award.winner_bid_price
        assert shade_profit <= honest_profit + 1  # integer micro-AXM
    # If they lose, profit is zero which is worse.


def test_single_valid_bid_paid_own_price():
    bid = SealedBid(
        auction_id="0x" + "aa" * 32,
        task_id="solo",
        agent_id="moss-caretaker",
        agent_wallet="0x" + "bb" * 20,
        price=700_000,
        estimated_tokens=280,
        nonce=1,
        deadline=9_999_999_999,
        digest="0x" + "cc" * 32,
        signature="0x" + "dd" * 32,
        sig_type="eip712-hmac-sha256",
    )
    invite = Invite(
        agent_id="moss-caretaker",
        name="Moss",
        cosine=0.5,
        junior=True,
        llm_invited=True,
        reason="solo",
    )
    award = clear_vickrey(
        auction_id=bid.auction_id,
        task_id="solo",
        bids=[bid],
        invites=[invite],
        junior_reserved=True,
        llm_calls_avoided=12,
        tokens_saved=9600,
    )
    assert award.winner_id == "moss-caretaker"
    assert award.clearing_price == 700_000
    assert award.savings_vs_first_price == 0


def test_sort_valid_bids_tie_breaks_on_agent_id():
    def _bid(agent_id: str) -> SealedBid:
        return SealedBid(
            auction_id="0x" + "11" * 32,
            task_id="t",
            agent_id=agent_id,
            agent_wallet="0x" + "22" * 20,
            price=1_000_000,
            estimated_tokens=1,
            nonce=1,
            deadline=1,
            digest="0x00",
            signature="0x00",
            sig_type="eip712-hmac-sha256",
        )

    ordered = sort_valid_bids([_bid("zeta"), _bid("alpha"), _bid("mu")])
    assert [bid.agent_id for bid in ordered] == ["alpha", "mu", "zeta"]


def test_research_task_prefers_research_agents():
    roster = demo_roster()
    task = demo_tasks()[0]
    result = filter_swarm(task, roster, ContractNetConfig(), rng=random.Random(9), force_junior=False)
    invited_ids = {row.agent_id for row in result.invited}
    assert invited_ids & {"atlas-scout", "helix-synth", "spark-scout", "wick-analyst"}
    assert "moss-caretaker" not in invited_ids
