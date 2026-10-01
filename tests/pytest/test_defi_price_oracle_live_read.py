"""Live eth_call reader tests for the price oracle.

Hermetic: ALL network is replaced by a fake transport injected into
make_eth_call_reader. No committed test here may touch a real RPC
endpoint. The single manual live verification lives outside this file
(recorded in the delivery report / doc note).

Covers the read seam contract: correct calldata encoding, stale-round
rejection, zero/negative answer rejection, future-timestamp rejection,
RPC-error fail-closed, and end-to-end scaling through ChainlinkFeedAdapter.
"""

import time

import pytest
from eth_abi import encode
from web3 import Web3

from sincor2.defi import price_oracle
from sincor2.defi.price_oracle import (
    BASE_MAINNET_FEEDS,
    ChainlinkFeedAdapter,
    EthCallChainlinkReader,
    OracleError,
    PRICE_FP,
    _DECIMALS_SELECTOR,
    _LATEST_ROUND_DATA_SELECTOR,
    make_eth_call_reader,
)

ETH_FEED = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"
USDC_FEED = "0x7e860098F58bBFC8648a4311b374B1D669a2bc6B"
RPC = "https://example.invalid/rpc"


def _round_bytes(round_id=12345, answer=4000_00000000,
                 started_at=None, updated_at=None,
                 answered_in_round=None):
    now = time.time()
    return encode(
        ["uint80", "int256", "uint256", "uint256", "uint80"],
        [round_id, answer,
         int(started_at if started_at is not None else now - 30),
         int(updated_at if updated_at is not None else now - 20),
         answered_in_round if answered_in_round is not None else round_id],
    )


class FakeTransport:
    """Canned eth_call backend: records calls, no network."""

    def __init__(self, decimals=8, round_bytes=None):
        self.decimals = decimals
        self.round_bytes = round_bytes if round_bytes is not None \
            else _round_bytes()
        self.seen = []  # (rpc_url, to_address, calldata)
        self.fail_next = False

    def __call__(self, rpc_url, to_address, calldata):
        self.seen.append((rpc_url, to_address, calldata))
        if self.fail_next:
            raise ConnectionError("boom")
        if calldata == _DECIMALS_SELECTOR:
            return self.decimals.to_bytes(32, "big")
        assert calldata == _LATEST_ROUND_DATA_SELECTOR, \
            f"unexpected calldata: {calldata.hex()}"
        return self.round_bytes


def make_reader(feeds=None, **kw):
    t = FakeTransport(**kw.pop("transport_kw", {}))
    reader = make_eth_call_reader(
        RPC, feeds or {"ETH/USD": ETH_FEED}, transport=t)
    return reader, t


# -- selectors ----------------------------------------------------------------

def test_selectors_match_web3_keccak():
    assert _LATEST_ROUND_DATA_SELECTOR == \
        Web3.keccak(text="latestRoundData()")[:4]
    assert _DECIMALS_SELECTOR == Web3.keccak(text="decimals()")[:4]


# -- construction ---------------------------------------------------------------

def test_construction_fetches_decimals_per_feed():
    reader, t = make_reader()
    dec_calls = [c for c in t.seen if c[2] == _DECIMALS_SELECTOR]
    assert len(dec_calls) == 1
    rpc_url, to, _ = dec_calls[0]
    assert rpc_url == RPC
    assert to == Web3.to_checksum_address(ETH_FEED)
    assert reader.stats()["feeds"] == {"ETH/USD": to}


def test_construction_rejects_invalid_address():
    with pytest.raises(OracleError):
        make_eth_call_reader(RPC, {"ETH/USD": "0xnope"},
                             transport=FakeTransport())



def test_construction_fails_closed_when_decimals_unreachable():
    t = FakeTransport()
    t.fail_next = True
    with pytest.raises(OracleError):
        make_eth_call_reader(RPC, {"ETH/USD": ETH_FEED}, transport=t)


def test_construction_fails_closed_on_bad_decimals_length():
    class Short(FakeTransport):
        def __call__(self, rpc_url, to_address, calldata):
            if calldata == _DECIMALS_SELECTOR:
                return b"\x08"  # 1 byte, not 32
            return super().__call__(rpc_url, to_address, calldata)

    with pytest.raises(OracleError):
        make_eth_call_reader(RPC, {"ETH/USD": ETH_FEED},
                             transport=Short())


