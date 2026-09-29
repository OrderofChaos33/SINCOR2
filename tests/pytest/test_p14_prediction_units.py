"""Unit tests for P14 Prediction Market Automation reference build.

Covers src/sincor2/defi/prediction_markets.py — market data ingest
(staleness + quality bar), forecast calibration (Brier vs naive), the three
hard edge vetoes, quarter-Kelly sizing with confidence^2 and the 10% cap,
the fail-closed Polyclaw wallet adapter (treasury-key red-team), order
lifecycle with to-the-cent PnL reconciliation, risk limits (per-market loss,
daily breaker with manual-only reset, portfolio/category caps), the 15 bps
settled-PnL fee ledger, and the dry-run backtest.

Pure logic, no chain access. Every test names the invariant it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import prediction_markets as pm
from src.sincor2.defi.catalog import TREASURY
from src.sincor2.defi.prediction_markets import (
    EdgeVeto,
    ForecastEngine,
    KellySizer,
    MarketDataFeed,
    MarketSnapshot,
    OrderLifecycleManager,
    PolyclawWalletAdapter,
    RiskHalt,
    RiskManager,
    StaleDataError,
    TreasuryKeyBlockedError,
    check_edge,
    run_backtest,
)

NOW = 1_700_000_000
BANKROLL = 100_000_00  # $100k in cents


def _snap(**over):
    base = dict(market_id="m1", question="Will X happen?",
                yes_price=0.55, volume_24h_cents=5_000_000,
                oracle="uma", resolves_at=NOW + 7 * 86400,
                as_of=NOW, category="politics")
    base.update(over)
    return MarketSnapshot(**base)


# -- ingest --------------------------------------------------------------------
def test_ingest_accepts_fresh_quality_market():
    """Invariant: fresh, liquid, allowlisted-oracle markets ingest cleanly."""
    assert MarketDataFeed().ingest(_snap(), NOW).market_id == "m1"


def test_stale_data_unusable_for_sizing():
    """Invariant: data older than 60s is never traded on."""
    feed = MarketDataFeed()
    with pytest.raises(StaleDataError):
        feed.ingest(_snap(as_of=NOW - 61), NOW)
    assert feed.stale_drops == 1
    # 59s is fine
    feed.ingest(_snap(as_of=NOW - 59), NOW)


def test_low_volume_market_rejected():
    """Invariant: < $10k 24h volume markets are vetoed at ingest."""
    with pytest.raises(EdgeVeto):
        MarketDataFeed().ingest(_snap(volume_24h_cents=999_999), NOW)


def test_unallowlisted_oracle_rejected():
    """Invariant: markets without a resolvable allowlisted oracle are vetoed."""
    with pytest.raises(EdgeVeto):
        MarketDataFeed().ingest(_snap(oracle="unknown-dex"), NOW)


def test_degenerate_price_rejected():
    """Invariant: 0/1 prices (resolved or broken markets) are vetoed."""
    with pytest.raises(EdgeVeto):
        MarketDataFeed().ingest(_snap(yes_price=1.0), NOW)


# -- forecast + calibration ------------------------------------------------------
def test_forecast_beats_naive_on_scripted_corpus():
    """Invariant: the engine's Brier beats the naive baseline on the corpus."""
    engine = ForecastEngine()
    # scripted corpus: model has a real +5pt edge, outcomes follow the model
    import random
    rng = random.Random(7)
    for i in range(200):
        m = rng.uniform(0.3, 0.7)
        p = min(max(m + 0.05, 0.01), 0.99)
        outcome = 1 if rng.random() < p else 0
        engine.record_resolution(p, m, outcome)
    assert engine.beats_naive(), \
        (engine.brier_score(), engine.naive_brier_score())


def test_brier_persists_calibration_history():
    """Invariant: calibration history persists per resolved market."""
    engine = ForecastEngine()
    engine.record_resolution(0.7, 0.6, 1)
    engine.record_resolution(0.7, 0.6, 0)
    assert engine.brier_score() == pytest.approx(((0.3 ** 2) + (0.7 ** 2)) / 2)


def test_confidence_scales_with_signal():
    """Invariant: stronger signals produce higher confidence."""
    engine = ForecastEngine()
    weak = engine.predict(_snap(), signal=0.01)
    strong = engine.predict(_snap(), signal=0.10)
    assert strong.confidence > weak.confidence


