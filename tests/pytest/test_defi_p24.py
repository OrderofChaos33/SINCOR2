"""P24 acceptance tests: fee split, bonding curve, vesting, accrual, policy.

Maps to the numbered acceptance criteria in
~/workspace/sincor2-auction-tasks/specs/p24-socialfi.md.
Self-contained: Python mirror of the onchain semantics (the Solidity under
onchain/src/p24/ compiles under solc 0.8.24; fork runs are a documented gap).
"""

from __future__ import annotations

import random
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.p24 import (
    PARAMS, accrual, curve, factory, fees, live_block, onboarding, policy,
    primitives, split,
)
from src.sincor2.defi.p24.factory import CreatorTokenFactory, FactoryError
from src.sincor2.defi.p24.live_block import LiveBlocked, guard_live
from src.sincor2.defi.p24.policy import PolicyViolation
from src.sincor2.defi.p24.primitives import (
    CollateralAdapter, CreatorStaking, PrimitiveError, VenueSleeve,
)
from src.sincor2.defi.catalog import TREASURY

R = random.Random(20260929)


# -- AC1: split conservation ------------------------------------------------------
def test_ac1_split_sums_exactly_with_dust_to_treasury():
    for fee in (0, 1, 2, 3, 999, 10**6, 10**12, 10**18, 10**24):
        legs = split.split_fee(fee)
        assert legs.creator_wei + legs.platform_wei + legs.treasury_wei == fee
        assert legs.creator_wei == fee * 4940 // 10_000
        assert legs.platform_wei == fee * 4940 // 10_000
        dust = fee - (legs.creator_wei + legs.platform_wei
                      + fee * 120 // 10_000)
        assert legs.treasury_wei == fee * 120 // 10_000 + dust
        assert legs.dust_wei == dust
        assert legs.treasury_to == TREASURY


def test_ac1_creator_floor_4000_bps_rejects_dilution():
    with pytest.raises(split.SplitParamError, match="4000"):
        split.validate_split_params(3999, 4961, 1040)
    split.validate_split_params(4940, 4940, 120)  # canonical passes


def test_ac1_fuzz_1000_fee_values():
    for _ in range(1000):
        fee = R.randint(0, 10**24)
        legs = split.split_fee(fee)
        assert legs.creator_wei + legs.platform_wei + legs.treasury_wei == fee
        assert legs.creator_wei >= fee * 4000 // 10_000 or fee == 0
        assert legs.treasury_wei >= fee * 120 // 10_000  # dust never leaves


# -- AC2: bonding curve --------------------------------------------------------------
def test_ac2_pricing_formulas():
    assert curve.buy_price_wei(0) == 0
    assert curve.buy_price_wei(100) == 100 * 100 * 10**6 // 16_000
    assert curve.sell_price_wei(1) == 0  # (1-1)^2
    assert curve.sell_price_wei(100) == 99 * 99 * 10**6 // 16_000


def test_ac2_graduation_at_69k_is_one_way():
    st = curve.CurveState()
    bought = 0
    while not st.closed:
        bought += 1
        st.buy(1)
        assert bought < 10**6  # sanity: it must close
    assert st.supply_tokens == 1034  # first supply with mcap >= $69,000
    assert curve.market_cap_usd_wei(1034) >= 69_000 * 10**6
    assert curve.market_cap_usd_wei(1033) < 69_000 * 10**6
    assert curve.graduated(1034) and not curve.graduated(1033)
    with pytest.raises(ValueError, match="closed"):
        st.buy(1)
    with pytest.raises(ValueError, match="closed"):
        st.sell(1)


def test_ac2_sells_bounded_by_supply():
    st = curve.CurveState()
    st.buy(10)
    with pytest.raises(ValueError, match="invalid"):
        st.sell(11)
    proceeds = st.sell(10)
    assert st.supply_tokens == 0
    assert proceeds > 0
    # instantaneous spread: buying at supply s costs more than selling at s
    assert curve.buy_price_wei(100) > curve.sell_price_wei(100)
    assert curve.buy_price_wei(100) == curve.sell_price_wei(101)


def test_ac2_buy_cost_is_exact_marginal_sum():
    st = curve.CurveState()
    cost = st.buy(5)
    assert cost == sum(curve.buy_price_wei(i) for i in range(5))


# -- AC3: vesting + accrual + staking + collateral -----------------------------------------
def test_ac3_supply_fixed_at_1e9_and_fifty_fifty():
    assert factory.TOTAL_SUPPLY_WEI == 1_000_000_000 * 10**18
    assert factory.TOTAL_SUPPLY_WEI == (factory.CURVE_SUPPLY_WEI
                                       + factory.VESTING_SUPPLY_WEI)
    assert factory.CURVE_SUPPLY_WEI == factory.VESTING_SUPPLY_WEI


def test_ac3_five_year_linear_vesting():
    fac = CreatorTokenFactory()
    token = fac.issue("Sunset Club", "SUN", "alice", "1.0.0",
                      screened=True, now=0.0)
    assert factory.vested_amount_wei(token, 0.0) == 0
    half = factory.VESTING_SECONDS // 2
    assert factory.vested_amount_wei(token, half) == token.vesting_supply_wei // 2
    assert factory.vested_amount_wei(token, factory.VESTING_SECONDS) == token.vesting_supply_wei
    assert factory.vested_amount_wei(token, factory.VESTING_SECONDS + 10**9) == token.vesting_supply_wei
    for t in (1, 10**6, 10**7, 10**8):
        assert factory.vested_amount_wei(token, t) == (
            token.vesting_supply_wei * t // factory.VESTING_SECONDS)


def test_ac3_unvested_creator_tokens_cannot_move():
    fac = CreatorTokenFactory()
    fac.issue("Sunset Club", "SUN", "alice", "1.0.0", screened=True, now=0.0)
    with pytest.raises(FactoryError, match="unvested"):
        fac.transfer_creator_tokens("SUN", 1, now=0.0)
    fac.transfer_creator_tokens("SUN", factory.VESTING_SUPPLY_WEI,
                                now=factory.VESTING_SECONDS)
    assert fac.tokens["SUN"].transferred_wei == factory.VESTING_SUPPLY_WEI
    # one wei more than vested: refused
    with pytest.raises(FactoryError, match="unvested"):
        fac.transfer_creator_tokens("SUN", 1, now=factory.VESTING_SECONDS)


def test_ac3_epoch_snapshot_gaming_blocked():
    acc = accrual.RevenueAccrual()
    acc.set_balance("TKN", "alice", 1_000)
    epoch = acc.snapshot_epoch("TKN", now=0.0)
    # late joiner after the snapshot cannot claim this epoch
    acc.set_balance("TKN", "mallory", 9_000)
    epoch2 = acc.snapshot_epoch("TKN", now=3_000.0)
    assert epoch2.index == epoch.index == 0
    acc.accrue_revenue("TKN", 1_000_000, now=4_000.0)
    assert acc.claimable("TKN", "alice", 0) == 1_000_000  # full: only snapshot
    assert acc.claimable("TKN", "mallory", 0) == 0
    assert acc.claim("TKN", "alice", 0) == 1_000_000
    assert acc.claim("TKN", "alice", 0) == 0  # double-claim returns 0


def test_ac3_72h_unbonding_enforced():
    st = CreatorStaking()
    st.stake("alice", 500, now=0.0)
    with pytest.raises(PrimitiveError, match="unbond not requested"):
        st.withdraw("alice", now=3_600.0)
    st.request_unbond("alice", now=10_000.0)
    with pytest.raises(PrimitiveError, match="unbonding"):
        st.withdraw("alice", now=10_000.0 + 72 * 3600 - 1)
    assert st.withdraw("alice", now=10_000.0 + 72 * 3600) == 500


def test_ac3_collateral_caps_enforced():
    adapter = CollateralAdapter(venues=[VenueSleeve("v1", 1_000_000.0)])
    # 10% per-token sleeve cap of $1M = $100k collateral -> 50% LTV = $50k
    assert adapter.max_borrow_usd("TKN", 1_000_000.0, "v1") == 50_000.0
    assert adapter.lock("TKN", 1_000_000.0, "v1") == 50_000.0
    # cap exhausted: no more headroom for this token
    assert adapter.max_borrow_usd("TKN", 1_000_000.0, "v1") == 0.0
    with pytest.raises(PrimitiveError, match="headroom"):
        adapter.lock("TKN", 1_000_000.0, "v1")
    with pytest.raises(PrimitiveError, match="allowlisted"):
        adapter.max_borrow_usd("TKN", 100.0, "nope")


def test_ac3_staking_rewards_pro_rata():
    st = CreatorStaking()
    st.stake("a", 100, now=0.0)
    st.stake("b", 300, now=1.0)
    st.fund_rewards(400)
    assert st.claim_rewards("a") == 100
    assert st.claim_rewards("b") == 300


def test_ac3_staking_rewards_claim_order_independent():
    # Adversarial: two identical worlds, opposite claim orders. Accumulator
    # accounting must give identical payouts and distribute the full pool —
    # no claim-order race, nothing stranded.
    def world():
        st = CreatorStaking()
        st.stake("a", 100, now=0.0)
        st.stake("b", 300, now=1.0)
        st.fund_rewards(400)
        return st
    s1 = world()
    ra1, rb1 = s1.claim_rewards("a"), s1.claim_rewards("b")
    s2 = world()
    rb2, ra2 = s2.claim_rewards("b"), s2.claim_rewards("a")
    assert (ra1, rb1) == (ra2, rb2) == (100, 300)
    assert ra1 + rb1 == 400  # full pool distributed
    assert s1.claim_rewards("a") == 0 and s1.claim_rewards("b") == 0


# -- AC4: content policy -----------------------------------------------------------------
_ADVERSARIAL = [
    "This token offers guaranteed returns",
    "100x gem incoming",
    "We are going to the moon",
    "It will moon soon",
    "Price prediction: up only",
    "Get rich quick with this drop",
    "Double your money this week",
    "This is a pump and dump scheme",
    "You can't lose on this",
    "A moonshot guaranteed for holders",
    "Triple your money by Friday",
    "Price will double after listing",
]


def test_ac4_adversarial_price_talk_all_blocked():
    for text in _ADVERSARIAL:
        result = policy.screen_text(text, field="description")
        assert result.ok is False, f"allowed: {text!r}"
        assert result.ruleset_version == policy.RULESET_VERSION
        assert result.matched_phrase is not None
        # the full rejected text is never stored in the result
        assert text not in repr(result)


def test_ac4_legitimate_content_passes():
    legit = [
        "GM everyone, what did you build today?",
        "The new trait drop looks amazing, love the art direction",
        "Reminder: community call tomorrow at noon UTC",
        "Just posted the lore chapter for the next collection",
        "Who is going to the meetup next week?",
        "Priceless work from the artist",  # word-boundary: not "price ..."
    ]
    for text in legit:
        assert policy.screen_text(text, field="bio").ok is True, text


def test_ac4_rejection_cites_version_and_phrase():
    with pytest.raises(PolicyViolation) as exc:
        policy.require_clean("Moon Fund", "MOON", "guaranteed 10x", "bio")
    assert exc.value.ruleset_version == "1.0.0"
    assert exc.value.matched_phrase == "guaranteed 10x"
    assert str(exc.value).startswith("no_price_talk")


def test_ac4_word_boundaries_not_substrings():
    # "moonshot" alone is not a deny phrase; only "moonshot guaranteed" is.
    assert policy.screen_text("the moonshot was amazing", field="bio").ok is True
    # "100x" inside a longer token is not a word-boundary match
    assert policy.screen_text("model 100xgem v2", field="name").ok is True


def test_ac4_fuzz_1000_matcher_mutations_still_blocked():
    phrases = ["guaranteed returns", "to the moon", "100x", "price prediction",
               "get rich quick", "pump and dump"]
    for _ in range(1000):
        phrase = R.choice(phrases)
        words = phrase.split()
        # adversarial casing / spacing, phrase word order preserved
        # (the matcher flexes inner whitespace; punctuation between words
        # is a genuinely different string and not claimed to match)
        variant = "".join(
            (w.upper() if R.random() < 0.5 else w)
            + R.choice([" ", "  ", "\t", " \n "])
            for w in words
        ).strip()
        text = f"{R.choice(['Hey', 'Wow', 'Look'])} {variant} {R.choice(['today', 'now'])}"
        result = policy.screen_text(text, field="description")
        assert result.ok is False, text


# -- AC5: fee math is wei-exact -----------------------------------------------------------------
def test_ac5_fee_wei_exact_worked_example():
    # docs/spec/P24_ECONOMICS.md: creator token doing $500k/yr volume ->
    # $50,000 in trade fees -> treasury leg $600.00 exactly.
    legs = split.split_fee(50_000 * 10**6)
    assert legs.treasury_wei == 600_000_000
    assert legs.creator_wei == 24_700_000_000
    assert legs.platform_wei == 24_700_000_000
    assert legs.dust_wei == 0


def test_ac5_treasury_address_is_canonical():
    assert TREASURY == "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
    assert fees.TREASURY == TREASURY


def test_ac5_settlement_only_to_treasury_above_10():
    book = fees.TreasuryFeeBook()
    rec = book.accrue("TKN", 5_000_000)  # $5 in fees -> treasury $0.06
    assert rec.treasury_wei == 5_000_000 * 120 // 10_000 == 60_000
    assert book.settle("TKN") is None  # below $10 batch floor
    book.accrue("TKN", 10**12)  # +$1,000,000 fees -> treasury $12,000
    s = book.settle("TKN")
    assert s is not None and s.to == TREASURY
    assert s.amount_usdc_wei == 60_000 + 12_000_000_000


# -- AC6: live-blocked is provable ---------------------------------------------------------------------
def test_ac6_every_live_path_raises():
    with pytest.raises(LiveBlocked):
        guard_live("anything")
    agent = onboarding.OnboardingAgent()
    with pytest.raises(LiveBlocked):
        agent.sign_live({"kind": "issue_token"})
    with pytest.raises(FactoryError, match="screen"):
        factory.CreatorTokenFactory().issue("n", "S", "c", "v",
                                            screened=False, now=0.0)
    assert live_block.LIVE_BLOCKED is True
    assert PARAMS["live_blocked"] is True


def test_ac6_onboarding_screens_before_issuance():
    agent = onboarding.OnboardingAgent()
    reg = agent.register("alice", "Sunset Club", "SUN",
                         "a community art project", "creator bio", now=0.0)
    assert reg.policy_version == "1.0.0"
    assert reg.token.symbol == "SUN"
    with pytest.raises(PolicyViolation):
        agent.register("mallory", "Moon Fund", "MOON",
                       "guaranteed 10x, buy now", "bio", now=0.0)
    assert len(agent.rejections) == 1
    rej = agent.rejections[0]
    assert rej.matched_phrase == "guaranteed 10x"
    assert rej.ruleset_version == "1.0.0"
    # the submitted text is deliberately not stored
    assert "guaranteed 10x, buy now" not in repr(rej)
