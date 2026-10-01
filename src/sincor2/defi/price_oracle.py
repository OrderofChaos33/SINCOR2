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


# -- live Chainlink eth_call reader --------------------------------------------

# EVM function selectors: first 4 bytes of keccak256 of the canonical
# signature. Hardcoded so importing this module never requires web3;
# cross-checked against Web3.keccak(text=...) in the live-read test file.
_LATEST_ROUND_DATA_SELECTOR = bytes.fromhex("feaf968c")  # latestRoundData()
_DECIMALS_SELECTOR = bytes.fromhex("313ce567")           # decimals()

_LATEST_ROUND_DATA_ABI = ["uint80", "int256", "uint256", "uint256", "uint80"]
_ROUND_DATA_MIN_LEN = 32 * 5  # five ABI slots: roundId, answer, startedAt,
                              # updatedAt, answeredInRound

#: Verified Chainlink aggregator proxies on Base mainnet (chain id 8453).
#: Asset symbol (as in PRODUCT_PRICE_NEEDS) -> proxy address.
#: Verified 2026-09-29; full sourcing in docs/ops/CHAINLINK_FEED_PINS.md.
#: BTC/USD is deliberately NOT pinned: independent sources disagree on its
#: Base address, so it stays unset rather than guessed.
BASE_MAINNET_FEEDS: Dict[str, str] = {
    "ETH/USD": "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
    "USDC/USD": "0x7e860098F58bBFC8648a4311b374B1D669a2bc6B",
}


def _default_eth_call_transport(rpc_url: str, to_address: str,
                                calldata: bytes, timeout_s: float) -> bytes:
    """Default eth_call transport over a JSON-RPC endpoint.

    Read-only: performs eth_call at "latest", never sends a transaction,
    never signs, never touches keys. Raises on any failure so the reader
    can fail closed.
    """
    try:
        from web3 import Web3
    except ImportError as exc:
        raise OracleError(
            "web3 is required for live eth_call reads") from exc
    w3 = Web3(Web3.HTTPProvider(
        rpc_url, request_kwargs={"timeout": timeout_s}))
    try:
        raw = w3.eth.call(
            {"to": to_address, "data": "0x" + calldata.hex()}, "latest")
    except Exception as exc:
        raise OracleError(f"eth_call failed for {to_address}: "
                          f"{type(exc).__name__}") from exc
    if not raw:
        raise OracleError(f"eth_call returned empty data for {to_address} "
                          f"(no code at address or wrong contract)")
    return bytes(raw)


