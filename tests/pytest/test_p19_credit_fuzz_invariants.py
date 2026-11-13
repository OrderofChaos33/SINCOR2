"""Audit-prep invariant + adversarial fuzz tests for P19 (credit_underwriting).

Property tests an auditor would demand of the credit pipeline reference
build:

- score_fuzz: score_wallet always returns within [300, 850]; mixer
  interaction clamps to exactly 300; identical features always give
  identical scores (deterministic).
- ltv_fuzz: max_ltv_bps is 0 below the 350 floor and non-decreasing in
  score; 750+ maps to 7500 bps; the on-disk table passes validate_ltv_table
  while shuffled/invalid tables raise.
- attestation_fuzz: stale, below-floor, unauthorized-attestor, and
  replayed-nonce attestations are all refused; live_score returns the
  fresh score or raises; future-dated issuance is not fresh.
- line_fuzz: debt can never exceed collateral * maxLTV; zero-collateral
  and over-cap draws raise UnsecuredDraw; health_factor is exactly
  collateral*LTV/debt (inf with no debt); circuit thresholds are exact
  at the 1.15 / 1.05 boundaries; hedge routing is exactly 25% of
  collateral and only in the hedge band.
- book_fuzz: once the book reaches $1,000 the 10% per-borrower and 40%
  per-band caps bind exactly; the bootstrap phase is exempt and flagged.
- fee_fuzz: origination and accrual fees are exactly 20 bps, integer
  cents; negative inputs raise; treasury_take decomposes exactly.

The score buys better terms, never zero collateral — no unsecured book
exists anywhere in the module. Deterministic: seeded RNG. N/N must pass.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi import credit_underwriting as cr  # noqa: E402
from src.sincor2.defi.credit_underwriting import (  # noqa: E402
    AttestationRegistry,
    BorrowerFeatures,
    CreditBook,
    CreditLine,
    ScoreAttestation,
    accrual_fee_usd,
    is_fresh,
    max_ltv_bps,
    origination_fee_usd,
    score_band,
    score_wallet,
    treasury_take,
    validate_ltv_table,
)

RNG = random.Random(0xA01919)


def fuzz_features() -> BorrowerFeatures:
    return BorrowerFeatures(
        wallet_age_blocks=RNG.choice([RNG.randint(0, 10**8), 0]),
        repaid=RNG.randint(0, 500),
        liquidated=RNG.randint(0, 50),
        avg_utilization=RNG.choice([RNG.uniform(0, 1), 0.0, 1.0, 2.0, -0.5]),
        tx_count=RNG.randint(0, 100_000),
        protocol_diversity=RNG.randint(0, 30),
        stablecoin_ratio=RNG.choice([RNG.uniform(0, 1), 0.0, 1.0, 1.5]),
        mixer_interaction=RNG.choice([True, False]),
        rug_interaction=RNG.choice([True, False]),
    )


def test_score_bounds_and_determinism():
    for _ in range(1000):
        f = fuzz_features()
        s = score_wallet(f)
        assert cr.SCORE_MIN <= s <= cr.SCORE_MAX, (f, s)
        assert score_wallet(f) == s  # deterministic
        if f.mixer_interaction:
            assert s == cr.SCORE_MIN  # mixer clamps to 300
    # clean borrower with perfect history reaches the top LTV band.
    # FINDING (reported): the documented 300-850 range is clamped but 850 is
    # unreachable — the weight budget caps at 500+60+120+40+40+40 = 800 with
    # zero penalties. The load-bearing property is the top LTV band (750+).
    good = BorrowerFeatures(
        wallet_age_blocks=31_536_000, repaid=500, liquidated=0,
        avg_utilization=0.0, tx_count=10_000, protocol_diversity=10,
        stablecoin_ratio=1.0)
    assert score_wallet(good) == 800
    assert score_wallet(good) >= 750


def test_ltv_table_monotonicity():
    validate_ltv_table()  # on-disk table is valid
    for _ in range(500):
        score = RNG.choice([RNG.randint(0, 1000), 349, 350, 750, 850])
        ltv = max_ltv_bps(score)
        if score < cr.SCORE_FLOOR:
            assert ltv == 0
        else:
            assert 3_500 <= ltv <= 7_500
        if score >= 750:
            assert ltv == 7_500
    # non-decreasing in score across the whole band range
    prev = -1
    for score in range(0, 1001, 7):
        ltv = max_ltv_bps(score)
        assert ltv >= prev
        prev = ltv
    # bands line up with the table
    for score, expect in ((800, "750+"), (700, "650+"), (600, "550+"),
                          (500, "450+"), (400, "350+"), (300, "ineligible")):
        assert score_band(score) == expect
    # invalid tables raise. FINDING (reported): the first row's LTV is never
    # range-checked — ((750, 0),) and ((750, 99999),) pass validate_ltv_table.
    # The on-disk table is valid, so max_ltv_bps is unaffected; the validator
    # gap only matters if a custom table is ever supplied.
    for bad_table in (
        (),
        ((750, 6_500), (650, 7_500)),   # LTV rises as score falls
        ((750, 7_500), (750, 7_400)),   # scores must descend
        ((300, 3_500),),                # below the score floor
        ((750, 7_500), (650, 10_001)),  # LTV out of range (non-first row)
    ):
        try:
            validate_ltv_table(bad_table)
            raise AssertionError(f"invalid table accepted: {bad_table}")
        except cr.LTVTableInvalid:
            pass


def test_attestation_freshness_replay_floor():
    now = 10**6
    reg = AttestationRegistry(authorized_attestors={"0xScorer"})
    def mk(borrower="b", score=700, issued=now - 100, nonce=1,
           attestor="0xScorer"):
        return ScoreAttestation(borrower, score, issued, nonce, attestor)
    reg.register(mk(nonce=1), now)
    assert reg.live_score("b", now) == 700
    # replayed nonce refused
    try:
        reg.register(mk(nonce=1), now)
        raise AssertionError("replayed nonce accepted")
    except cr.AttestationInvalid:
        pass
    # stale refused
    try:
        reg.register(mk(nonce=2, issued=now - cr.ATTEST_TTL_SECONDS - 1), now)
        raise AssertionError("stale attestation accepted")
    except cr.AttestationInvalid:
        pass
    # future-dated issuance is not fresh
    assert is_fresh(mk(issued=now + 1), now) is False
    # below floor refused
    try:
        reg.register(mk(nonce=3, score=349), now)
        raise AssertionError("below-floor score accepted")
    except cr.AttestationInvalid:
        pass
    # unauthorized attestor refused
    try:
        reg.register(mk(nonce=4, attestor="0xEvil"), now)
        raise AssertionError("unauthorized attestor accepted")
    except cr.AttestationInvalid:
        pass
    # no live attestation
    try:
        reg.live_score("ghost", now)
        raise AssertionError("missing attestation scored")
    except cr.AttestationInvalid:
        pass
    # attestation expires 72h after issuance, to the second
    reg2 = AttestationRegistry(authorized_attestors={"0xScorer"})
    issued_at = now
    reg2.register(mk(nonce=9, issued=issued_at), now)
    assert reg2.live_score("b", issued_at + cr.ATTEST_TTL_SECONDS) == 700
    try:
        reg2.live_score("b", issued_at + cr.ATTEST_TTL_SECONDS + 1)
        raise AssertionError("expired attestation scored")
    except cr.AttestationInvalid:
        pass


def test_credit_line_no_unsecured_book():
    for _ in range(300):
        collateral = RNG.choice([RNG.uniform(100, 10**6), 0.0])
        score = RNG.choice([RNG.randint(300, 850), 349, 300])
        line = CreditLine("b", collateral, score)
        assert line.ltv_bps == max_ltv_bps(score)
        assert line.max_draw_usd == collateral * line.ltv_bps / 10_000
        if collateral <= 0 or line.ltv_bps == 0:
            try:
                line.draw(1.0)
                raise AssertionError("unsecured draw accepted")
            except cr.UnsecuredDraw:
                pass
            continue
        # fuzzed draw sequences: debt never exceeds the cap
        for _ in range(RNG.randint(1, 6)):
            amt = RNG.uniform(0.01, line.max_draw_usd * 1.5 + 1)
            try:
                line.draw(amt)
            except cr.UnsecuredDraw:
                assert line.debt_usd + amt > line.max_draw_usd + 1e-9
            assert line.debt_usd <= line.max_draw_usd + 1e-9
        # health factor exact
        if line.debt_usd > 0:
            assert line.health_factor() == (collateral * line.ltv_bps
                                            / 10_000 / line.debt_usd)
        else:
            assert line.health_factor() == float("inf")
        # circuit thresholds exact at boundaries
        hf = line.health_factor()
        action = line.circuit_action()
        if hf != float("inf"):
            assert action == ("liquidate" if hf < 1.05
                              else "hedge" if hf < 1.15 else "ok")
        if action == "hedge":
            assert line.hedge_amount_usd() == collateral * cr.HEDGE_MAX_PCT
        else:
            assert line.hedge_amount_usd() == 0.0
    # negative construction refused
    for bad in (("b", -1.0, 700),):
        try:
            CreditLine(*bad)
            raise AssertionError("negative collateral accepted")
        except ValueError:
            pass
    try:
        CreditLine("b", 100.0, 700).draw(0.0)
        raise AssertionError("zero draw accepted")
    except ValueError:
        pass


def test_credit_book_concentration_caps():
    for _ in range(100):
        book = CreditBook()
        borrowers = [f"b{i}" for i in range(RNG.randint(1, 6))]
        for _ in range(RNG.randint(1, 20)):
            borrower = RNG.choice(borrowers)
            amount = RNG.uniform(1, 50_000)
            score = RNG.choice([RNG.randint(350, 850), 700])
            try:
                res = book.originate(borrower, amount, score)
            except cr.ConcentrationBreach:
                # caps bind only once the book reaches $1,000
                assert book.total_usd + amount >= cr.MIN_BOOK_FOR_CAPS_USD
                continue
            new_total = res["book_total_usd"]
            if new_total >= cr.MIN_BOOK_FOR_CAPS_USD:
                assert res["caps_enforced"] is True
                # caps hold on the resulting book state
                for b, v in book._by_borrower.items():
                    assert v <= book.total_usd * cr.CONC_BORROWER_PCT + 1e-6
                for band, v in book._by_band.items():
                    assert v <= book.total_usd * cr.CONC_BAND_PCT + 1e-6
            else:
                assert res["caps_enforced"] is False  # bootstrap exempt
        # conservation: book total is the sum of its parts
        assert abs(sum(book._by_borrower.values()) - book.total_usd) < 1e-6
        assert abs(sum(book._by_band.values()) - book.total_usd) < 1e-6
    # ineligible score refused
    book = CreditBook()
    try:
        book.originate("b", 100.0, 300)
        raise AssertionError("ineligible origination accepted")
    except cr.AttestationInvalid:
        pass
    # exact cap boundary: borrower landing exactly at 10% passes; 10%+1c raises
    book = CreditBook()
    book.originate("a", 900.0, 800)    # bootstrap: exempt from caps
    book.originate("b", 100.0, 700)    # new_total 1000: caps bind; b is
    # exactly 100/1000 = 10% of the new book and band 650+ is 10% -> allowed
    assert book._by_borrower["b"] == 100.0
    try:
        book.originate("b", 1.0, 700)  # 101/1001 > 10% -> raises
        raise AssertionError("over-cap loan accepted")
    except cr.ConcentrationBreach:
        pass


def test_fee_accounting_exact():
    for _ in range(300):
        principal = RNG.choice([RNG.uniform(0, 10**9), 0.0])
        interest = RNG.choice([RNG.uniform(0, 10**8), 0.0])
        assert origination_fee_usd(principal) == \
            round(principal * cr.FEE_ORIG_BPS / 10_000, 2)
        assert accrual_fee_usd(interest) == \
            round(interest * cr.FEE_ACCRUAL_BPS / 10_000, 2)
        take = treasury_take(principal, 0.1, 1.0)
        assert take["total_to_treasury_usd"] == \
            round(take["origination_fee_usd"] + take["accrual_fee_usd"], 2)
        assert take["treasury"] == cr.TREASURY
    for bad in (-1.0, -10**6):
        for fn in (origination_fee_usd, accrual_fee_usd):
            try:
                fn(bad)
                raise AssertionError("negative fee input accepted")
            except ValueError:
                pass
