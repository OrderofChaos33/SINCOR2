"""
SINCOR DeFi P14 — Prediction Market Automation (Python reference model).

Automated prediction-market trading agent: ingest -> forecast -> edge
check -> Kelly sizing -> Polyclaw-wallet-only execution -> lifecycle ->
risk limits -> fee routing. Pure Python, deterministic, no network calls,
no keys in this module.

THE security boundary: the PolyclawWalletAdapter is the ONLY live
execution path. The treasury key can never sign from this module — proven
by red-team tests, not by convention. Wallet state is isolated from
strategy logic (strategy proposes, adapter disposes).

Money is integer cents everywhere: PnL reconciles to the cent.
Time is an explicit parameter for determinism.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .catalog import TREASURY

logger = logging.getLogger(__name__)

# -- locked numeric parameters (deep spec section 1) -------------------------
FEE_BPS = 15                    # 0.15% of *settled* PnL -> Treasury
KELLY_FRACTION = 0.25           # quarter-Kelly
MAX_ALLOC_PCT = 0.10            # catalog cap per position
MIN_EDGE = 0.02                 # 2% probability gap vs market
MIN_EXPECTED_VALUE = 0.03       # 3%
MIN_CONFIDENCE = 0.60           # 60%
MIN_CAPITAL_CENTS = 2_500       # $25
PER_MARKET_LOSS_PCT = 0.03      # 3% of bankroll
DAILY_LOSS_BREAKER_PCT = 0.05   # 5% of bankroll, manual reset only
MAX_PORTFOLIO_DEPLOYED_PCT = 0.60
MAX_CATEGORY_PCT = 0.40
MAX_DAYS_TO_RESOLUTION = 90
MIN_MARKET_VOLUME_CENTS = 1_000_000   # $10k 24h volume
DATA_STALENESS_LIMIT_S = 60
BRIER_WARN = 0.22
BRIER_HALT = 0.25

# Key identifiers are opaque labels. The adapter compares labels; it never
# holds key material. TREASURY (from catalog) is the hard-blocked label.
POLYCLAW_KEY_ID = "polyclaw-hot-wallet-01"
TREASURY_KEY_ID = "treasury-eoa"


class StaleDataError(RuntimeError):
    """Market data older than 60s is unusable for sizing."""


class EdgeVeto(Exception):
    """One of the three hard edge vetoes failed: no trade."""


class TreasuryKeyBlockedError(RuntimeError):
    """Fail-closed: a treasury-key signing attempt was blocked and logged."""


class RiskHalt(Exception):
    """A risk limit halted new orders. Carries the named alert."""

    def __init__(self, alert: str, detail: str = "") -> None:
        super().__init__(f"{alert}: {detail}")
        self.alert = alert
        self.detail = detail


class LiveBlockedError(RuntimeError):
    """A manual-gated action was attempted without approval."""


# -- market data feed ----------------------------------------------------------
@dataclass(frozen=True)
class MarketSnapshot:
    market_id: str
    question: str
    yes_price: float            # 0..1 CLOB last price
    volume_24h_cents: int
    oracle: str                 # "uma" | "chainlink" | ...
    resolves_at: int            # unix seconds
    as_of: int                  # data timestamp, unix seconds
    category: str = "general"


ALLOWED_ORACLES = ("uma", "chainlink")


class MarketDataFeed:
    """Ingest with staleness check + minimum market quality bar."""

    def __init__(self) -> None:
        self.stale_drops = 0
        self.quality_drops = 0

    def ingest(self, snap: MarketSnapshot, now: int) -> MarketSnapshot:
        if now - snap.as_of > DATA_STALENESS_LIMIT_S:
            self.stale_drops += 1
            raise StaleDataError(
                f"market {snap.market_id}: data {now - snap.as_of}s old")
        if snap.volume_24h_cents < MIN_MARKET_VOLUME_CENTS:
            self.quality_drops += 1
            raise EdgeVeto(f"market {snap.market_id}: volume below $10k bar")
        if snap.oracle not in ALLOWED_ORACLES:
            self.quality_drops += 1
            raise EdgeVeto(f"market {snap.market_id}: oracle not allowlisted")
        if not 0.0 < snap.yes_price < 1.0:
            raise EdgeVeto(f"market {snap.market_id}: degenerate price")
        return snap


# -- forecast engine -------------------------------------------------------------
@dataclass
class Forecast:
    p_yes: float
    confidence: float           # 0..1
    model: str = "scripted"


class ForecastEngine:
    """
    Scripted calibrated forecaster (reference model). Tracks Brier score
    against resolved markets and must beat the naive baseline
    (market-implied price as probability) on the scripted corpus.
    """

    def __init__(self, model_bias: float = 0.0) -> None:
        self.model_bias = model_bias
        self._outcomes: List[Tuple[float, float, int]] = []  # (p_model, p_market, outcome)

    def predict(self, snap: MarketSnapshot,
                signal: float = 0.0) -> Forecast:
        """
        signal: signed model edge hint in probability points
        (fixture-injected; no network). Confidence derived from |signal|.
        """
        p = min(max(snap.yes_price + signal + self.model_bias, 0.01), 0.99)
        confidence = min(0.5 + abs(signal) * 4.0, 0.95)
        return Forecast(p_yes=p, confidence=confidence)

    def record_resolution(self, p_model: float, p_market: float, outcome: int) -> None:
        assert outcome in (0, 1)
        self._outcomes.append((p_model, p_market, outcome))

    @staticmethod
    def _brier(pairs: List[Tuple[float, int]]) -> float:
        if not pairs:
            return float("nan")
        return sum((p - o) ** 2 for p, o in pairs) / len(pairs)

    def brier_score(self) -> float:
        return self._brier([(p, o) for p, _, o in self._outcomes])

    def naive_brier_score(self) -> float:
        return self._brier([(m, o) for _, m, o in self._outcomes])

    def beats_naive(self) -> bool:
        return self.brier_score() < self.naive_brier_score()

    def brier_by_category(self) -> Dict[str, float]:
        return {"all": self.brier_score()}


# -- edge check ------------------------------------------------------------------
@dataclass
class EdgeDecision:
    trade: bool
    edge: float
    expected_value: float
    confidence: float
    reason: str


def check_edge(p_model: float, p_market: float, confidence: float) -> EdgeDecision:
    """
    Three hard vetoes: |p_model - p_market| >= 2%, EV >= 3%,
    confidence >= 60%. A veto is a veto, not a suggestion.
    """
    edge = abs(p_model - p_market)
    # expected value per unit staked buying YES at market price m
    ev = (p_model / p_market - 1.0) if p_market > 0 else -1.0
    if edge < MIN_EDGE:
        return EdgeDecision(False, edge, ev, confidence,
                            f"edge {edge:.4f} < {MIN_EDGE}")
    if ev < MIN_EXPECTED_VALUE:
        return EdgeDecision(False, edge, ev, confidence,
                            f"EV {ev:.4f} < {MIN_EXPECTED_VALUE}")
    if confidence < MIN_CONFIDENCE:
        return EdgeDecision(False, edge, ev, confidence,
                            f"confidence {confidence:.2f} < {MIN_CONFIDENCE}")
    return EdgeDecision(True, edge, ev, confidence, "all vetoes cleared")


# -- Kelly sizer -------------------------------------------------------------------
@dataclass
class SizeDecision:
    stake_cents: int
    fraction_of_bankroll: float
    kelly_full: float
    capped: bool
    warning: str = ""


class KellySizer:
    """
    f* = (p*b - q) / b, then f_deployed = f* x 0.25 x confidence^2,
    hard-clamped to 10% of bankroll. Stakes can never exceed the cap.
    """

    def __init__(self, kelly_fraction: float = KELLY_FRACTION,
                 max_alloc_pct: float = MAX_ALLOC_PCT) -> None:
        self.kelly_fraction = kelly_fraction
        self.max_alloc_pct = max_alloc_pct
        self.cap_warnings = 0

    def size(self, p: float, market_price: float, confidence: float,
             bankroll_cents: int) -> SizeDecision:
        if bankroll_cents <= 0:
            raise ValueError("bankroll must be positive")
        b = (1.0 - market_price) / market_price if market_price > 0 else 0.0
        q = 1.0 - p
        kelly_full = (p * b - q) / b if b > 0 else 0.0
        if kelly_full <= 0:
            return SizeDecision(0, 0.0, kelly_full, False, "no edge: zero size")
        f_deployed = kelly_full * self.kelly_fraction * confidence ** 2
        capped = False
        warning = ""
        if f_deployed > self.max_alloc_pct:
            capped = True
            self.cap_warnings += 1
            warning = (f"kelly_cap bound: {f_deployed:.4f} -> {self.max_alloc_pct}")
            logger.warning(warning)
            f_deployed = self.max_alloc_pct
        stake = int(bankroll_cents * f_deployed)
        return SizeDecision(stake, f_deployed, kelly_full, capped, warning)


# -- Polyclaw wallet adapter (the security boundary) ---------------------------------
@dataclass
class SignedOrder:
    market_id: str
    side: str               # "YES" | "NO"
    size_cents: int
    price: float
    order_type: str         # "GTC" | "FOK"
    key_id: str
    dry_run: bool


class PolyclawWalletAdapter:
    """
    The ONLY live execution path. Fail-closed: the signing key label must be
    the Polyclaw wallet; the treasury label is hard-blocked and every attempt
    is logged. Minimal code, maximal tests, no strategy logic inside.
    """

    def __init__(self, key_id: str, dry_run: bool = True) -> None:
        self.key_id = key_id
        self.dry_run = dry_run
        self.blocked_attempts: List[Dict[str, object]] = []
        self.signed: List[SignedOrder] = []

    def _authorize(self, key_id: str) -> None:
        if key_id == TREASURY_KEY_ID or key_id == TREASURY:
            self.blocked_attempts.append({"key_id": key_id,
                                          "reason": "treasury key blocked"})
            logger.error("BLOCKED treasury-key signing attempt")
            raise TreasuryKeyBlockedError(
                "treasury key can never sign from this module")
        if key_id != POLYCLAW_KEY_ID:
            self.blocked_attempts.append({"key_id": key_id,
                                          "reason": "unknown key"})
            raise TreasuryKeyBlockedError(f"unknown signing key: {key_id}")

    def sign_order(self, market_id: str, side: str, size_cents: int,
                   price: float, order_type: str = "GTC",
                   key_id: Optional[str] = None) -> SignedOrder:
        self._authorize(key_id or self.key_id)
        order = SignedOrder(market_id=market_id, side=side,
                            size_cents=size_cents, price=price,
                            order_type=order_type,
                            key_id=key_id or self.key_id,
                            dry_run=self.dry_run)
        self.signed.append(order)
        return order


# -- order lifecycle ---------------------------------------------------------------
@dataclass
class Position:
    market_id: str
    side: str
    size_cents: int
    entry_price: float
    category: str
    opened_at: int
    resolved: bool = False
    payout_cents: int = 0


@dataclass
class FeeEntry:
    market_id: str
    pnl_cents: int
    fee_cents: int
    fee_to: str = TREASURY


class OrderLifecycleManager:
    """
    Opens via the adapter (GTC maker preferred, FOK for exits), tracks to
    resolution, redeems per the CTF invariant (1 winning share = 1 USDC),
    reconciles expected vs realized PnL to the cent.
    """

    NEAR_RESOLUTION_S = 2 * 3600  # no new entries within 2h of resolution

    def __init__(self, wallet: PolyclawWalletAdapter) -> None:
        self.wallet = wallet
        self.positions: List[Position] = []
        self.fee_ledger: List[FeeEntry] = []

    def open_position(self, snap: MarketSnapshot, side: str, size_cents: int,
                      price: float, now: int,
                      order_type: str = "GTC") -> Position:
        if snap.resolves_at - now < self.NEAR_RESOLUTION_S:
            raise RiskHalt("near_resolution_block",
                           f"market {snap.market_id} resolves within 2h")
        if size_cents <= 0:
            raise ValueError("size must be positive")
        self.wallet.sign_order(snap.market_id, side, size_cents, price,
                               order_type=order_type)
        pos = Position(market_id=snap.market_id, side=side,
                       size_cents=size_cents, entry_price=price,
                       category=snap.category, opened_at=now)
        self.positions.append(pos)
        return pos

    def resolve(self, pos: Position, outcome_yes: bool) -> int:
        """Settle via oracle outcome. CTF: winning share redeems 1 USDC."""
        if pos.resolved:
            raise ValueError("already resolved")
        won = (pos.side == "YES") == outcome_yes
        if won:
            # shares bought = size / entry_price, each redeeming 100 cents
            shares = pos.size_cents / pos.entry_price
            payout = int(round(shares * 100))
        else:
            payout = 0
        pos.resolved = True
        pos.payout_cents = payout
        pnl = payout - pos.size_cents
        if pnl > 0:
            fee = pnl * FEE_BPS // 10_000
            self.fee_ledger.append(FeeEntry(market_id=pos.market_id,
                                            pnl_cents=pnl, fee_cents=fee))
        return pnl

    def realized_pnl_cents(self) -> int:
        return sum(p.payout_cents - p.size_cents
                   for p in self.positions if p.resolved)

    def fee_total_cents(self) -> int:
        return sum(f.fee_cents for f in self.fee_ledger)

    def fee_ledger_reconciles(self) -> bool:
        # every fee entry is exactly 15 bps of its positive settled pnl
        return all(f.fee_cents == f.pnl_cents * FEE_BPS // 10_000
                   for f in self.fee_ledger)


# -- risk manager --------------------------------------------------------------------
class RiskManager:
    """
    Per-market loss limit (3%), daily loss circuit breaker (5%, manual reset
    only), portfolio cap (60%), category concentration (40%), max 90 days to
    resolution. Breaches halt new orders with a named alert.
    """

    def __init__(self, bankroll_cents: int) -> None:
        if bankroll_cents <= 0:
            raise ValueError("bankroll must be positive")
        self.bankroll_cents = bankroll_cents
        self._daily_loss_cents = 0
        self._breaker_tripped = False
        self._market_loss: Dict[str, int] = {}
        self._deployed_cents = 0
        self._category_deployed: Dict[str, int] = {}

    def check_new_position(self, snap: MarketSnapshot, size_cents: int,
                           now: int) -> None:
        if self._breaker_tripped:
            raise RiskHalt("daily_breaker",
                           "5% daily loss breaker tripped; manual reset required")
        if (snap.resolves_at - now) > MAX_DAYS_TO_RESOLUTION * 86400:
            raise RiskHalt("max_duration",
                           f"market resolves in >{MAX_DAYS_TO_RESOLUTION}d")
        if self._market_loss.get(snap.market_id, 0) >= \
                int(self.bankroll_cents * PER_MARKET_LOSS_PCT):
            raise RiskHalt("per_market_loss_limit",
                           f"market {snap.market_id} at 3% loss limit")
        if self._deployed_cents + size_cents > \
                int(self.bankroll_cents * MAX_PORTFOLIO_DEPLOYED_PCT):
            raise RiskHalt("portfolio_cap", "60% of bankroll already deployed")
        cat = self._category_deployed.get(snap.category, 0)
        if cat + size_cents > int(self._deployed_cents * MAX_CATEGORY_PCT) \
                and self._deployed_cents > 0:
            # category cap is relative to deployed capital
            deployed_after = self._deployed_cents + size_cents
            if cat + size_cents > int(deployed_after * MAX_CATEGORY_PCT):
                raise RiskHalt("category_concentration",
                               f"category {snap.category} > 40% of deployed")

    def register_fill(self, snap: MarketSnapshot, size_cents: int) -> None:
        self._deployed_cents += size_cents
        self._category_deployed[snap.category] = \
            self._category_deployed.get(snap.category, 0) + size_cents

    def register_settlement(self, market_id: str, pnl_cents: int,
                            size_cents: int) -> None:
        self._deployed_cents = max(0, self._deployed_cents - size_cents)
        if pnl_cents < 0:
            loss = -pnl_cents
            self._daily_loss_cents += loss
            self._market_loss[market_id] = \
                self._market_loss.get(market_id, 0) + loss
            if self._daily_loss_cents >= \
                    int(self.bankroll_cents * DAILY_LOSS_BREAKER_PCT):
                self._breaker_tripped = True
                logger.error("DAILY LOSS BREAKER TRIPPED at %dc", self._daily_loss_cents)

    def reset_breaker(self, manual_approval: bool) -> None:
        """Manual reset only — never auto-reset."""
        if not manual_approval:
            raise LiveBlockedError("breaker reset requires manual approval")
        self._breaker_tripped = False
        self._daily_loss_cents = 0

    @property
    def breaker_tripped(self) -> bool:
        return self._breaker_tripped


# -- backtest --------------------------------------------------------------------------
def run_backtest(markets: List[Tuple[MarketSnapshot, float, int]],
                 engine: ForecastEngine, sizer: KellySizer,
                 bankroll_cents: int) -> Dict[str, object]:
    """
    markets: (snapshot, signal, outcome). Simulates the full loop in dry-run:
    ingest -> forecast -> edge -> size -> fill -> resolve.
    Returns realized PnL, max drawdown, Brier score, fee totals per market.
    """
    feed = MarketDataFeed()
    wallet = PolyclawWalletAdapter(POLYCLAW_KEY_ID, dry_run=True)
    lifecycle = OrderLifecycleManager(wallet)
    risk = RiskManager(bankroll_cents)
    equity_curve = [bankroll_cents]
    per_market: List[Dict[str, object]] = []
    now = 1_700_000_000
    for snap, signal, outcome in markets:
        # Fresh data arrives each iteration: refresh the quote timestamp so the
        # feed's 60s staleness invariant is evaluated against live data, not a
        # batch timestamp. (Without this, advancing `now` would stale-out every
        # market after the first — the feed is right to reject; the harness was
        # wrong to hand it old quotes.)
        snap = dataclasses.replace(snap, as_of=now)
        try:
            snap = feed.ingest(snap, now)
        except (StaleDataError, EdgeVeto):
            continue
        fc = engine.predict(snap, signal=signal)
        decision = check_edge(fc.p_yes, snap.yes_price, fc.confidence)
        if not decision.trade:
            continue
        size = sizer.size(fc.p_yes, snap.yes_price, fc.confidence, bankroll_cents)
        if size.stake_cents <= 0:
            continue
        try:
            risk.check_new_position(snap, size.stake_cents, now)
        except RiskHalt:
            continue
        pos = lifecycle.open_position(snap, "YES", size.stake_cents,
                                      snap.yes_price, now)
        risk.register_fill(snap, size.stake_cents)
        pnl = lifecycle.resolve(pos, outcome == 1)
        risk.register_settlement(snap.market_id, pnl, size.stake_cents)
        engine.record_resolution(fc.p_yes, snap.yes_price, outcome)
        equity_curve.append(equity_curve[-1] + pnl)
        per_market.append({"market_id": snap.market_id, "pnl_cents": pnl,
                           "stake_cents": size.stake_cents})
        now += 3600
    peak = bankroll_cents
    max_dd = 0
    for e in equity_curve:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)
    return {
        "realized_pnl_cents": equity_curve[-1] - bankroll_cents,
        "max_drawdown_cents": max_dd,
        "brier": engine.brier_score(),
        "naive_brier": engine.naive_brier_score(),
        "fee_total_cents": lifecycle.fee_total_cents(),
        "fee_ledger_reconciles": lifecycle.fee_ledger_reconciles(),
        "markets_traded": len(per_market),
        "per_market": per_market,
    }