class EthCallChainlinkReader:
    """Read-only live reader for Chainlink aggregator proxies.

    ``read = make_eth_call_reader(rpc_url, feeds)`` returns an instance
    whose ``__call__(asset)`` matches :class:`ChainlinkFeedAdapter`'s
    injected ``read_round(asset)`` signature, returning a
    :class:`ChainlinkRound` or ``None``.

    Construction performs one eth_call per feed to fetch ``decimals()``
    (needed to normalize answers into the oracle's PRICE_FP scale). Any
    failure there raises :class:`OracleError`: a reader is never built
    with unverified scaling.

    Every read is fail-closed: any RPC/transport error, short or
    undecodable response, stale round (``answered_in_round != round_id``),
    non-positive ``updated_at``/``answer``, or a timestamp in the future
    beyond CLOCK_SKEW_TOLERANCE_S yields ``None`` (unknown), never a
    guessed price. Callers (e.g. :class:`PriceOracle`) treat ``None`` as
    "no source" and raise on insufficient sources.

    Honest limits: reads are at "latest" (no block pinning), the L2
    sequencer-uptime feed is NOT checked (Base-specific gap documented in
    docs/ops/CHAINLINK_FEED_PINS.md), there is no alerting on errors --
    use :meth:`stats` for that -- and feed addresses are only as good as
    the pinned map (see BASE_MAINNET_FEEDS).
    """

    def __init__(self,
                 rpc_url: str,
                 feeds: Dict[str, str],
                 timeout_s: float = 10.0,
                 transport: Optional[Callable[[str, str, bytes], bytes]]
                 = None) -> None:
        if not rpc_url:
            raise OracleError("rpc_url is required")
        if not feeds:
            raise OracleError("at least one asset->feed mapping is required")
        try:
            from web3 import Web3
        except ImportError as exc:
            raise OracleError(
                "web3 is required for live eth_call reads") from exc
        self._rpc_url = rpc_url
        self._timeout_s = timeout_s
        self._transport = (transport if transport is not None
                           else lambda u, a, d: _default_eth_call_transport(
                               u, a, d, timeout_s))
        self._feeds: Dict[str, str] = {}
        self._decimals: Dict[str, int] = {}
        for asset, address in feeds.items():
            try:
                checksummed = Web3.to_checksum_address(address)
            except Exception as exc:
                raise OracleError(
                    f"invalid feed address for {asset}: {address}") from exc
            decimals = self._fetch_decimals(checksummed, asset)
            self._feeds[asset] = checksummed
            self._decimals[asset] = decimals
        self.calls = 0
        self.errors = 0
        self.last_error: Optional[str] = None

    def _fetch_decimals(self, address: str, asset: str) -> int:
        try:
            raw = self._transport(self._rpc_url, address,
                                  _DECIMALS_SELECTOR)
        except Exception as exc:
            raise OracleError(
                f"decimals() fetch failed for {asset} @ {address}: "
                f"{type(exc).__name__}: {exc}") from exc
        if len(raw) != 32:
            raise OracleError(
                f"decimals() returned {len(raw)} bytes for {asset} "
                f"@ {address} (expected 32)")
        decimals = int.from_bytes(raw, "big")
        if not 1 <= decimals <= 36:
            raise OracleError(
                f"decimals() = {decimals} for {asset} @ {address} is "
                f"outside the sane range 1..36")
        return decimals

    def _fail(self, what: str) -> None:
        self.errors += 1
        self.last_error = what

    def __call__(self, asset: str) -> Optional[ChainlinkRound]:
        """Perform eth_call latestRoundData() and validate the round."""
        address = self._feeds.get(asset)
        if address is None:
            return None  # no feed pinned for this asset
        self.calls += 1
        try:
            raw = self._transport(self._rpc_url, address,
                                  _LATEST_ROUND_DATA_SELECTOR)
        except Exception as exc:
            self._fail(f"eth_call failed for {asset}: "
                       f"{type(exc).__name__}: {exc}")
            return None
        if len(raw) < _ROUND_DATA_MIN_LEN:
            self._fail(f"{asset}: short response ({len(raw)} bytes)")
            return None
        try:
            from eth_abi import decode
            (round_id, answer, started_at, updated_at,
             answered_in_round) = decode(_LATEST_ROUND_DATA_ABI,
                                         raw[:_ROUND_DATA_MIN_LEN])
        except Exception as exc:
            self._fail(f"{asset}: undecodable round data: "
                       f"{type(exc).__name__}")
            return None
        # Same validation the ChainlinkFeedAdapter documents, applied at
        # the read seam too so a directly-used reader is equally safe.
        if answered_in_round != round_id:
            self._fail(f"{asset}: stale round (answered_in_round "
                       f"{answered_in_round} != round_id {round_id})")
            return None
        if updated_at <= 0 or answer <= 0:
            self._fail(f"{asset}: non-positive updated_at/answer")
            return None
        if updated_at > time.time() + CLOCK_SKEW_TOLERANCE_S:
            self._fail(f"{asset}: future updated_at {updated_at}")
            return None
        return ChainlinkRound(int(round_id), int(answer),
                              self._decimals[asset], float(started_at),
                              float(updated_at), int(answered_in_round))

    def stats(self) -> Dict[str, Any]:
        """Operational counters for future monitoring hooks."""
        return {"calls": self.calls, "errors": self.errors,
                "last_error": self.last_error,
                "feeds": dict(self._feeds)}


def make_eth_call_reader(rpc_url: str,
                         feeds: Dict[str, str],
                         timeout_s: float = 10.0,
                         transport: Optional[
                             Callable[[str, str, bytes], bytes]] = None
                         ) -> EthCallChainlinkReader:
    """Build a read-only live Chainlink round reader.

    ``rpc_url`` is a JSON-RPC HTTPS endpoint; ``feeds`` maps asset symbol
    (e.g. "ETH/USD") to the aggregator proxy address -- see
    BASE_MAINNET_FEEDS. ``transport`` is injectable for tests; it takes
    ``(rpc_url, to_address, calldata)`` and returns raw bytes, raising on
    any failure. The default transport uses web3 eth_call at "latest".

    PURELY READ-ONLY: no transactions, no signing, no keys -- ever.
    """
    return EthCallChainlinkReader(rpc_url, feeds, timeout_s=timeout_s,
                                  transport=transport)