# -- edge vetoes -------------------------------------------------------------------
def test_edge_veto_blocks_small_gap():
    """Invariant: |p_model - p_market| < 2% is a hard veto."""
    d = check_edge(p_model=0.56, p_market=0.55, confidence=0.9)
    assert not d.trade


def test_edge_veto_blocks_low_ev():
    """Invariant: EV < 3% is a hard veto even with a wide gap."""
    # p=0.60 vs m=0.55: edge 5% but EV = 0.60/0.55-1 = 9% -> passes;
    # use p barely above m so EV < 3%: p=0.565, m=0.55 -> EV 2.7%
    d = check_edge(p_model=0.565, p_market=0.55, confidence=0.9)
    assert not d.trade


def test_edge_veto_blocks_low_confidence():
    """Invariant: confidence < 60% is a hard veto."""
    d = check_edge(p_model=0.70, p_market=0.55, confidence=0.59)
    assert not d.trade


def test_edge_passes_all_three():
    """Invariant: edge >= 2% AND EV >= 3% AND conf >= 60% -> trade."""
    d = check_edge(p_model=0.70, p_market=0.55, confidence=0.80)
    assert d.trade
    assert d.edge >= 0.02 and d.expected_value >= 0.03


# -- Kelly sizing ----------------------------------------------------------------------
def test_kelly_quarter_times_confidence_squared():
    """Invariant: f_deployed = f* x 0.25 x confidence^2, exact formula."""
    sizer = KellySizer()
    p, m, conf = 0.60, 0.50, 0.80
    b = (1 - m) / m
    kelly_full = (p * b - (1 - p)) / b
    d = sizer.size(p, m, conf, BANKROLL)
    assert d.kelly_full == pytest.approx(kelly_full)
    assert d.fraction_of_bankroll == pytest.approx(kelly_full * 0.25 * conf ** 2)
    assert d.stake_cents == int(BANKROLL * d.fraction_of_bankroll)


def test_kelly_cap_binds_with_logged_warning():
    """Invariant: stakes never exceed 10% of bankroll; binding logs a warning."""
    sizer = KellySizer()
    d = sizer.size(p=0.95, market_price=0.50, confidence=0.95,
                   bankroll_cents=BANKROLL)
    assert d.capped
    assert d.fraction_of_bankroll == 0.10
    assert d.stake_cents == BANKROLL // 10
    assert sizer.cap_warnings == 1
    assert "kelly_cap" in d.warning


def test_kelly_never_negative_or_over_cap_in_fuzz():
    """Adversarial fuzz: stakes never exceed the cap under random inputs."""
    import random
    rng = random.Random(11)
    sizer = KellySizer()
    for _ in range(2000):
        p = rng.uniform(0.01, 0.99)
        m = rng.uniform(0.01, 0.99)
        conf = rng.uniform(0.0, 1.0)
        d = sizer.size(p, m, conf, BANKROLL)
        assert 0 <= d.stake_cents <= BANKROLL // 10
        assert d.fraction_of_bankroll <= 0.10 + 1e-12


def test_no_edge_means_zero_size():
    """Invariant: non-positive Kelly fraction -> zero size, never a bet."""
    d = KellySizer().size(p=0.40, market_price=0.55, confidence=0.9,
                          bankroll_cents=BANKROLL)
    assert d.stake_cents == 0


# -- wallet adapter (security boundary) ---------------------------------------------------
def test_treasury_key_blocked_and_logged():
    """Red-team: 100% of treasury-key attempts are blocked and logged."""
    adapter = PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID)
    for evil in (pm.TREASURY_KEY_ID, TREASURY, TREASURY.lower()):
        with pytest.raises(TreasuryKeyBlockedError):
            adapter.sign_order("m1", "YES", 1_000, 0.55, key_id=evil)
    assert len(adapter.blocked_attempts) == 3
    assert adapter.signed == []  # nothing signed


def test_unknown_key_blocked():
    """Red-team: unknown signing keys are blocked fail-closed."""
    adapter = PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID)
    with pytest.raises(TreasuryKeyBlockedError):
        adapter.sign_order("m1", "YES", 1_000, 0.55, key_id="random-key")


def test_polyclaw_key_signs_dry_run_end_to_end():
    """Invariant: the Polyclaw key signs; strategy logic never sees key material."""
    adapter = PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID, dry_run=True)
    order = adapter.sign_order("m1", "YES", 5_000, 0.55)
    assert order.key_id == pm.POLYCLAW_KEY_ID
    assert order.dry_run is True
    # the adapter exposes no key material, only labels
    assert not hasattr(adapter, "private_key")