@pytest.mark.parametrize("bad", [0, 37, 100])
def test_construction_fails_closed_on_insane_decimals(bad):
    with pytest.raises(OracleError):
        make_eth_call_reader(RPC, {"ETH/USD": ETH_FEED},
                             transport=FakeTransport(decimals=bad))


def test_construction_requires_feeds_and_rpc():
    with pytest.raises(OracleError):
        make_eth_call_reader(RPC, {}, transport=FakeTransport())
    with pytest.raises(OracleError):
        make_eth_call_reader("", {"ETH/USD": ETH_FEED},
                             transport=FakeTransport())


# -- read path ------------------------------------------------------------------

def test_calldata_is_latest_round_data_to_feed():
    reader, t = make_reader()
    reader("ETH/USD")
    round_calls = [c for c in t.seen
                   if c[2] == _LATEST_ROUND_DATA_SELECTOR]
    assert len(round_calls) == 1
    rpc_url, to, _ = round_calls[0]
    assert rpc_url == RPC
    assert to == Web3.to_checksum_address(ETH_FEED)


def test_valid_round_returns_chainlink_round():
    reader, _ = make_reader()
    rnd = reader("ETH/USD")
    assert rnd is not None
    assert rnd.round_id == 12345
    assert rnd.answer == 4000_00000000
    assert rnd.decimals == 8
    assert rnd.answered_in_round == 12345
    assert reader.calls == 1 and reader.errors == 0


def test_end_to_end_scaling_through_chainlink_adapter():
    reader, _ = make_reader()
    adapter = ChainlinkFeedAdapter("chainlink-base", reader)
    pt = adapter.fetch("ETH/USD", time.time())
    assert pt is not None
    assert pt.kind == "chainlink"
    # 8-decimal feed answer 4000e8 -> PRICE_FP scale -> 4000e8
    assert pt.price_fp == 4000 * PRICE_FP


def test_stale_round_rejected():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(
            round_id=100, answered_in_round=99)})
    assert reader("ETH/USD") is None
    assert "stale round" in (reader.last_error or "")
    assert reader.errors == 1


@pytest.mark.parametrize("bad_answer", [0, -1, -4000_00000000])
def test_nonpositive_answer_rejected(bad_answer):
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(answer=bad_answer)})
    assert reader("ETH/USD") is None


def test_zero_updated_at_rejected():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(updated_at=0)})
    assert reader("ETH/USD") is None


def test_future_timestamp_rejected():
    reader, _ = make_reader(
        transport_kw={"round_bytes": _round_bytes(
            updated_at=time.time() + 3600)})
    assert reader("ETH/USD") is None
    assert "future" in (reader.last_error or "")


def test_rpc_error_fails_closed():
    reader, t = make_reader()
    t.fail_next = True
    assert reader("ETH/USD") is None
    assert reader.errors == 1
    assert reader.last_error is not None


def test_short_response_rejected():
    reader, _ = make_reader(transport_kw={"round_bytes": b"\x00" * 31})
    assert reader("ETH/USD") is None
    assert "short response" in (reader.last_error or "")


def test_empty_response_rejected():
    reader, _ = make_reader(transport_kw={"round_bytes": b""})
    assert reader("ETH/USD") is None


def test_unknown_asset_returns_none_without_call():
    reader, t = make_reader()
    assert reader("BTC/USD") is None
    round_calls = [c for c in t.seen
                   if c[2] == _LATEST_ROUND_DATA_SELECTOR]
    assert round_calls == []  # no feed pinned -> no network attempt


# -- pinned feeds ---------------------------------------------------------------

def test_base_mainnet_feeds_pinned_and_checksummed():
    assert BASE_MAINNET_FEEDS["ETH/USD"] == ETH_FEED
    assert BASE_MAINNET_FEEDS["USDC/USD"] == USDC_FEED
    for asset, addr in BASE_MAINNET_FEEDS.items():
        # must already be EIP-55 checksummed in source
        assert addr == Web3.to_checksum_address(addr), asset
    # BTC/USD deliberately unpinned (conflicting sources) -- assert the
    # exclusion is explicit, not an accident.
    assert "BTC/USD" not in BASE_MAINNET_FEEDS
