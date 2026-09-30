"""Shared price oracle acceptance tests.

Self-contained: covers the oracle module (adapters, aggregation, guards,
circuit breaker, audit chain, fail-closed reads), adversarial cases
(stale feed, single-source manipulation, flash spike, all-sources-down,
deviation beyond threshold, cache behavior), and the per-product wiring
for all 17 price-needing products.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi import price_oracle as po
from src.sincor2.defi.price_oracle import (
    PRICE_FP,
    AssetConfig,
    AuditLog,
    ChainlinkFeedAdapter,
    ChainlinkRound,
    DexTwapAdapter,
    NoPriceAvailable,
    OracleError,
    PriceDegraded,
    PriceHalted,
    PriceOracle,
    PriceStale,
    ProductPriceFeed,
    ReferenceAdapter,
    TwapReading,
    wiring_for,
)

T0 = 1_750_000_000.0
ETH = "ETH/USD"
BTC = "BTC/USD"


def _ref(prices, ts=T0, ids=("ref-1", "ref-2", "ref-3")):
    """Oracle with N reference adapters seeded at given prices."""
    o = PriceOracle()
    for sid, px in zip(ids, prices):
        a = ReferenceAdapter(sid)
        a.set_price(ETH, px, ts)
        o.add_adapter(a)
    return o


# -- adapters ---------------------------------------------------------------

def test_reference_adapter_latest_wins_and_unknown_none():
    a = ReferenceAdapter("r1")
    assert a.kind == "reference"
    assert a.fetch(ETH, T0) is None
    a.set_price(ETH, 3_000 * PRICE_FP, T0 - 10)
    a.set_price(ETH, 3_100 * PRICE_FP, T0)
    pt = a.fetch(ETH, T0)
    assert pt.price_fp == 3_100 * PRICE_FP and pt.kind == "reference"
    with pytest.raises(OracleError):
        a.set_price(ETH, 0, T0)


def test_chainlink_adapter_valid_round_converts_decimals():
    def read(asset):
        assert asset == ETH
        return ChainlinkRound(round_id=7, answer=3_000_00000000, decimals=8,
                              started_at=T0 - 5, updated_at=T0 - 1,
                              answered_in_round=7)
    ad = ChainlinkFeedAdapter("cl-eth", read)
    assert ad.kind == "chainlink"
    pt = ad.fetch(ETH, T0)
    assert pt.price_fp == 3_000 * PRICE_FP  # 8dp -> 1e8 scale


def test_chainlink_adapter_rejects_stale_round_and_bad_answers():
    bad = [
        ChainlinkRound(7, 3_000_00000000, 8, T0 - 5, T0 - 1, 6),  # stale round
        ChainlinkRound(7, 0, 8, T0 - 5, T0 - 1, 7),  # zero answer
        ChainlinkRound(7, -5, 8, T0 - 5, T0 - 1, 7),  # negative answer
        ChainlinkRound(7, 3_000_00000000, 8, T0 - 5, 0, 7),  # no timestamp
    ]
    for rnd in bad:
        ad = ChainlinkFeedAdapter("cl", lambda asset, r=rnd: r)
        assert ad.fetch(ETH, T0) is None
    ad = ChainlinkFeedAdapter("cl", lambda asset: None)
    assert ad.fetch(ETH, T0) is None


def test_dex_twap_adapter_passthrough_and_rejects():
    ok = TwapReading(price_fp=3_000 * PRICE_FP, window_start_ts=T0 - 3600,
                     window_end_ts=T0, observed_ts=T0 - 2)
    ad = DexTwapAdapter("twap-1", lambda asset: ok)
    assert ad.kind == "dex_twap"
    assert ad.fetch(ETH, T0).price_fp == 3_000 * PRICE_FP
    bad = TwapReading(price_fp=0, window_start_ts=T0 - 1,
                      window_end_ts=T0, observed_ts=T0)
    ad2 = DexTwapAdapter("twap-2", lambda asset: bad)
    assert ad2.fetch(ETH, T0) is None
    inverted = TwapReading(price_fp=1, window_start_ts=T0,
                           window_end_ts=T0 - 1, observed_ts=T0)
    ad3 = DexTwapAdapter("twap-3", lambda asset: inverted)
    assert ad3.fetch(ETH, T0) is None


def test_duplicate_source_id_rejected():
    o = PriceOracle()
    o.add_adapter(ReferenceAdapter("r1"))
    with pytest.raises(OracleError):
        o.add_adapter(ReferenceAdapter("r1"))


# -- aggregation ------------------------------------------------------------

def test_median_odd_and_even_counts():
    o = _ref([3_000 * PRICE_FP, 3_010 * PRICE_FP, 2_990 * PRICE_FP])
    pub = o.publish(ETH, T0)
    assert pub.price_fp == 3_000 * PRICE_FP and pub.sources == 3
    assert not pub.degraded
    o2 = _ref([3_000 * PRICE_FP, 3_020 * PRICE_FP], ids=("a", "b"))
    pub2 = o2.publish(ETH, T0)
    assert pub2.price_fp == (3_000 * PRICE_FP + 3_020 * PRICE_FP) // 2


def test_min_sources_enforced_fail_closed():
    o = _ref([3_000 * PRICE_FP], ids=("only",))
    with pytest.raises(NoPriceAvailable):
        o.publish(ETH, T0)
    kinds = [e["kind"] for e in o.audit.entries()]
    assert "insufficient_sources" in kinds


def test_stale_points_dropped_never_silently_used():
    o = PriceOracle()
    old = ReferenceAdapter("old")
    old.set_price(ETH, 3_000 * PRICE_FP, T0 - 10_000)  # far too old
    fresh1 = ReferenceAdapter("f1")
    fresh1.set_price(ETH, 3_100 * PRICE_FP, T0)
    fresh2 = ReferenceAdapter("f2")
    fresh2.set_price(ETH, 3_100 * PRICE_FP, T0)
    for a in (old, fresh1, fresh2):
        o.add_adapter(a)
    pub = o.publish(ETH, T0)
    assert pub.price_fp == 3_100 * PRICE_FP and pub.sources == 2
    assert any(e["kind"] == "stale_source_dropped"
               for e in o.audit.entries())


def test_all_sources_stale_no_price():
    o = PriceOracle()
    for i in range(2):
        a = ReferenceAdapter(f"s{i}")
        a.set_price(ETH, 3_000 * PRICE_FP, T0 - 10_000)
        o.add_adapter(a)
    with pytest.raises(NoPriceAvailable):
        o.publish(ETH, T0)


def test_future_dated_point_rejected():
    o = PriceOracle()
    a = ReferenceAdapter("future")
    a.set_price(ETH, 3_000 * PRICE_FP, T0 + 3_600)  # not yet observable
    b = ReferenceAdapter("b")
    b.set_price(ETH, 3_000 * PRICE_FP, T0)
    c = ReferenceAdapter("c")
    c.set_price(ETH, 3_000 * PRICE_FP, T0)
    for x in (a, b, c):
        o.add_adapter(x)
    pub = o.publish(ETH, T0)
    assert pub.sources == 2  # future point excluded


# -- deviation guard + circuit breaker --------------------------------------

def test_deviation_warn_marks_degraded_but_publishes():
    o = _ref([3_000 * PRICE_FP, 3_030 * PRICE_FP, 3_000 * PRICE_FP])  # 1% spread
    pub = o.publish(ETH, T0)
    assert pub.degraded is True
    assert any(e["kind"] == "deviation_warn" for e in o.audit.entries())


def test_deviation_halt_trips_breaker():
    o = _ref([3_000 * PRICE_FP, 3_300 * PRICE_FP, 3_000 * PRICE_FP])  # 10%
    with pytest.raises(PriceHalted):
        o.publish(ETH, T0)
    assert o.is_halted(ETH)
    assert any(e["kind"] == "breaker_trip" for e in o.audit.entries())
    # halted: even reads refuse
    with pytest.raises(PriceHalted):
        o.get_price(ETH, T0)
    # and republishing while halted refuses too
    with pytest.raises(PriceHalted):
        o.publish(ETH, T0 + 1)


def test_consecutive_warns_trip_breaker():
    o = _ref([3_000 * PRICE_FP, 3_030 * PRICE_FP, 3_000 * PRICE_FP])
    o.configure(ETH, max_warn_trips=3)
    o.publish(ETH, T0)
    o.publish(ETH, T0 + 1)
    assert not o.is_halted(ETH)
    with pytest.raises(PriceHalted):
        o.publish(ETH, T0 + 2)
    assert o.is_halted(ETH)


def test_breaker_reset_is_audited_and_recovers():
    o = _ref([3_000 * PRICE_FP, 3_300 * PRICE_FP, 3_000 * PRICE_FP])
    with pytest.raises(PriceHalted):
        o.publish(ETH, T0)
    o.reset_halt(ETH, "sources reconciled, feed fixed", T0 + 5)
    assert not o.is_halted(ETH)
    assert any(e["kind"] == "breaker_reset" for e in o.audit.entries())
    # healthy sources now publish fine
    o2 = _ref([3_000 * PRICE_FP, 3_001 * PRICE_FP, 3_000 * PRICE_FP])
    pub = o2.publish(ETH, T0 + 6)
    assert pub.price_fp == 3_000 * PRICE_FP and not pub.degraded


# -- fail-closed reads -------------------------------------------------------

def test_get_price_fail_closed_modes():
    o = _ref([3_000 * PRICE_FP, 3_000 * PRICE_FP, 3_000 * PRICE_FP])
    with pytest.raises(NoPriceAvailable):
        o.get_price(ETH, T0)  # never published
    o.publish(ETH, T0)
    r = o.get_price(ETH, T0 + 10)
    assert r.price_fp == 3_000 * PRICE_FP and r.sources == 3
    with pytest.raises(PriceStale):
        o.get_price(ETH, T0 + 10_000)  # publication aged out


def test_strict_read_refuses_degraded():
    o = _ref([3_000 * PRICE_FP, 3_030 * PRICE_FP, 3_000 * PRICE_FP])
    o.publish(ETH, T0)
    assert o.get_price(ETH, T0).degraded is True
    with pytest.raises(PriceDegraded):
        o.get_price(ETH, T0, strict=True)


# -- adversarial -------------------------------------------------------------

def test_single_source_manipulation_median_holds():
    # attacker controls 1 of 3: 10x print. Median must not move; deviation
    # guard must fire (halt at 5%).
    o = _ref([3_000 * PRICE_FP, 3_000 * PRICE_FP, 30_000 * PRICE_FP])
    with pytest.raises(PriceHalted):
        o.publish(ETH, T0)
    assert o.is_halted(ETH)


def test_flash_spike_single_source_with_tight_sources_halts():
    o = PriceOracle()
    for i, px in enumerate([3_000 * PRICE_FP, 3_002 * PRICE_FP,
                            4_500 * PRICE_FP]):  # 50% spike on one
        a = ReferenceAdapter(f"s{i}")
        a.set_price(ETH, px, T0)
        o.add_adapter(a)
    with pytest.raises(PriceHalted):
        o.publish(ETH, T0)


def test_all_sources_down_no_price():
    o = PriceOracle()

    class Boom(ReferenceAdapter):
        def fetch(self, asset, now):
            raise RuntimeError("feed exploded")

    o.add_adapter(Boom("b1"))
    o.add_adapter(Boom("b2"))
    with pytest.raises(NoPriceAvailable):
        o.publish(ETH, T0)
    assert any(e["kind"] == "adapter_error" for e in o.audit.entries())


def test_one_adapter_error_does_not_kill_the_rest():
    o = PriceOracle()

    class Boom(ReferenceAdapter):
        def fetch(self, asset, now):
            raise RuntimeError("boom")

    o.add_adapter(Boom("bad"))
    for i, px in enumerate([3_000 * PRICE_FP, 3_000 * PRICE_FP]):
        a = ReferenceAdapter(f"g{i}")
        a.set_price(ETH, px, T0)
        o.add_adapter(a)
    pub = o.publish(ETH, T0)
    assert pub.price_fp == 3_000 * PRICE_FP and pub.sources == 2


def test_cache_behavior_publication_reused_then_refreshed():
    o = PriceOracle()
    adapters = []
    for i in range(3):
        a = ReferenceAdapter(f"c{i}")
        a.set_price(ETH, 3_000 * PRICE_FP, T0)
        a.set_price(ETH, 3_000 * PRICE_FP, T0 + 301)  # fresh for republish
        adapters.append(a)
        o.add_adapter(a)
    o.configure(ETH, max_age_s=300)
    pub1 = o.publish(ETH, T0)
    r = o.get_price(ETH, T0 + 100)
    assert r.ts == pub1.ts  # served from cache, no republish needed
    with pytest.raises(PriceStale):
        o.get_price(ETH, T0 + 301)  # aged out: must republish, not reuse
    pub2 = o.publish(ETH, T0 + 301)
    assert pub2.ts == T0 + 301
    assert o.get_price(ETH, T0 + 301).price_fp == 3_000 * PRICE_FP


# -- audit chain -------------------------------------------------------------

def test_audit_chain_verifies_and_detects_tamper():
    o = _ref([3_000 * PRICE_FP, 3_030 * PRICE_FP, 3_000 * PRICE_FP])
    o.publish(ETH, T0)
    assert o.audit.verify_chain() is True
    assert len(o.audit) >= 2  # deviation_warn + price_published
    # tamper with a recorded entry -> chain breaks
    o.audit._entries[0]["detail"] = {"forged": True}
    assert o.audit.verify_chain() is False


def test_every_publication_and_trip_is_logged():
    o = _ref([3_000 * PRICE_FP, 3_300 * PRICE_FP, 3_000 * PRICE_FP])
    with pytest.raises(PriceHalted):
        o.publish(ETH, T0)
    kinds = {e["kind"] for e in o.audit.entries()}
    assert "breaker_trip" in kinds
    o.reset_halt(ETH, "reviewed", T0 + 1)
    assert "breaker_reset" in {e["kind"] for e in o.audit.entries()}


# -- product wiring ----------------------------------------------------------

WIRED_MODULES = {
    "P01_YIELD_AGG": "src.sincor2.defi.yield_aggregator",
    "P02_CLMM": "src.sincor2.defi.clmm_manager",
    "P03_INTENT_DARK": "src.sincor2.defi.intent_dark_pool",
    "P06_PERPS": "src.sincor2.defi.perp_hedge_swarm",
    "P07_BRIDGE": "src.sincor2.defi.bridge_optimizer",
    "P08_RWA": "src.sincor2.defi.rwa_vaults",
    "P10_FLASH_ARB": "src.sincor2.defi.flash_arbitrage",
    "P11_DELTA_NEUTRAL": "src.sincor2.defi.delta_neutral",
    "P12_TWAMM": "src.sincor2.defi.twamm",
    "P13_AVS": "src.sincor2.defi.avs_tranching",
    "P14_PREDICTION": "src.sincor2.defi.prediction_markets",
    "P15_LENDING": "src.sincor2.defi.lending_optimizer",
    "P16_DEX_AGG": "src.sincor2.defi.dex_aggregator",
    "P17_OPTIONS": "src.sincor2.defi.options_protocol",
    "P18_STRUCTURED": "src.sincor2.defi.structured_products",
    "P19_CREDIT": "src.sincor2.defi.credit_underwriting",
    "P25_PORTFOLIO": "src.sincor2.defi.p25.rebalancer",
}


def _wired_oracle():
    o = PriceOracle()
    a1 = ReferenceAdapter("ref-a")
    a2 = ReferenceAdapter("ref-b")
    for a in (a1, a2):
        for asset, usd in (("ETH/USD", 3_000), ("BTC/USD", 97_000),
                           ("USDC/USD", 1)):
            a.set_price(asset, usd * PRICE_FP, T0)
        o.add_adapter(a)
    for asset in ("ETH/USD", "BTC/USD", "USDC/USD"):
        o.publish(asset, T0)
    return o


def test_all_17_products_declare_and_bind():
    o = _wired_oracle()
    assert set(WIRED_MODULES) == set(po.PRODUCT_PRICE_NEEDS)
    for key, modname in WIRED_MODULES.items():
        mod = importlib.import_module(modname)
        assert mod.PRICE_ASSETS == po.PRODUCT_PRICE_NEEDS[key], key
        feed = mod.price_feed_for(o)
        assert isinstance(feed, ProductPriceFeed)
        assert feed.product_key == key
        assert feed.assets == po.PRODUCT_PRICE_NEEDS[key]
        got = feed.require_all(T0 + 5)
        assert set(got) == set(feed.assets)
        expected = {"ETH/USD": 3_000 * PRICE_FP,
                    "BTC/USD": 97_000 * PRICE_FP,
                    "USDC/USD": 1 * PRICE_FP}
        for asset, val in got.items():
            assert val == expected[asset], (key, asset)


def test_feed_refuses_undeclared_asset():
    o = _wired_oracle()
    feed = wiring_for("P12_TWAMM", o)  # ETH/USD only
    with pytest.raises(OracleError):
        feed.price("BTC/USD", T0)


def test_feed_fail_closed_when_oracle_halted():
    o = _wired_oracle()
    feed = wiring_for("P15_LENDING", o)
    assert feed.price("ETH/USD", T0 + 5) == 3_000 * PRICE_FP
    # now poison the oracle: halt ETH
    o2 = PriceOracle()
    for i, px in enumerate([3_000 * PRICE_FP, 3_000 * PRICE_FP,
                            9_000 * PRICE_FP]):
        a = ReferenceAdapter(f"h{i}")
        a.set_price("ETH/USD", px, T0)
        o2.add_adapter(a)
    feed2 = wiring_for("P15_LENDING", o2)
    with pytest.raises(PriceHalted):
        o2.publish("ETH/USD", T0)
    with pytest.raises(PriceHalted):
        feed2.price("ETH/USD", T0)


def test_products_without_price_needs_rejected():
    o = _wired_oracle()
    with pytest.raises(KeyError):
        wiring_for("P20_COMPLIANCE", o)
    with pytest.raises(KeyError):
        wiring_for("P04_MEV", o)


def test_perp_native_sync_routes_through_shared_oracle():
    from src.sincor2.defi import perp_hedge_swarm as phs
    o = _wired_oracle()
    feed = phs.price_feed_for(o)
    engine_feed = phs.PriceFeed()
    got = engine_feed.sync_from_oracle(feed, "ETH/USD", T0 + 5)
    assert got == 3_000 * PRICE_FP
    assert engine_feed.get(T0 + 5) == 3_000 * PRICE_FP
    # oracle failure leaves the local feed untouched (fail-static)
    o2 = PriceOracle()  # nothing published
    feed2 = phs.price_feed_for(o2)
    with pytest.raises(NoPriceAvailable):
        engine_feed.sync_from_oracle(feed2, "ETH/USD", T0 + 6)
    assert engine_feed.get(T0 + 6) == 3_000 * PRICE_FP  # old price intact


def test_oracle_config_rejects_unknown_keys():
    o = PriceOracle()
    with pytest.raises(OracleError):
        o.configure("ETH/USD", not_a_key=1)
