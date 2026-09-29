"""Shared price oracle for the SINCOR Speculative DeFi arm.

Single price-feed dependency for all 26 DeFi products. Nearly every product
needs real prices (NAV, liquidation, rebalancing, settlement) to ever work
end-to-end; one careful shared oracle beats 26 ad-hoc price hacks.

Security model (price manipulation drains funds, so this is built like it):
- Pluggable adapters behind one interface: a deterministic
  :class:`ReferenceAdapter` for tests/simulations, plus interfaces shaped for
  real feeds (:class:`ChainlinkFeedAdapter` mirroring Chainlink
  ``AggregatorV3Interface.latestRoundData``, :class:`DexTwapAdapter` for
  Uniswap-V3-style TWAP reads). Reference data is labeled ``reference`` —
  nothing here pretends to be a live feed.
- Multi-source median aggregation; per-asset staleness guard (stale prices
  are rejected, never silently used); deviation guard (warn at
  ``deviation_warn_bps``, halt at ``deviation_halt_bps``); circuit breaker
  (persistent anomaly halts the asset); fail-closed everywhere (no price ->
  no action, always an exception, never a guess).
- Audit trail: every publication and every guard trip is appended to a
  hash-chained (sha256, defi-arm convention) log with ``verify_chain()``.
- Money math is integer-only. Prices are integer ``price_fp`` with
  ``PRICE_FP = 10**8`` scale (same convention as
  :mod:`sincor2.defi.perp_hedge_swarm`), quoted in the asset pair's quote
  currency (e.g. ``"ETH/USD"``).

Safety rules (hard):
- Default mode is DRY_RUN. The oracle *publishes* prices; it never acts on
  them. Nothing here touches a chain, a wallet, or funds. Consumers must call
  :meth:`PriceOracle.get_price` (or ``strict=True``) and treat any raised
  :class:`OracleError` as "do not act".
- Production deployment still needs: real Chainlink/Pyth feed addresses,
  an eth_call read path injected into :class:`ChainlinkFeedAdapter`, real
  DEX TWAP readers injected into :class:`DexTwapAdapter`, per-asset tuning
  of staleness/deviation parameters, and monitoring on the audit log.
  This module is the interface + the guards, not the live feeds.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

DRY_RUN = True  # the oracle publishes; it never executes.

PRICE_FP = 10**8  # fixed-point price scale (matches perp_hedge_swarm).

CLOCK_SKEW_TOLERANCE_S = 60.0  # feed points newer than now+this are rejected.

# Products that need external price feeds, mapped to the assets they consume.
# Surveyed 2026-09-29 against src/sincor2/defi/*. The 9 products NOT listed
# need no external price feed: P04 (order-quoted prices only), P05 (risk
# scores, not asset prices), P09 (fixture-injected vote price), P20
# (compliance signals), P21 (allocation bps), P22 (APR quotes, not asset
# prices), P23 (dedicated NFT-floor oracle, different asset class),
# P24 (internal bonding curve), P26 (meta layer).
PRODUCT_PRICE_NEEDS: Dict[str, List[str]] = {
    "P01_YIELD_AGG": ["ETH/USD", "BTC/USD", "USDC/USD"],
    "P02_CLMM": ["ETH/USD", "USDC/USD"],
    "P03_INTENT_DARK": ["ETH/USD", "BTC/USD"],
    "P06_PERPS": ["ETH/USD", "BTC/USD"],
    "P07_BRIDGE": ["ETH/USD", "USDC/USD"],
    "P08_RWA": ["USDC/USD"],
    "P10_FLASH_ARB": ["ETH/USD", "BTC/USD", "USDC/USD"],
    "P11_DELTA_NEUTRAL": ["ETH/USD", "BTC/USD"],
    "P12_TWAMM": ["ETH/USD"],
    "P13_AVS": ["ETH/USD"],
    "P14_PREDICTION": ["ETH/USD", "BTC/USD"],
    "P15_LENDING": ["ETH/USD", "BTC/USD", "USDC/USD"],
    "P16_DEX_AGG": ["ETH/USD", "BTC/USD", "USDC/USD"],
    "P17_OPTIONS": ["ETH/USD", "BTC/USD"],
    "P18_STRUCTURED": ["ETH/USD", "BTC/USD"],
    "P19_CREDIT": ["ETH/USD", "BTC/USD"],
    "P25_PORTFOLIO": ["ETH/USD", "BTC/USD", "USDC/USD"],
}


# -- errors -------------------------------------------------------------------

class OracleError(Exception):
    """Base oracle failure. Fail-closed: callers must treat as 'do not act'."""


class NoPriceAvailable(OracleError):
    """Fewer than min_sources fresh readings: no price exists."""


class PriceStale(OracleError):
    """Last publication is older than max_age_s: refuse to serve it."""


class PriceHalted(OracleError):
    """Circuit breaker tripped for this asset: price-dependent action blocked."""


class PriceDegraded(OracleError):
    """Strict read on a deviation-warned (degraded) publication."""


# -- data ---------------------------------------------------------------------

@dataclass(frozen=True)
class PricePoint:
    source_id: str
    asset: str
    price_fp: int  # integer, PRICE_FP scale, must be > 0
    ts: float
    kind: str  # "reference" | "chainlink" | "dex_twap" | ...


@dataclass(frozen=True)
class Publication:
    asset: str
    price_fp: int
    ts: float
    sources: int
    degraded: bool  # deviation warn tripped but below halt


@dataclass(frozen=True)
class PriceResult:
    asset: str
    price_fp: int
    ts: float
    sources: int
    degraded: bool


@dataclass
class AssetConfig:
    max_age_s: float = 300.0
    min_sources: int = 2
    deviation_warn_bps: int = 100  # 1%: flag + mark degraded
    deviation_halt_bps: int = 500  # 5%: trip the circuit breaker
    max_warn_trips: int = 3  # consecutive warns trip the breaker too


# -- adapters ------------------------------------------------------------------

class PriceAdapter(ABC):
    """One price source. Production feeds inject their read path."""

    @property
    @abstractmethod
    def source_id(self) -> str: ...

    @property
    @abstractmethod
    def kind(self) -> str: ...

    @abstractmethod
    def fetch(self, asset: str, now: float) -> Optional[PricePoint]:
        """Return the latest known point for asset, or None if unknown."""


class ReferenceAdapter(PriceAdapter):
    """Deterministic reference/test adapter. kind='reference' — never live.

    Scripted prices for tests and dry-run simulations. Every point it emits
    is labeled reference so no consumer can mistake it for a market feed.
    """

    def __init__(self, source_id: str = "reference-1") -> None:
        self._source_id = source_id
        self._series: Dict[str, List[PricePoint]] = {}

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def kind(self) -> str:
        return "reference"

    def set_price(self, asset: str, price_fp: int, ts: float) -> None:
        if price_fp <= 0:
            raise OracleError("reference price must be positive")
        self._series.setdefault(asset, []).append(
            PricePoint(self._source_id, asset, price_fp, ts, self.kind))

    def fetch(self, asset: str, now: float) -> Optional[PricePoint]:
        pts = [p for p in self._series.get(asset, []) if p.ts <= now]
        return max(pts, key=lambda p: p.ts) if pts else None


@dataclass(frozen=True)
class ChainlinkRound:
    """Mirrors AggregatorV3Interface.latestRoundData()."""
    round_id: int
    answer: int  # raw aggregator answer, > 0
    decimals: int
    started_at: float
    updated_at: float
    answered_in_round: int


class ChainlinkFeedAdapter(PriceAdapter):
    """Interface shaped for a real Chainlink price feed.

    ``read_round(asset)`` is injected: production passes a callable that
    performs eth_call ``latestRoundData()`` against the feed's aggregator
    address and returns a :class:`ChainlinkRound`. Tests inject fakes.

    Validation (real Chainlink best practice, enforced here):
    - ``answered_in_round == round_id`` (stale-round check)
    - ``updated_at > 0`` and ``answer > 0``
    Anything else is dropped, never published.
    """

    def __init__(self, source_id: str,
                 read_round: Callable[[str], Optional[ChainlinkRound]]) -> None:
        self._source_id = source_id
        self._read_round = read_round

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def kind(self) -> str:
        return "chainlink"

    def fetch(self, asset: str, now: float) -> Optional[PricePoint]:
        rnd = self._read_round(asset)
        if rnd is None:
            return None
        if rnd.answered_in_round != rnd.round_id:
            return None  # stale round: a feed that stopped updating
        if rnd.updated_at <= 0 or rnd.answer <= 0:
            return None
        price_fp = rnd.answer * PRICE_FP // (10 ** rnd.decimals)
        if price_fp <= 0:
            return None
        return PricePoint(self._source_id, asset, price_fp,
                          rnd.updated_at, self.kind)


@dataclass(frozen=True)
class TwapReading:
    price_fp: int  # time-weighted average over the window, > 0
    window_start_ts: float
    window_end_ts: float
    observed_ts: float


class DexTwapAdapter(PriceAdapter):
    """Interface shaped for a DEX TWAP feed (Uniswap-V3-style).

    ``read_twap(asset)`` is injected: production passes a callable that
    reads the pool's TWAP (e.g. via ``observe()``) and returns a
    :class:`TwapReading`. Tests inject fakes.
    """

    def __init__(self, source_id: str,
                 read_twap: Callable[[str], Optional[TwapReading]]) -> None:
        self._source_id = source_id
        self._read_twap = read_twap

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def kind(self) -> str:
        return "dex_twap"

    def fetch(self, asset: str, now: float) -> Optional[PricePoint]:
        rd = self._read_twap(asset)
        if rd is None or rd.price_fp <= 0:
            return None
        if rd.window_end_ts < rd.window_start_ts:
            return None
        return PricePoint(self._source_id, asset, rd.price_fp,
                          rd.observed_ts, self.kind)


# -- audit trail ---------------------------------------------------------------

def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class AuditLog:
    """Append-only, sha256 hash-chained log (defi-arm convention).

    Every publication and every guard trip is recorded. ``verify_chain()``
    detects any tampering with recorded entries.
    """

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []
        self._prev_hash = "0" * 64

    def log(self, kind: str, asset: str, detail: Dict[str, Any],
            now: float) -> Dict[str, Any]:
        entry = {
            "seq": len(self._entries),
            "ts": now,
            "kind": kind,
            "asset": asset,
            "detail": detail,
            "prev_hash": self._prev_hash,
        }
        entry_hash = hashlib.sha256(_canonical(entry).encode()).hexdigest()
        entry["entry_hash"] = entry_hash
        self._entries.append(entry)
        self._prev_hash = entry_hash
        return entry

    def verify_chain(self) -> bool:
        prev = "0" * 64
        for i, e in enumerate(self._entries):
            if e.get("seq") != i or e.get("prev_hash") != prev:
                return False
            body = {k: v for k, v in e.items() if k != "entry_hash"}
            if hashlib.sha256(_canonical(body).encode()).hexdigest() != e.get(
                    "entry_hash"):
                return False
            prev = e["entry_hash"]
        return True

    def entries(self, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        if kind is None:
            return list(self._entries)
        return [e for e in self._entries if e["kind"] == kind]

    def __len__(self) -> int:
        return len(self._entries)


# -- oracle --------------------------------------------------------------------

class PriceOracle:
    """Multi-source median price oracle with staleness, deviation, and
    circuit-breaker guards. Fail-closed: every failure mode raises; a price
    is only ever returned when it is fresh, sourced, and unhalted."""

    def __init__(self) -> None:
        self._adapters: List[PriceAdapter] = []
        self._configs: Dict[str, AssetConfig] = {}
        self._publications: Dict[str, Publication] = {}
        self._halted: Dict[str, str] = {}  # asset -> trip reason
        self._warn_trips: Dict[str, int] = {}
        self.audit = AuditLog()

    # -- configuration ------------------------------------------------------
    def add_adapter(self, adapter: PriceAdapter) -> None:
        if any(a.source_id == adapter.source_id for a in self._adapters):
            raise OracleError(f"duplicate source_id: {adapter.source_id}")
        self._adapters.append(adapter)

    def configure(self, asset: str, **kwargs: Any) -> None:
        cfg = self._configs.get(asset, AssetConfig())
        for k, v in kwargs.items():
            if not hasattr(cfg, k):
                raise OracleError(f"unknown asset config key: {k}")
            setattr(cfg, k, v)
        self._configs[asset] = cfg

    def _config(self, asset: str) -> AssetConfig:
        return self._configs.get(asset, AssetConfig())

    def is_halted(self, asset: str) -> bool:
        return asset in self._halted

    def halt_reason(self, asset: str) -> Optional[str]:
        return self._halted.get(asset)

    # -- publication --------------------------------------------------------
    @staticmethod
    def _median(prices: List[int]) -> int:
        s = sorted(prices)
        n = len(s)
        mid = n // 2
        if n % 2 == 1:
            return s[mid]
        return (s[mid - 1] + s[mid]) // 2  # floored once, like P23

    def publish(self, asset: str, now: Optional[float] = None) -> Publication:
        now = now if now is not None else time.time()
        cfg = self._config(asset)
        if self.is_halted(asset):
            self.audit.log("publish_refused_halted", asset,
                           {"reason": self._halted[asset]}, now)
            raise PriceHalted(f"{asset} halted: {self._halted[asset]}")

        points: List[PricePoint] = []
        for adapter in self._adapters:
            try:
                pt = adapter.fetch(asset, now)
            except Exception as exc:  # one bad source must not kill the rest
                self.audit.log("adapter_error", asset,
                               {"source": adapter.source_id,
                                "error": f"{type(exc).__name__}"}, now)
                continue
            if pt is None:
                continue
            if pt.price_fp <= 0 or pt.asset != asset:
                self.audit.log("point_rejected", asset,
                               {"source": adapter.source_id,
                                "reason": "invalid_point"}, now)
                continue
            if pt.ts > now + CLOCK_SKEW_TOLERANCE_S:
                self.audit.log("point_rejected", asset,
                               {"source": adapter.source_id,
                                "reason": "future_timestamp"}, now)
                continue
            if now - pt.ts > cfg.max_age_s:
                self.audit.log("stale_source_dropped", asset,
                               {"source": adapter.source_id,
                                "age_s": round(now - pt.ts, 1)}, now)
                continue
            points.append(pt)

        if len(points) < cfg.min_sources:
            self.audit.log("insufficient_sources", asset,
                           {"fresh": len(points),
                            "required": cfg.min_sources}, now)
            raise NoPriceAvailable(
                f"{asset}: {len(points)} fresh sources < {cfg.min_sources}")

        median = self._median([p.price_fp for p in points])
        lo = min(p.price_fp for p in points)
        hi = max(p.price_fp for p in points)
        deviation_bps = (hi - lo) * 10_000 // median

        if deviation_bps >= cfg.deviation_halt_bps:
            reason = (f"sources diverged {deviation_bps}bps >= "
                      f"halt {cfg.deviation_halt_bps}bps")
            self._trip(asset, reason, now,
                       {"deviation_bps": deviation_bps,
                        "sources": len(points), "median_fp": median})
            raise PriceHalted(f"{asset} halted: {reason}")

        degraded = deviation_bps >= cfg.deviation_warn_bps
        if degraded:
            self._warn_trips[asset] = self._warn_trips.get(asset, 0) + 1
            self.audit.log("deviation_warn", asset,
                           {"deviation_bps": deviation_bps,
                            "warn_bps": cfg.deviation_warn_bps,
                            "consecutive_warns": self._warn_trips[asset],
                            "median_fp": median}, now)
            if self._warn_trips[asset] >= cfg.max_warn_trips:
                reason = (f"{self._warn_trips[asset]} consecutive deviation "
                          f"warns >= {cfg.max_warn_trips}")
                self._trip(asset, reason, now,
                           {"deviation_bps": deviation_bps})
                raise PriceHalted(f"{asset} halted: {reason}")
        else:
            self._warn_trips[asset] = 0

        pub = Publication(asset, median, now, len(points), degraded)
        self._publications[asset] = pub
        self.audit.log("price_published", asset,
                       {"price_fp": median, "sources": len(points),
                        "deviation_bps": deviation_bps,
                        "degraded": degraded}, now)
        return pub

    def _trip(self, asset: str, reason: str, now: float,
              detail: Dict[str, Any]) -> None:
        self._halted[asset] = reason
        self.audit.log("breaker_trip", asset,
                       {"reason": reason, **detail}, now)

    def reset_halt(self, asset: str, reason: str,
                   now: Optional[float] = None) -> None:
        """Manual, reviewed reset of a tripped breaker. Always audited."""
        now = now if now is not None else time.time()
        self._halted.pop(asset, None)
        self._warn_trips[asset] = 0
        self.audit.log("breaker_reset", asset, {"reason": reason}, now)

    # -- reads (fail-closed) --------------------------------------------------
    def get_price(self, asset: str, now: Optional[float] = None,
                  strict: bool = False) -> PriceResult:
        """Return the last published price iff fresh and unhalted.

        ``strict=True`` additionally refuses deviation-warned (degraded)
        publications. Every failure raises: no price -> no action.
        """
        now = now if now is not None else time.time()
        if self.is_halted(asset):
            raise PriceHalted(f"{asset} halted: {self._halted[asset]}")
        pub = self._publications.get(asset)
        if pub is None:
            raise NoPriceAvailable(f"{asset}: never published")
        cfg = self._config(asset)
        if now - pub.ts > cfg.max_age_s:
            raise PriceStale(
                f"{asset}: publication {now - pub.ts:.0f}s old > "
                f"{cfg.max_age_s:.0f}s max")
        if strict and pub.degraded:
            raise PriceDegraded(f"{asset}: publication is degraded")
        return PriceResult(pub.asset, pub.price_fp, pub.ts, pub.sources,
                           pub.degraded)


# -- product wiring ------------------------------------------------------------

class ProductPriceFeed:
    """Per-product view over the shared oracle.

    Bound to one product's declared assets; ``price()`` raises on any
    oracle failure so consumers stay fail-closed without extra checks.
    """

    def __init__(self, oracle: PriceOracle, product_key: str) -> None:
        if product_key not in PRODUCT_PRICE_NEEDS:
            raise KeyError(f"{product_key} declares no price needs")
        self._oracle = oracle
        self._product_key = product_key
        self._assets = list(PRODUCT_PRICE_NEEDS[product_key])

    @property
    def product_key(self) -> str:
        return self._product_key

    @property
    def assets(self) -> List[str]:
        return list(self._assets)

    def price(self, asset: str, now: Optional[float] = None,
              strict: bool = False) -> int:
        if asset not in self._assets:
            raise OracleError(
                f"{self._product_key} did not declare price need for {asset}")
        return self._oracle.get_price(asset, now, strict=strict).price_fp

    def require_all(self, now: Optional[float] = None,
                    strict: bool = False) -> Dict[str, int]:
        return {a: self.price(a, now, strict=strict) for a in self._assets}


def wiring_for(product_key: str, oracle: PriceOracle) -> ProductPriceFeed:
    """Bind the shared oracle to one product's declared price needs."""
    return ProductPriceFeed(oracle, product_key)
