"""Tests for the Chainlink historical round reader (P17).

Hermetic: ALL network is replaced by a fake transport injected into
ChainlinkRoundReader. No committed test here may touch a real RPC
endpoint. Feed addresses come only from the repo-pinned
``price_oracle.BASE_MAINNET_FEEDS`` map (never invented here).

Covers: selector encoding, roundId->price/timestamp/answeredInRound
decoding + wei scaling, roundId 0 rejection, reverted round
(answeredInRound == 0), superseded round (answeredInRound != roundId),
price <= 0 rejection, updatedAt staleness / zero / future handling,
RPC-error fail-closed, constructor address validation, asset-pin
fail-closed (BTC/USD), and the PriceFeed wiring for the options sim.
"""

import time

import pytest
from eth_abi import decode, encode
from web3 import Web3

from sincor2.defi import price_oracle
from sincor2.defi.chainlink_round_reader import (
    _GET_ROUND_DATA_SELECTOR,
    ChainlinkRoundReader,
    InvalidRound,
    RoundReaderError,
    StaleRound,
)
from sincor2.defi.options_protocol import (
    PriceFeed,
    StaleOracle,
    ORACLE_STALENESS_SECONDS,
)

ETH_FEED = price_oracle.BASE_MAINNET_FEEDS["ETH/USD"]
RPC = "https://example.invalid/rpc"
DECIMALS = 8
NOW = time.time()


def _round_bytes(round_id=12345, answer=4000_00000000, started_at=None,
                 updated_at=None, answered_in_round=None):
    return encode(
        ["uint80", "int256", "uint256", "uint256", "uint80"],
        [round_id, answer,
         int(started_at if started_at is not None else NOW - 30),
         int(updated_at if updated_at is not None else NOW - 20),
         answered_in_round if answered_in_round is not None
         else round_id],
    )


class FakeTransport:
    """Canned eth_call backend: records calls, no network."""

    def __init__(self, decimals=DECIMALS, round_bytes=None):
        self.decimals = decimals
        self.round_bytes = round_bytes if round_bytes is not None \
            else _round_bytes()
        self.seen = []  # (rpc_url, to_address, calldata)
        self.fail_next = False

    def __call__(self, rpc_url, to_address, calldata):
        self.seen.append((rpc_url, to_address, calldata))
        if self.fail_next:
            raise ConnectionError("boom")
        from sincor2.defi.chainlink_round_reader import (
            _DECIMALS_SELECTOR,
        )
        if calldata == _DECIMALS_SELECTOR:
            return self.decimals.to_bytes(32, "big")
        assert calldata[:4] == _GET_ROUND_DATA_SELECTOR, \
            f"unexpected selector: {calldata[:4].hex()}"
        return self.round_bytes


def make_reader(feed=ETH_FEED, **kw):
    t = FakeTransport(**kw.pop("transport_kw", {}))
    reader = ChainlinkRoundReader(RPC, feed, transport=t, **kw)
    return reader, t


# -- selector / calldata ------------------------------------------------------

def test_selector_matches_web3_keccak():
    assert _GET_ROUND_DATA_SELECTOR == \
        Web3.keccak(text="getRoundData(uint80)")[:4]


def test_calldata_encodes_round_id():
    reader, t = make_reader()
    t.seen.clear()
    reader.read_round(98765, now=NOW)
    (rpc_url, to_addr, calldata), = t.seen
    assert calldata[:4] == _GET_ROUND_DATA_SELECTOR
    (round_id_word,) = decode(["uint80"], calldata[4:])
    assert round_id_word == 98765
    assert len(calldata) == 4 + 32


# -- happy path ---------------------------------------------------------------

def test_read_round_returns_price_timestamp_answered_in_round():
    reader, _ = make_reader()
    rnd = reader.read_round(12345, now=NOW)
    assert rnd.round_id == 12345
    # answer 4000 * 1e8 -> wei at 1e18 scale
    assert rnd.price_wei == 4000_00000000 * 10**10
    assert abs(rnd.timestamp - (NOW - 20)) < 2
    assert rnd.answered_in_round == 12345
    assert rnd.started_at <= rnd.timestamp


def test_decimals_fetched_at_construction():
    reader, _ = make_reader()
    assert reader.decimals == DECIMALS
    assert reader.feed_address == Web3.to_checksum_address(ETH_FEED)


def test_constructor_takes_feed_address_param():
    reader, _ = make_reader(feed=ETH_FEED)
    assert reader.feed_address == Web3.to_checksum_address(ETH_FEED)


