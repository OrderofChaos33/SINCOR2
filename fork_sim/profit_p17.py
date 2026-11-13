"""P17 profitability fork-sim: a covered-call strategy through the REAL protocol.

Founder bar (2026-10-22): "if they make money on sepolia go live" — proven
profitability is the go-live bar. This module produces that evidence for
P17 (On-Chain Options Protocol) without inventing anything.

STRATEGY SCC-30-10 — systematic covered-call writing on ETH (ONE strategy,
defined once, documented below; parameters were committed BEFORE the first
run and are never tuned to the outcome):
  * Every 30 days, sell 30-day European calls struck 10% above spot.
  * 50% overwrite: 1 call written per 2 ETH of inventory (bounds the
    single-cycle assignment loss; standard covered-call-fund practice).
  * Covered 1:1 by the vault: each written call locks 1 ETH in the real
    CoveredVault; the covered-only invariant (minted <= lockedCollateral)
    is asserted on every mint.
  * Quote = Black-Scholes model (trailing-30d realized vol from historical
    rounds, NO lookahead) marked up to the top of the protocol's on-chain
    2% tolerance band (accept_quote). The markup is the liquidity-provider
    edge; the protocol's own pricer accepts it.
  * Premium rails [0.5%, 50%] of notional enforced by quote_premium; a
    railed quote fails closed (cycle skipped, never forced).
  * Settlement through the real vault: ITM exercise pays exactly
    max(0, P-K) per the protocol; OTM sweeps burn and release collateral.
  * Fees: 15 bps of every premium routes to treasury (premium_fee_wei,
    the protocol rule), then the LOCKED 5% platform fee on net profit.

  Why covered calls (and not cash-secured puts): the CoveredVault's
  native collateral unit is 1 unit of underlying per call — covered calls
  are the protocol's primary case. 30d is in the fixed tenor set
  {7,14,30,60,90} and inside the 7d..90d band. 10% OTM keeps the premium
  above the 0.5%-of-notional floor across vol regimes while keeping the
  keep-the-premium (win) rate high.

REGIME (committed before the first run; not tuned):
  * Synthetic ETH path: geometric Brownian motion, mu=0.15, sigma=0.75,
    S0=$3000, hourly steps, fixed seed 20261022. Zero drift would FLATTER
    a short-call strategy (drift is the call writer's headwind), so the
    modest bull drift is the adversarial-but-realistic choice.
  * Rounds are served by a HERMETIC mock transport (no live RPC anywhere;
    rpc_url is "mock://hermetic" and the mock refuses anything else).
    Historical prices flow through the REAL ChainlinkRoundReader
    (fail-closed validation: round 0 / reverted / superseded / stale /
    non-positive answers all raise).
  * Feed address comes from the repo-pinned BASE_MAINNET_FEEDS via
    ChainlinkRoundReader.for_asset("ETH/USD") — never invented.

VERDICT MEASUREMENT (anti-juice rule):
  * The GO bar is applied to the STRATEGY OVERLAY P&L
    (premium kept - assignment payouts - fees), NOT to total equity.
    Crediting ETH's own drift to the strategy would juice the verdict;
    total equity (with inventory mark-to-market) is reported as context
    only, with the drift contribution broken out.
  * Machinery check (flat $3000 path, like P14's perfect-signal bound):
    no call is ever exercised, so the machinery MUST keep
    premium-minus-fees. If it doesn't, the vault/fee plumbing is broken.

GO/NO-GO threshold (explicit, same bars as P14):
  GO  iff  net_pnl > 0
       AND win_rate >= 0.40
       AND max_drawdown < 20% of bankroll
       AND trades >= 10            (degenerate guard: not trading != profit)
  otherwise NO-GO with the named reason.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sincor2.defi.chainlink_round_reader import (
    ChainlinkRoundReader,
    InvalidRound,
    StaleRound,
)
from sincor2.defi.options_protocol import (
    QUOTE_TOLERANCE,
    CoveredVault,
    OptionSeries,
    PriceFeed,
    QuoteRejected,
    accept_quote,
    black_scholes,
    clamp_vol,
    premium_fee_wei,
    quote_premium,
)

# ---------------------------------------------------------------------------
# Strategy + regime constants (committed before the first run; never tuned).
# ---------------------------------------------------------------------------
STRATEGY_ID = "SCC-30-10"
TENOR_DAYS = 30
OTM_FACTOR = 1.10              # 10% out of the money
OVERWRITE_NUM = 1              # 1 call per ...
OVERWRITE_DEN = 2              # ... 2 ETH of inventory (50% overwrite)
QUOTE_MARKUP_NUM = 10199         # quote at +1.99%: the top of the on-chain
QUOTE_MARKUP_DEN = 10000        # 2% band, minus float-dust margin. (The
                                # protocol's accept_quote computes tolerance
                                # as int(model_wei * 0.02) in FLOAT math, so
                                # at 1e20-wei scale its bound carries ~1e3
                                # wei of float noise; 1.99% in exact integer
                                # math stays strictly inside it.)
VOL_LOOKBACK_HOURS = 720       # trailing 30d realized vol, hourly rounds
BANKROLL_USD = 100_000.0
PLATFORM_FEE_BPS = 500         # locked 5% platform fee to treasury
CYCLE_DAYS = 30
FIRST_WRITE_DAY = 30
N_CYCLES = 13
SIM_DAYS = 450                 # covers last expiry (day 420) + margin
ROUND_INTERVAL_S = 3600        # synthetic hourly Chainlink rounds
SEED = 20261022                # verdict-path seed (fixed; never re-rolled)
GBM_MU = 0.15
GBM_SIGMA = 0.75
GBM_S0 = 3000.0
T0 = 1_700_000_000.0
MOCK_RPC_URL = "mock://hermetic"
WRITER = "scc-30-10-writer"
WAD = 10**18

_GET_ROUND_DATA_SELECTOR = bytes.fromhex("9a6fc8f5")
_DECIMALS_SELECTOR = bytes.fromhex("313ce567")
_ROUND_ABI = ["uint80", "int256", "uint256", "uint256", "uint80"]


# ---------------------------------------------------------------------------
# Hermetic mock transport: synthetic Chainlink rounds, no network.
# ---------------------------------------------------------------------------
class MockRoundTransport:
    """Serves decimals() and getRoundData() for a synthetic round history.

    Hermetic: refuses any rpc_url other than "mock://hermetic". Round ids
    are 1-based (round 0 raises InvalidRound in the reader, as on-chain).
    ``tamper`` maps round_id -> replacement answeredInRound for fail-closed
    tests. Every call is logged so tests can prove no live RPC was used.
    """

    def __init__(self, prices: List[float], t0: float = T0,
                 interval_s: int = ROUND_INTERVAL_S,
                 tamper: Optional[Dict[int, int]] = None):
        self._prices = list(prices)
        self._t0 = t0
        self._interval = interval_s
        self._tamper = dict(tamper or {})
        self.calls: List[tuple] = []

    def __call__(self, rpc_url: str, to_address: str,
                 calldata: bytes) -> bytes:
        from eth_abi import encode
        self.calls.append((rpc_url, to_address, calldata[:4].hex()))
        if rpc_url != MOCK_RPC_URL:
            raise RuntimeError(
                f"mock transport refuses non-mock rpc_url {rpc_url!r}")
        if calldata[:4] == _DECIMALS_SELECTOR:
            return encode(["uint256"], [8])
        if calldata[:4] != _GET_ROUND_DATA_SELECTOR:
            raise RuntimeError("mock transport: unknown selector")
        round_id = int.from_bytes(calldata[4:36], "big")
        if round_id < 1 or round_id > len(self._prices):
            # Reverted / non-existent round: answeredInRound == 0.
            return encode(_ROUND_ABI, [0, 0, 0, 0, 0])
        price = self._prices[round_id - 1]
        ts = int(self._t0 + (round_id - 1) * self._interval)
        answered = self._tamper.get(round_id, round_id)
        answer = int(round(price * 10**8))  # 8dp like the ETH/USD feed
        return encode(_ROUND_ABI,
                      [round_id, answer, ts, ts, answered])

    def assert_hermetic(self) -> None:
        bad = [c for c in self.calls if c[0] != MOCK_RPC_URL]
        assert not bad, f"non-mock RPC used: {bad}"


def build_market(seed: int = SEED, days: int = SIM_DAYS, mu: float = GBM_MU,
                 sigma: float = GBM_SIGMA, s0: float = GBM_S0) -> List[float]:
    """Deterministic GBM hourly price path (seeded; no live data)."""
    rng = random.Random(seed)
    dt = 1.0 / (365.0 * 24.0)
    n = int(days * 24) + 1
    prices = [s0]
    drift = (mu - 0.5 * sigma * sigma) * dt
    diff = sigma * math.sqrt(dt)
    for _ in range(1, n):
        prices.append(prices[-1] * math.exp(drift + diff * rng.gauss(0.0, 1.0)))
    return prices


def realized_vol_annual(price_wei: List[int]) -> float:
    """Trailing realized vol (annualized) from hourly closes. No lookahead."""
    if len(price_wei) < 3:
        return clamp_vol(0.0)
    rets = [math.log(price_wei[i] / price_wei[i - 1])
            for i in range(1, len(price_wei)) if price_wei[i - 1] > 0]
    if len(rets) < 2:
        return clamp_vol(0.0)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return clamp_vol(math.sqrt(var) * math.sqrt(365.0 * 24.0))


def apply_fees(gross: float, product_fee: float):
    """Net overlay P&L after product fee and the locked 5% platform fee.

    Platform fee applies to net profit only (never on losses).
    Returns (net_pnl, platform_fee).
    """
    net_before_platform = gross - product_fee
    platform_fee = max(0.0, net_before_platform) * PLATFORM_FEE_BPS / 10_000
    return net_before_platform - platform_fee, platform_fee


def profitability_verdict(stats: Dict):
    """Explicit GO/NO-GO threshold. Returns (verdict, reason)."""
    if stats["trades"] < 10:
        return "NO-GO", (f"only {stats['trades']} trades "
                         f"(< 10; not trading != profit)")
    if stats["net_pnl"] <= 0:
        return "NO-GO", f"net P&L {stats['net_pnl']:+.2f} USD not positive"
    if stats["win_rate"] < 0.40:
        return "NO-GO", f"win rate {stats['win_rate']:.1%} < 40%"
    if stats["max_dd"] >= BANKROLL_USD * 0.20:
        return "NO-GO", (f"max drawdown {stats['max_dd']:.2f} USD "
                         f">= 20% of bankroll")
    return "GO", "all bars cleared: positive net, >=40% win rate, <20% drawdown"


@dataclass
class CycleResult:
    cycle: int
    write_day: float
    spot_usd: float
    strike_usd: float
    vol: float
    n: int
    premium_usd: float       # writer-kept (after 15bps treasury routing)
    product_fee_usd: float   # 15bps routed to treasury
    itm: bool
    payout_usd: float
    overlay_pnl: float       # premium_usd - payout_usd
    skipped: str = ""        # reason if the cycle did not trade
    coverage_ratio: float = 0.0


def _reader(prices: List[float], t0: float, tamper=None) -> tuple:
    transport = MockRoundTransport(prices, t0=t0, tamper=tamper)
    reader = ChainlinkRoundReader.for_asset(
        "ETH/USD", MOCK_RPC_URL, max_age_s=45 * 86400, transport=transport)
    return reader, transport



def run_strategy_sim(prices: List[float], t0: float = T0,
                     n_cycles: int = N_CYCLES,
                     tamper=None) -> Dict:
    """Run SCC-30-10 through the REAL CoveredVault + ChainlinkRoundReader.

    Every price the strategy sees comes from a validated historical round
    via the reader (mock transport). Staleness is judged against the SIM
    clock (the historically correct semantic for a backtest: "was this
    round fresh at the time it was read"), via read_round(rid, now=...).
    Every write goes through the real vault (covered invariant asserted
    per mint); settlement uses the real settle/exercise/sweep; the
    vault-computed payoff is cross-checked against independent
    max(0, P-K) math.
    """
    reader, transport = _reader(prices, t0, tamper=tamper)
    vault = CoveredVault()
    interval = ROUND_INTERVAL_S

    def round_at(ts: float) -> int:
        rid = int(round((ts - t0) / interval)) + 1
        return max(1, min(rid, len(prices)))

    inventory_eth = int(BANKROLL_USD // prices[0])
    cash = BANKROLL_USD - inventory_eth * prices[0]
    treasury_routed = 0.0
    cycles: List[CycleResult] = []
    overlay_curve = [0.0]
    equity_curve = [BANKROLL_USD]

    for c in range(n_cycles):
        write_time = t0 + (FIRST_WRITE_DAY + c * CYCLE_DAYS) * 86400.0
        rid = round_at(write_time)
        if rid + 1 > len(prices) or rid - VOL_LOOKBACK_HOURS < 1:
            cycles.append(CycleResult(
                c, FIRST_WRITE_DAY + c * CYCLE_DAYS, 0, 0, 0, 0,
                0, 0, False, 0, 0, skipped="round window out of range"))
            continue
        rnd = reader.read_round(rid, now=write_time)
        feed = PriceFeed(spot_wei=rnd.price_wei, updated_at=rnd.timestamp)
        spot_wei = feed.get_spot(write_time)   # reverts if stale
        spot = spot_wei / WAD

        # Trailing-30d realized vol from validated historical rounds.
        hist = [reader.read_round(rid - k, now=write_time).price_wei
                for k in range(1, VOL_LOOKBACK_HOURS + 1)]
        vol = realized_vol_annual(hist)

        strike = spot * OTM_FACTOR
        strike_wei = int(round(strike * WAD))
        series_id = f"{STRATEGY_ID}-C{c:02d}-{int(round(strike))}"
        series = OptionSeries(series_id, "WETH", strike_wei, TENOR_DAYS, True)
        vault.list_series(series)

        n = (inventory_eth * OVERWRITE_NUM) // OVERWRITE_DEN
        if n < 1:
            cycles.append(CycleResult(
                c, FIRST_WRITE_DAY + c * CYCLE_DAYS, spot, strike, vol, 0,
                0, 0, False, 0, 0, skipped="no inventory to overwrite"))
            continue
        try:
            premium_wei = quote_premium(series, spot_wei, vol)
        except QuoteRejected as exc:
            cycles.append(CycleResult(
                c, FIRST_WRITE_DAY + c * CYCLE_DAYS, spot, strike, vol, n,
                0, 0, False, 0, 0, skipped=f"quote railed: {exc}"))
            continue
        # LP edge: quote at the top of the on-chain 2% tolerance band.
        quoted_wei = premium_wei * QUOTE_MARKUP_NUM // QUOTE_MARKUP_DEN
        assert accept_quote(quoted_wei, premium_wei), \
            "markup breached the on-chain pricer bound (fail closed)"

        pos = vault.write(WRITER, series_id, n)
        cov = vault.coverage_ratio(series_id)
        assert cov >= 1.0, f"covered invariant broken: {cov}"

        fee_each = premium_fee_wei(quoted_wei)
        kept_each = quoted_wei - fee_each
        premium_usd = kept_each * n / WAD
        fee_usd = fee_each * n / WAD
        cash += premium_usd
        treasury_routed += fee_usd

        expiry_time = write_time + TENOR_DAYS * 86400.0
        rid_e = round_at(expiry_time)
        rnd_e = reader.read_round(rid_e, now=expiry_time)
        feed_e = PriceFeed(spot_wei=rnd_e.price_wei,
                           updated_at=rnd_e.timestamp)
        res = vault.settle(series_id, feed_e, expiry_time)
        spot_e = res["spot_wei"] / WAD
        payout_usd = 0.0
        if res["itm"]:
            ex = vault.exercise(pos, feed_e, expiry_time)
            # Cross-check the protocol's settlement math independently.
            expect = max(0.0, spot_e - strike) * n
            got = ex["payoff_wei"] / WAD
            assert abs(got - expect) < 1e-6, \
                f"vault payoff {got} != max(0,P-K)*n {expect}"
            payout_usd = got
            cash += n * strike - payout_usd   # assigned: deliver ETH at K
            inventory_eth -= n
        else:
            vault.sweep(series_id, expiry_time + 25 * 3600.0)
            vault.withdraw(WRITER, series_id)

        overlay = premium_usd - payout_usd
        overlay_curve.append(overlay_curve[-1] + overlay)
        equity_curve.append(cash + inventory_eth * spot_e)
        cycles.append(CycleResult(
            c, FIRST_WRITE_DAY + c * CYCLE_DAYS, spot, strike, vol, n,
            premium_usd, fee_usd, res["itm"], payout_usd, overlay,
            coverage_ratio=cov))

    transport.assert_hermetic()
    trades = [cy for cy in cycles if not cy.skipped]
    wins = [cy for cy in trades if cy.overlay_pnl > 0]
    gross = sum(cy.overlay_pnl for cy in trades)
    product_fee = sum(cy.product_fee_usd for cy in trades)
    net, platform_fee = apply_fees(gross, product_fee)
    peak = overlay_curve[0]
    max_dd = 0.0
    for v in overlay_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return {
        "strategy": STRATEGY_ID,
        "seed": SEED,
        "prices": len(prices),
        "cycles": len(cycles),
        "trades": len(trades),
        "skipped": len(cycles) - len(trades),
        "wins": len(wins),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "gross_pnl": gross,
        "product_fee": product_fee,
        "platform_fee": platform_fee,
        "net_pnl": net,
        "max_dd": max_dd,
        "bankroll": BANKROLL_USD,
        "treasury_routed": treasury_routed,
        "final_cash": cash,
        "final_inventory_eth": inventory_eth,
        "final_equity": equity_curve[-1],
        "drift_pnl": equity_curve[-1] - BANKROLL_USD - gross,
        "overlay_curve": overlay_curve,
        "cycles_detail": [c.__dict__ for c in cycles],
    }


def run_machinery_check() -> Dict:
    """Machinery upper bound: oscillating path, never assigned.

    Like P14's perfect-signal check: prices oscillate +/-5% daily around
    $3000 (high realized vol so quotes clear the premium rails, but the
    30d return never reaches the +10% strike). No call is ever exercised,
    so the vault/fee plumbing MUST keep premium-minus-fees. If it
    doesn't, the machinery (not the strategy) is broken.
    """
    n = SIM_DAYS * 24 + 1
    prices = [GBM_S0 * (1.0 + 0.05 * math.sin(2.0 * math.pi * i / 24.0))
              for i in range(n)]
    stats = run_strategy_sim(prices, n_cycles=N_CYCLES)
    return {"stats": stats}


def summarize(label: str, stats: Dict) -> str:
    verdict, reason = profitability_verdict(stats)
    return (
        f"[{verdict}] {label}: net {stats['net_pnl']:+.2f} USD "
        f"(gross {stats['gross_pnl']:+.2f}, product fee "
        f"{stats['product_fee']:.2f}, platform 5% "
        f"{stats['platform_fee']:.2f}) over {stats['trades']} trades "
        f"({stats['skipped']} skipped), win rate {stats['win_rate']:.1%}, "
        f"max DD {stats['max_dd']:.2f} USD — {reason}"
    )


if __name__ == "__main__":
    print(f"strategy={STRATEGY_ID} seed={SEED} "
          f"mu={GBM_MU} sigma={GBM_SIGMA} s0={GBM_S0}")
    prices = build_market()
    stats = run_strategy_sim(prices)
    print(summarize("SCC-30-10 verdict path", stats))
    print(f"  treasury routed (15bps): {stats['treasury_routed']:.2f} USD | "
          f"final equity {stats['final_equity']:.2f} USD "
          f"(overlay {stats['gross_pnl']:+.2f} + drift "
          f"{stats['drift_pnl']:+.2f}) | "
          f"inventory {stats['final_inventory_eth']} ETH")
    mach = run_machinery_check()
    ms = mach["stats"]
    print(summarize("machinery check (flat path)", ms))
    # Robustness context only (never the verdict): extra seeds.
    for extra in (7, 99, 1234):
        p = build_market(seed=extra)
        s = run_strategy_sim(p)
        v, _ = profitability_verdict(s)
        print(f"  seed {extra}: net {s['net_pnl']:+.2f} USD, "
              f"win {s['win_rate']:.0%}, dd {s['max_dd']:.2f} -> {v}")
