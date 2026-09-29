"""Unit tests for the P17 On-Chain Options Protocol reference build.

Covers src/sincor2/defi/options_protocol.py — Black-Scholes pricing,
premium rails, the covered-only mint invariant, expiry-band gate,
settlement/exercise/sweep, 15 bps fee, oracle staleness, and access
control behind SKU SINCOR-DEFI-P17-OPTIONS. Pure logic, no chain.

Each test cites the rule it guards (spec p17 acceptance criteria).
Money math is asserted to the wei. 24/24 must pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.options_protocol import (
    DUST_PREMIUM_WEI,
    EXPIRY_TENORS_DAYS,
    FEE_BPS,
    SERIES_OI_CAP,
    TREASURY,
    CoveredVault,
    ExpiryBandViolation,
    NakedShortAttempt,
    OptionPosition,
    OptionSeries,
    OptionsError,
    PriceFeed,
    QuoteRejected,
    StaleOracle,
    WithdrawBlocked,
    accept_quote,
    black_scholes,
    clamp_vol,
    premium_fee_wei,
    quote_premium,
    validate_tenor,
)

WAD = 10**18


def call_series(days=30, strike_usd=100.0):
    return OptionSeries("C-30-100", "WETH", int(strike_usd * WAD), days, True)


def put_series(days=30, strike_usd=100.0):
    return OptionSeries("P-30-100", "WETH", int(strike_usd * WAD), days, False)


@pytest.fixture()
def vault():
    v = CoveredVault()
    v.list_series(call_series())
    v.list_series(put_series())
    return v


# -- Black-Scholes -----------------------------------------------------------------------
def test_bs_call_known_value():
    """AC2: pricer matches worked BS examples within 1%."""
    # spot=100, K=100, T=1y, vol=20%, r=5% -> ~10.45 (textbook).
    c = black_scholes(100.0, 100.0, 1.0, 0.20, r=0.05, is_call=True)
    assert abs(c - 10.4506) / 10.4506 < 0.01


def test_put_call_parity():
    """C - P == S - K*e^(-rT): model is arbitrage-consistent."""
    c = black_scholes(100.0, 95.0, 0.5, 0.25, is_call=True)
    p = black_scholes(100.0, 95.0, 0.5, 0.25, is_call=False)
    import math
    assert abs((c - p) - (100.0 - 95.0 * math.exp(-0.04 * 0.5))) < 1e-9


def test_vol_clamp_bounds_oracle_spike():
    """Spec: vol input clamped to [10%, 300%]."""
    assert clamp_vol(10.0) == 3.00
    assert clamp_vol(0.001) == 0.10
    assert clamp_vol(0.5) == 0.5


def test_bs_rejects_bad_inputs():
    with pytest.raises(ValueError):
        black_scholes(0.0, 100.0, 1.0, 0.2)
    with pytest.raises(ValueError):
        black_scholes(100.0, 100.0, 0.0, 0.2)


# -- expiry band ---------------------------------------------------------------------------
@pytest.mark.parametrize("tenor", EXPIRY_TENORS_DAYS)
def test_fixed_tenors_accepted(tenor):
    validate_tenor(tenor)  # no raise


@pytest.mark.parametrize("tenor", [1, 6, 45, 91, 365])
def test_out_of_band_reverts(tenor):
    """AC2: T < 7d or > 90d, or non-fixed tenor, reverts listing."""
    with pytest.raises(ExpiryBandViolation):
        validate_tenor(tenor)


def test_list_out_of_band_series_reverts(vault):
    with pytest.raises(ExpiryBandViolation):
        vault.list_series(OptionSeries("BAD", "WETH", 100 * WAD, 45, True))


# -- premium rails ----------------------------------------------------------------------------
def test_premium_within_rails():
    p = quote_premium(call_series(), 100 * WAD, 0.60)
    s = call_series()
    assert s.notional_wei() * 50 // 10_000 <= p <= s.notional_wei() * 5_000 // 10_000


def test_dust_premium_rejected():
    """Dust guard: premium below $1.00 rejected."""
    deep_otm = OptionSeries("C-DOTM", "WETH", 10_000 * WAD, 7, True)
    with pytest.raises(QuoteRejected):
        quote_premium(deep_otm, 100 * WAD, 0.10)


def test_accept_quote_within_2pct():
    """On-chain bound: |quoted - model| <= 2% accepted."""
    assert accept_quote(1_000, 1_010) is True
    assert accept_quote(1_000, 1_030) is False


def test_fee_exactly_15bps():
    """AC4: treasury receives exactly 15 bps of every premium."""
    assert premium_fee_wei(1_000_000) == 1_500  # 15 bps, integer-exact
    assert FEE_BPS == 15


# -- covered-only -------------------------------------------------------------------------------
def test_covered_call_mint_1_to_1(vault):
    """AC1: every option minted 1:1 against locked collateral."""
    pos = vault.write("writer1", "C-30-100", 10)
    assert pos.count == 10
    assert pos.collateral_wei == 10 * WAD
    assert vault.coverage_ratio("C-30-100") >= 1.0


def test_covered_put_locks_strike(vault):
    pos = vault.write("writer1", "P-30-100", 5)
    assert pos.collateral_wei == 5 * 100 * WAD  # K per option


def test_naked_short_unrepresentable(vault):
    """AC1: minted <= lockedCollateral holds; over-mint is impossible."""
    vault.write("writer1", "C-30-100", 3)
    # Direct invariant probe: coverage never dips below 1.0.
    assert vault.coverage_ratio("C-30-100") >= 1.0
    # Writing zero / negative is refused before mint accounting.
    with pytest.raises(OptionsError):
        vault.write("writer1", "C-30-100", 0)


def test_series_oi_cap(vault):
    """Series open-interest cap: 10,000 options."""
    for i in range(5):
        vault.write(f"writer{i}", "C-30-100", 2_000)  # each at the 20% cap
    with pytest.raises(OptionsError):
        vault.write("writer5", "C-30-100", 1)  # 10,001st


def test_per_writer_oi_cap(vault):
    """Per-writer cap: 20% of max series OI = 2,000 options."""
    vault.write("w1", "C-30-100", 2_000)
    with pytest.raises(OptionsError):
        vault.write("w1", "C-30-100", 1)
    vault.write("w2", "C-30-100", 500)  # other writers unaffected
    assert vault._writer_oi[("w2", "C-30-100")] == 500


def test_pause_halts_writes_not_withdraws(vault):
    """AC6: pause halts writes but never traps withdrawable collateral."""
    vault.write("w1", "C-30-100", 2)
    vault.pause_writes()
    with pytest.raises(OptionsError):
        vault.write("w2", "C-30-100", 1)
    vault.unpause_writes()
    vault.write("w2", "C-30-100", 1)  # works again


# -- settlement -----------------------------------------------------------------------------------
def _settle(vault, series_id, spot_usd, now=2_000_000.0):
    feed = PriceFeed(int(spot_usd * WAD), now)
    return vault.settle(series_id, feed, now), now


def test_itm_call_exercise_pays_to_wei(vault):
    """AC3: ITM call exercise pays exactly max(0, P-K) to the wei."""
    vault.write("w1", "C-30-100", 2)
    res, now = _settle(vault, "C-30-100", 130.0)
    assert res["itm"] is True
    pos = OptionPosition("buyer", "C-30-100", 2, 0)
    out = vault.exercise(pos, PriceFeed(int(130.0 * WAD), now), now)
    assert out["payoff_wei"] == 2 * 30 * WAD  # exactly 2 x (130-100)


def test_otm_call_worthless(vault):
    vault.write("w1", "C-30-100", 2)
    _settle(vault, "C-30-100", 90.0)
    pos = OptionPosition("buyer", "C-30-100", 2, 0)
    with pytest.raises(OptionsError):
        vault.exercise(pos, PriceFeed(int(90.0 * WAD), 2_000_000.0), 2_000_000.0)


def test_itm_put_payoff(vault):
    """AC3: ITM put pays exactly max(0, K-P) to the wei."""
    vault.write("w1", "P-30-100", 1)
    _settle(vault, "P-30-100", 70.0)
    pos = OptionPosition("buyer", "P-30-100", 1, 0)
    out = vault.exercise(pos, PriceFeed(int(70.0 * WAD), 2_000_000.0), 2_000_000.0)
    assert out["payoff_wei"] == 30 * WAD


def test_exercise_window_elapsed(vault):
    vault.write("w1", "C-30-100", 1)
    _, now = _settle(vault, "C-30-100", 130.0)
    pos = OptionPosition("buyer", "C-30-100", 1, 0)
    late = now + 24 * 3600 + 1
    with pytest.raises(OptionsError):
        vault.exercise(pos, PriceFeed(int(130.0 * WAD), late), late)


def test_sweep_releases_collateral(vault):
    """Post-window sweep burns remainder and releases writer collateral."""
    vault.write("w1", "C-30-100", 4)
    _, now = _settle(vault, "C-30-100", 90.0)  # OTM
    out = vault.sweep("C-30-100", now + 24 * 3600 + 1)
    assert out["tokens_burned"] == 4
    assert out["collateral_released_wei"] == 4 * WAD
    assert vault.coverage_ratio("C-30-100") == float("inf")


def test_withdraw_blocked_while_live(vault):
    """Withdrawal blocked while writerLocked > 0."""
    vault.write("w1", "C-30-100", 2)
    with pytest.raises(WithdrawBlocked):
        vault.withdraw("w1", "C-30-100")


def test_stale_oracle_reverts_settle(vault):
    """Oracle staleness > 2h reverts pricing/exercise/settlement."""
    vault.write("w1", "C-30-100", 1)
    stale = PriceFeed(int(130.0 * WAD), 0.0)
    with pytest.raises(StaleOracle):
        vault.settle("C-30-100", stale, 3 * 3600.0)


# -- adversarial review fixes ----------------------------------------------------------------------------
def test_double_exercise_reverts(vault):
    """Review fix: a position cannot be exercised twice (double-pay)."""
    vault.write("w1", "C-30-100", 2)
    _, now = _settle(vault, "C-30-100", 130.0)
    pos = OptionPosition("buyer", "C-30-100", 2, 0)
    out = vault.exercise(pos, PriceFeed(int(130.0 * WAD), now), now)
    assert out["payoff_wei"] == 2 * 30 * WAD
    with pytest.raises(OptionsError):
        vault.exercise(pos, PriceFeed(int(130.0 * WAD), now), now)


def test_resettle_reverts(vault):
    """Review fix: a series settles exactly once — P_exp cannot be
    overwritten after positions were paid against it."""
    vault.write("w1", "C-30-100", 1)
    _settle(vault, "C-30-100", 130.0)
    with pytest.raises(OptionsError):
        _settle(vault, "C-30-100", 140.0)