def test_adapter_default_is_dry_run():
    """Invariant: dry_run=true is the default; live needs explicit opt-in."""
    assert PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID).dry_run is True


# -- order lifecycle -------------------------------------------------------------------------
def test_scripted_lifecycle_reconciles_to_the_cent():
    """Invariant: open -> resolve -> redeem reconciles PnL to the cent."""
    wallet = PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID)
    lc = OrderLifecycleManager(wallet)
    pos = lc.open_position(_snap(), "YES", 10_000, 0.50, NOW)
    pnl = lc.resolve(pos, outcome_yes=True)
    # bought 10_000c / 0.50 = 20_000 shares @ 100c = 2_000_000c payout
    assert pos.payout_cents == 2_000_000
    assert pnl == 1_990_000
    assert lc.realized_pnl_cents() == 1_990_000


def test_losing_position_pays_zero():
    """Invariant: a lost position pays exactly zero (CTF invariant)."""
    wallet = PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID)
    lc = OrderLifecycleManager(wallet)
    pos = lc.open_position(_snap(), "YES", 10_000, 0.50, NOW)
    assert lc.resolve(pos, outcome_yes=False) == -10_000
    assert pos.payout_cents == 0


def test_near_resolution_entry_blocked():
    """Invariant: no new entries within 2h of resolution (oracle risk guard)."""
    wallet = PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID)
    lc = OrderLifecycleManager(wallet)
    snap = _snap(resolves_at=NOW + 3600)
    with pytest.raises(RiskHalt) as exc:
        lc.open_position(snap, "YES", 1_000, 0.55, NOW)
    assert exc.value.alert == "near_resolution_block"


def test_fee_ledger_exact_15bps_and_reconciles():
    """Invariant: 15 bps on settled PnL routes to Treasury; ledger reconciles."""
    wallet = PolyclawWalletAdapter(pm.POLYCLAW_KEY_ID)
    lc = OrderLifecycleManager(wallet)
    pos = lc.open_position(_snap(), "YES", 10_000, 0.50, NOW)
    lc.resolve(pos, outcome_yes=True)  # pnl = +1_990_000c
    assert lc.fee_total_cents() == 1_990_000 * 15 // 10_000
    assert lc.fee_ledger[0].fee_to == TREASURY
    assert lc.fee_ledger_reconciles()
    # no fee on losses
    pos2 = lc.open_position(_snap(market_id="m2"), "YES", 10_000, 0.50, NOW)
    lc.resolve(pos2, outcome_yes=False)
    assert len(lc.fee_ledger) == 1


# -- risk manager --------------------------------------------------------------------------------
def test_daily_breaker_trips_and_needs_manual_reset():
    """Invariant: 5% daily loss halts new positions; reset is manual-only."""
    risk = RiskManager(BANKROLL)
    snap = _snap()
    risk.register_fill(snap, 1_000_00)
    risk.register_settlement("m1", -6_000_00, 1_000_00)  # -6% of bankroll
    assert risk.breaker_tripped
    with pytest.raises(RiskHalt) as exc:
        risk.check_new_position(_snap(market_id="m2"), 1_000, NOW)
    assert exc.value.alert == "daily_breaker"
    with pytest.raises(pm.LiveBlockedError):
        risk.reset_breaker(manual_approval=False)  # never auto-reset
    risk.reset_breaker(manual_approval=True)
    assert not risk.breaker_tripped


def test_per_market_loss_limit_halts():
    """Invariant: 3% per-market loss halts new orders in that market."""
    risk = RiskManager(BANKROLL)
    snap = _snap()
    risk.register_fill(snap, 1_000_00)
    risk.register_settlement("m1", -4_000_00, 1_000_00)  # -4% > 3%
    with pytest.raises(RiskHalt) as exc:
        risk.check_new_position(snap, 1_000, NOW)
    assert exc.value.alert == "per_market_loss_limit"


def test_portfolio_cap_and_duration_guards():
    """Invariant: 60% portfolio cap and 90-day max duration halt entries."""
    risk = RiskManager(BANKROLL)
    snap = _snap()
    risk.register_fill(snap, int(BANKROLL * 0.60))
    with pytest.raises(RiskHalt) as exc:
        risk.check_new_position(_snap(market_id="m2"), 1, NOW)
    assert exc.value.alert == "portfolio_cap"
    long_snap = _snap(market_id="m3", resolves_at=NOW + 91 * 86400)
    with pytest.raises(RiskHalt) as exc2:
        risk.check_new_position(long_snap, 1, NOW)
    assert exc2.value.alert == "max_duration"


