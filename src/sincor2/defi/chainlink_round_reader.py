"""SINCOR DeFi P17 — Chainlink historical round reader.

Read-only client for Chainlink AggregatorV3Interface ``getRoundData``:
given an explicit ``roundId`` it returns the round's price, timestamp and
``answeredInRound`` so the P17 options sim can price / settle against
historical Chainlink rounds (backtests, expiry settlement at a pinned
round).

Feed addresses are NEVER invented here. They come either from the
constructor's ``feed_address`` parameter or from the repo-pinned
:py:data:`~sincor2.defi.price_oracle.BASE_MAINNET_FEEDS` map via
:meth:`ChainlinkRoundReader.for_asset` (sourcing documented in
``docs/ops/CHAINLINK_FEED_PINS.md``). An unpinned asset (e.g. BTC/USD)
fails closed at construction time.

Safety properties (fail-closed by design):
- transport performs ``eth_call`` at "latest" only: read-only, never
  signs, never sends a transaction, never touches keys.
- ``round_id == 0`` is rejected (no round zero on Chainlink aggregators).
- A reverted / non-existent round shows ``answeredInRound == 0`` ->
  invalid.
- A superseded round (``answeredInRound != roundId``) -> stale.
- ``updatedAt`` older than ``max_age_s`` -> stale; ``updatedAt == 0``
  or ``answer <= 0`` -> invalid.
- Any RPC/transport error, short or undecodable response, or a timestamp
  beyond the clock-skew tolerance raises — never a guessed price.

DRY_RUN posture inherited from the P17 reference build: this module only
READS chain state for simulation inputs; it cannot execute anything.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

#: ``getRoundData(uint80)`` — first 4 bytes of
#: keccak256("getRoundData(uint80)"); cross-checked against
#: ``Web3.keccak`` in ``tests/pytest/test_p17_round_reader.py``.
_GET_ROUND_DATA_SELECTOR = bytes.fromhex("9a6fc8f5")
#: ``decimals()`` — kept local so this module never requires web3 at
#: import time (web3 is only imported when the default transport runs).
_DECIMALS_SELECTOR = bytes.fromhex("313ce567")

#: getRoundData return layout:
#: (uint80 roundId, int256 answer, uint256 startedAt,
#:  uint256 updatedAt, uint80 answeredInRound).
_ROUND_DATA_ABI_TYPES = ["uint80", "int256", "uint256", "uint256",
                         "uint80"]
_ROUND_DATA_MIN_LEN = 32 * 5
_WORD = 32
_CLOCK_SKEW_TOLERANCE_S = 60


class RoundReaderError(RuntimeError):
    """Base error for the historical round reader."""


class InvalidRound(RoundReaderError):
    """The round is unusable: roundId 0, reverted (answeredInRound == 0),
    zero/non-positive updatedAt, non-positive answer, bad decode."""


class StaleRound(RoundReaderError):
    """The round is superseded (answeredInRound != roundId) or its
    updatedAt is older than ``max_age_s``."""


@dataclass(frozen=True)
class RoundData:
    """Validated historical Chainlink round, scaled to wei (1e18)."""
    round_id: int
    price_wei: int          # > 0, answer scaled from feed decimals to 1e18
    started_at: float
    timestamp: float        # updatedAt
    answered_in_round: int


def _default_eth_call_transport(rpc_url: str, to_address: str,
                                calldata: bytes, timeout_s: float) -> bytes:
    """Default eth_call transport over a JSON-RPC endpoint.

    Read-only: ``eth_call`` at "latest", never sends a transaction,
    never signs, never touches keys. Raises on any failure.
    """
    try:
        from web3 import Web3
    except ImportError as exc:
        raise RoundReaderError(
            "web3 is required for live eth_call reads") from exc
    w3 = Web3(Web3.HTTPProvider(
        rpc_url, request_kwargs={"timeout": timeout_s}))
    try:
        raw = w3.eth.call(
            {"to": to_address, "data": "0x" + calldata.hex()}, "latest")
    except Exception as exc:
        raise RoundReaderError(f"eth_call failed for {to_address}: "
                               f"{type(exc).__name__}") from exc
    if not raw:
        raise RoundReaderError(f"eth_call returned empty data for "
                               f"{to_address} (no code at address or "
                               f"wrong contract)")
    return bytes(raw)


class ChainlinkRoundReader:
    """Read-only historical round reader for a Chainlink aggregator.

    ``transport(rpc_url, to_address, calldata) -> bytes`` is injected;
    production uses the default eth_call transport, tests inject fakes.
    """

    def __init__(
        self,
        rpc_url: str,
        feed_address: str,
        max_age_s: float = 2 * 3600,
        timeout_s: float = 10.0,
        transport: Optional[Callable[[str, str, bytes], bytes]] = None,
    ) -> None:
        if not rpc_url:
            raise RoundReaderError("rpc_url is required")
        if not feed_address:
            raise RoundReaderError("feed_address is required")
        if max_age_s <= 0:
            raise RoundReaderError("max_age_s must be positive")
        try:
            from web3 import Web3
        except ImportError as exc:
            raise RoundReaderError(
                "web3 is required for live eth_call reads") from exc
        try:
            checksummed = Web3.to_checksum_address(feed_address)
        except Exception as exc:
            raise RoundReaderError(
                f"invalid feed address: {feed_address}") from exc
        self._rpc_url = rpc_url
        self._feed = checksummed
        self._max_age_s = max_age_s
        self._timeout_s = timeout_s
        self._transport = (transport if transport is not None
                           else lambda u, a, d: _default_eth_call_transport(
                               u, a, d, timeout_s))
        self._decimals = self._fetch_decimals(checksummed)
        self.calls = 0
        self.errors = 0
        self.last_error: Optional[str] = None

    @classmethod
    def for_asset(
        cls,
        asset: str,
        rpc_url: str,
        max_age_s: float = 2 * 3600,
        timeout_s: float = 10.0,
        transport: Optional[Callable[[str, str, bytes], bytes]] = None,
        feeds: Optional[Dict[str, str]] = None,
    ) -> "ChainlinkRoundReader":
        """Build a reader using the repo-pinned feed map.

        ``feeds`` defaults to
        :py:data:`~sincor2.defi.price_oracle.BASE_MAINNET_FEEDS` (Base
        mainnet pins, documented in ``docs/ops/CHAINLINK_FEED_PINS.md``).
        An asset with no pin (e.g. BTC/USD) fails closed at construction.
        """
        if feeds is None:
            from sincor2.defi import price_oracle
            feeds = price_oracle.BASE_MAINNET_FEEDS
        try:
            feed_address = feeds[asset]
        except (KeyError, TypeError) as exc:
            raise RoundReaderError(
                f"no pinned Chainlink feed for asset {asset!r}: "
                f"feed address refused to be invented") from exc
        return cls(rpc_url, feed_address, max_age_s=max_age_s,
                   timeout_s=timeout_s, transport=transport)

    @property
    def feed_address(self) -> str:
        return self._feed

    @property
    def decimals(self) -> int:
        return self._decimals

    def _fetch_decimals(self, address: str) -> int:
        try:
            raw = self._transport(self._rpc_url, address,
                                  _DECIMALS_SELECTOR)
        except Exception as exc:
            raise RoundReaderError(
                f"decimals() call failed for {address}: "
                f"{type(exc).__name__}") from exc
        if len(raw) < _WORD:
            raise RoundReaderError(
                f"decimals() returned short data for {address}")
        decimals = int.from_bytes(raw[-_WORD:], "big")
        if decimals <= 0 or decimals > 18:
            raise RoundReaderError(
                f"decimals() out of range for {address}: {decimals}")
        return decimals

    def read_round(self, round_id: int,
                   now: Optional[float] = None) -> RoundData:
        """Fetch and validate one historical round.

        Raises :class:`InvalidRound` (roundId 0, reverted round,
        non-positive price, bad timestamps/decode) or
        :class:`StaleRound` (superseded or too-old round). Any transport
        failure raises :class:`RoundReaderError`. Never returns a guess.
        """
        if not isinstance(round_id, int) or round_id <= 0:
            raise InvalidRound(f"round_id must be a positive integer, "
                               f"got {round_id!r}")
        now = time.time() if now is None else now
        calldata = _GET_ROUND_DATA_SELECTOR + \
            round_id.to_bytes(_WORD, "big")
        self.calls += 1
        try:
            raw = self._transport(self._rpc_url, self._feed, calldata)
        except Exception as exc:
            self.errors += 1
            self.last_error = f"eth_call failed: {type(exc).__name__}"
            raise RoundReaderError(
                f"eth_call getRoundData({round_id}) failed for "
                f"{self._feed}: {type(exc).__name__}") from exc
        if len(raw) < _ROUND_DATA_MIN_LEN:
            self.errors += 1
            self.last_error = "short getRoundData response"
            raise InvalidRound(
                f"getRoundData({round_id}) returned {len(raw)} bytes "
                f"(< {_ROUND_DATA_MIN_LEN})")
        try:
            from eth_abi import decode
            (rid, answer, started_at, updated_at,
             answered_in_round) = decode(_ROUND_DATA_ABI_TYPES, raw)
        except Exception as exc:
            self.errors += 1
            self.last_error = f"getRoundData decode failed: {exc}"
            raise InvalidRound(
                f"could not decode getRoundData({round_id})") from exc

        if answered_in_round == 0:
            self.errors += 1
            self.last_error = f"round {round_id}: answeredInRound == 0"
            raise InvalidRound(
                f"round {round_id} has answeredInRound == 0 "
                f"(reverted / non-existent round)")
        if answered_in_round != rid:
            self.errors += 1
            self.last_error = (f"round {round_id}: answeredInRound "
                               f"{answered_in_round} != roundId {rid}")
            raise StaleRound(
                f"round {round_id} superseded: answeredInRound "
                f"{answered_in_round} != roundId {rid}")
        if updated_at <= 0:
            self.errors += 1
            self.last_error = f"round {round_id}: updatedAt == 0"
            raise InvalidRound(f"round {round_id} has updatedAt == 0")
        if updated_at > now + _CLOCK_SKEW_TOLERANCE_S:
            self.errors += 1
            self.last_error = (f"round {round_id}: updatedAt "
                               f"{updated_at} in the future")
            raise InvalidRound(
                f"round {round_id} updatedAt {updated_at} is in the "
                f"future (beyond clock-skew tolerance)")
        if now - updated_at > self._max_age_s:
            self.errors += 1
            self.last_error = (f"round {round_id}: age "
                               f"{now - updated_at:.0f}s exceeds "
                               f"{self._max_age_s:.0f}s")
            raise StaleRound(
                f"round {round_id} is stale: updatedAt "
                f"{now - updated_at:.0f}s ago exceeds max_age_s "
                f"{self._max_age_s:.0f}s")
        if answer <= 0:
            self.errors += 1
            self.last_error = f"round {round_id}: answer {answer} <= 0"
            raise InvalidRound(f"round {round_id} has non-positive "
                               f"answer {answer}")
        price_wei = int(answer) * (10 ** (18 - self._decimals))
        if price_wei <= 0:
            self.errors += 1
            self.last_error = f"round {round_id}: scaled price <= 0"
            raise InvalidRound(f"round {round_id} scaled price_wei "
                               f"non-positive")
        return RoundData(round_id=rid, price_wei=price_wei,
                         started_at=float(started_at),
                         timestamp=float(updated_at),
                         answered_in_round=answered_in_round)

    def price_feed(self, round_id: int,
                   now: Optional[float] = None):
        """Consume a historical round as a P17
        :class:`~sincor2.defi.options_protocol.PriceFeed`.

        Glue between the reader and the options sim: the returned
        ``PriceFeed`` carries the round's scaled price (wei) and its
        ``updatedAt`` timestamp, so the sim's existing staleness guard
        (:meth:`PriceFeed.get_spot`) applies unchanged.
        """
        from sincor2.defi.options_protocol import PriceFeed
        rnd = self.read_round(round_id, now=now)
        return PriceFeed(spot_wei=rnd.price_wei, updated_at=rnd.timestamp)

    def stats(self) -> Dict[str, object]:
        return {"calls": self.calls, "errors": self.errors,
                "last_error": self.last_error}