def test_constructor_rejects_bad_address():
    with pytest.raises(RoundReaderError):
        ChainlinkRoundReader(RPC, "not-an-address",
                             transport=FakeTransport())


def test_constructor_requires_rpc_and_feed():
    with pytest.raises(RoundReaderError):
        ChainlinkRoundReader("", ETH_FEED, transport=FakeTransport())
    with pytest.raises(RoundReaderError):
        ChainlinkRoundReader(RPC, "", transport=FakeTransport())


def test_constructor_rejects_decimals_failure():
    t = FakeTransport()
    t.fail_next = True
    with pytest.raises(RoundReaderError):
        ChainlinkRoundReader(RPC, ETH_FEED, transport=t)


def test_for_asset_uses_repo_pins():
    t = FakeTransport()
    reader = ChainlinkRoundReader.for_asset("ETH/USD", RPC, transport=t)
    assert reader.feed_address == Web3.to_checksum_address(ETH_FEED)


def test_for_asset_refuses_unpinned_asset():
    # BTC/USD is deliberately NOT pinned in the repo map; the reader
    # fails closed instead of inventing an address.
    assert "BTC/USD" not in price_oracle.BASE_MAINNET_FEEDS
    with pytest.raises(RoundReaderError):
        ChainlinkRoundReader.for_asset("BTC/USD", RPC,
                                       transport=FakeTransport())


# -- invalid rounds -----------------------------------------------------------

def test_round_id_zero_rejected():
    reader, _ = make_reader()
    with pytest.raises(InvalidRound):
        reader.read_round(0, now=NOW)
    with pytest.raises(InvalidRound):
        reader.read_round(-5, now=NOW)


def test_reverted_round_answered_in_round_zero():
    reader, t = make_reader(
        transport_kw={"round_bytes": _round_bytes(answered_in_round=0)})
    with pytest.raises(InvalidRound, match="answeredInRound == 0"):
        reader.read_round(12345, now=NOW)


def test_price_zero_or_negative_invalid():
    for answer in (0, -1):
        reader, _ = make_reader(
            transport_kw={"round_bytes": _round_bytes(answer=answer)})
        with pytest.raises(InvalidRound, match="non-positive"):
            reader.read_round(12345, now=NOW)


def test_updated_at_zero_invalid():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(updated_at=0)})
    with pytest.raises(InvalidRound, match="updatedAt == 0"):
        reader.read_round(12345, now=NOW)


def test_future_updated_at_invalid():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(
            updated_at=NOW + 3600)})
    with pytest.raises(InvalidRound, match="future"):
        reader.read_round(12345, now=NOW)


def test_short_response_invalid():
    reader, t = make_reader(transport_kw={"round_bytes": b"\x00" * 64})
    with pytest.raises(InvalidRound):
        reader.read_round(12345, now=NOW)


# -- stale rounds -------------------------------------------------------------

def test_superseded_round_stale():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(round_id=12345,
                                                 answered_in_round=12000)})
    with pytest.raises(StaleRound, match="superseded"):
        reader.read_round(12345, now=NOW)


def test_old_updated_at_stale():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(
            updated_at=NOW - 3 * 3600)})
    with pytest.raises(StaleRound, match="stale"):
        reader.read_round(12345, now=NOW)


def test_custom_max_age_honored():
    reader, _ = make_reader(
        max_age_s=24 * 3600,
        transport_kw={"round_bytes": _round_bytes(
            updated_at=NOW - 3 * 3600)})
    rnd = reader.read_round(12345, now=NOW)
    assert rnd.price_wei > 0


# -- fail-closed transport ----------------------------------------------------

def test_rpc_error_fails_closed():
    reader, t = make_reader()
    t.fail_next = True
    with pytest.raises(RoundReaderError):
        reader.read_round(12345, now=NOW)
    assert reader.stats()["errors"] >= 1


# -- PriceFeed wiring ---------------------------------------------------------

def test_price_feed_wiring_for_options_sim():
    reader, _ = make_reader()
    feed = reader.price_feed(12345, now=NOW)
    assert isinstance(feed, PriceFeed)
    assert feed.spot_wei == 4000_00000000 * 10**10
    assert feed.get_spot(NOW) == feed.spot_wei
    # the sim's own staleness guard still applies to the wired feed
    with pytest.raises(StaleOracle):
        feed.get_spot(NOW + ORACLE_STALENESS_SECONDS + 1)


def test_price_feed_wiring_propagates_invalid_round():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(answered_in_round=0)})
    with pytest.raises(InvalidRound):
        reader.price_feed(12345, now=NOW)