# -- backtest --------------------------------------------------------------------------------------
def test_backtest_runs_green_with_reported_metrics():
    """Invariant: the dry-run backtest reports PnL, drawdown, Brier, fees."""
    import random
    rng = random.Random(3)
    markets = []
    for i in range(30):
        m = rng.uniform(0.35, 0.65)
        signal = 0.06 if rng.random() < 0.7 else -0.02
        p = min(max(m + signal, 0.01), 0.99)
        outcome = 1 if rng.random() < p else 0
        markets.append((_snap(market_id=f"m{i}", yes_price=m,
                              category="sports" if i % 2 else "politics"),
                        signal, outcome))
    res = run_backtest(markets, ForecastEngine(), KellySizer(), BANKROLL)
    assert res["markets_traded"] > 0
    assert isinstance(res["realized_pnl_cents"], int)
    assert res["max_drawdown_cents"] >= 0
    assert res["fee_ledger_reconciles"] is True
    assert res["fee_total_cents"] >= 0
    # per-market attribution present
    assert len(res["per_market"]) == res["markets_traded"]


def test_backtest_trades_across_iterations_not_just_first():
    """Regression: the harness must refresh quote timestamps each iteration.

    The backtest advances its clock per market; handing every market the same
    batch `as_of` would stale-out everything after the first trade. Fresh data
    must arrive per iteration, so later markets stay tradable.
    """
    import random
    rng = random.Random(3)
    markets = []
    for i in range(30):
        m = rng.uniform(0.35, 0.65)
        signal = 0.06 if rng.random() < 0.7 else -0.02
        p = min(max(m + signal, 0.01), 0.99)
        outcome = 1 if rng.random() < p else 0
        markets.append((_snap(market_id=f"m{i}", yes_price=m,
                              category="sports" if i % 2 else "politics"),
                        signal, outcome))
    res = run_backtest(markets, ForecastEngine(), KellySizer(), BANKROLL)
    traded_ids = [x["market_id"] for x in res["per_market"]]
    # markets beyond the first few must be reachable
    assert any(int(t[1:]) >= 5 for t in traded_ids), traded_ids


# -- per-category calibration + halt (kill-switch) --------------------------------
def test_brier_by_category_computed_per_category():
    """Invariant: Brier is tracked per category, not just globally."""
    engine = ForecastEngine()
    engine.record_resolution(0.9, 0.5, 1, category="sports")
    engine.record_resolution(0.9, 0.5, 0, category="sports")
    engine.record_resolution(0.6, 0.5, 1, category="politics")
    by_cat = engine.brier_by_category()
    assert set(by_cat) == {"sports", "politics"}
    assert by_cat["sports"] == pytest.approx(((0.1 ** 2) + (0.9 ** 2)) / 2)
    assert by_cat["politics"] == pytest.approx(0.4 ** 2)


def test_category_halt_triggers_above_025():
    """Invariant: Brier > 0.25 in a traded category halts it."""
    engine = ForecastEngine()
    for _ in range(10):
        engine.record_resolution(0.9, 0.5, 0, category="sports")  # always wrong
    assert engine.brier_by_category()["sports"] > 0.25
    assert engine.category_halted("sports") is True
    assert engine.category_halted("politics") is False  # untraded: never halts


def test_backtest_skips_halted_category():
    """Invariant: the backtest takes no new positions in a halted category."""
    import random
    rng = random.Random(11)
    engine = ForecastEngine()
    # poison the "sports" category: confidently wrong on every resolution
    for _ in range(20):
        engine.record_resolution(0.95, 0.5, 0, category="sports")
    assert engine.category_halted("sports")
    markets = []
    for i in range(20):
        m = rng.uniform(0.4, 0.6)
        signal = 0.08
        p = min(max(m + signal, 0.01), 0.99)
        outcome = 1 if rng.random() < p else 0
        cat = "sports" if i % 2 else "politics"
        markets.append((_snap(market_id=f"h{i}", yes_price=m, category=cat),
                        signal, outcome))
    res = run_backtest(markets, engine, KellySizer(), BANKROLL)
    traded = res["per_market"]
    assert traded, "politics markets should still trade"
    # every traded market id h<i> with even i is politics; none are sports
    sports_traded = [t for t in traded if int(t["market_id"][1:]) % 2 == 1]
    assert sports_traded == []
