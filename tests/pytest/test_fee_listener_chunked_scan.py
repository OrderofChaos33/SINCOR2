"""Durable fee-listener scanning — chunked eth_getLogs + checksum boundary.

Regression tests for the two production failures documented in
ops/fee-listener-service/STATUS.md:

* a single whole-range ``eth_getLogs`` call cannot clear a large backlog
  against providers that cap the block range (1rpc rejects >50 blocks),
  and a hard failure mid-scan must leave the cursor at the last fully
  scanned chunk boundary — never skip blocks, never double-record;
* the in-repo AXM token constant is lowercase, and web3.py v8 rejects
  non-checksum filter addresses, so the listener as-shipped could never
  complete a live cycle without the FEE_LISTENER_TOKEN env workaround.
  The token must be EIP-55 checksummed at the RPC boundary.

All offline, hermetic fake provider (no network, no keys).
"""

from web3 import Web3

from src.sincor2.onchain.constants import AXIOM_TOKEN
from src.sincor2.onchain.fee_conversion_executor import (
    ConversionLedger,
    STATUS_PENDING,
)
from src.sincor2.onchain.fee_event_listener import (
    TRANSFER_TOPIC,
    FeeEventListener,
    FeeListenerConfig,
    _topic_address,
)

TOKEN = AXIOM_TOKEN  # lowercase in-repo constant
TOKEN_CK = Web3.to_checksum_address(TOKEN)
RECIPIENT = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
PAYER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _log(tx_hash, block, log_index, value_wei, to=RECIPIENT, token=TOKEN_CK):
    # A real RPC returns the contract address checksummed.
    return {
        "blockNumber": block,
        "blockHash": f"0xhash{block:04d}",
        "transactionHash": tx_hash,
        "logIndex": log_index,
        "address": token,
        "topics": [TRANSFER_TOPIC, _topic_address(PAYER), _topic_address(to)],
        "data": hex(value_wei),
    }


class _ChunkEth:
    """Fake eth namespace with provider-style range caps and fault injection."""

    def __init__(self):
        self.blocks = {}              # n -> hash hex
        self.logs = []                # raw log dicts
        self.max_range = None         # reject get_logs spans larger than this
        self.strict_checksum = False  # mimic web3 v8 filter validation
        self.fail_on_get_logs_call = None  # 0-based call index -> outage
        self.get_logs_calls = []      # every filter dict the provider saw

    def get_block_number(self):
        return max(self.blocks)

    def get_block(self, n):
        return {"hash": self.blocks[n], "number": n}

    def get_logs(self, f):
        call_idx = len(self.get_logs_calls)
        self.get_logs_calls.append(dict(f))
        if (self.fail_on_get_logs_call is not None
                and call_idx == self.fail_on_get_logs_call):
            raise ConnectionError("fake mid-scan RPC outage")
        if self.strict_checksum and f["address"] != Web3.to_checksum_address(
                f["address"]):
            raise ValueError("invalid address: not EIP-55 checksummed")
        span = f["toBlock"] - f["fromBlock"] + 1
        if self.max_range is not None and span > self.max_range:
            raise ValueError(
                f"eth_getLogs block range too large: requested {span} "
                f"blocks (max {self.max_range})")
        return [l for l in self.logs
                if f["fromBlock"] <= l["blockNumber"] <= f["toBlock"]]


class _FakeW3:
    def __init__(self):
        self.eth = _ChunkEth()


def _chain(w3, latest):
    for n in range(latest + 1):
        w3.eth.blocks[n] = f"0xhash{n:04d}"


def _listener(w3, tmp_path, **kw):
    kw.setdefault("confirmations", 0)
    kw.setdefault("start_block", 0)
    cfg = FeeListenerConfig(
        rpc_url="https://example.invalid",
        token=TOKEN,
        fee_recipient=RECIPIENT,
        poll_interval_secs=1,
        cursor_path=str(tmp_path / "cursor.json"),
        **kw,
    )
    ledger = ConversionLedger(str(tmp_path / "ledger.json"))
    return FeeEventListener(config=cfg, ledger=ledger,
                            w3_factory=lambda: w3)


# --- (a) multi-chunk scan ------------------------------------------------------


def test_scan_spanning_multiple_chunks_records_each_event_once(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 29)
    w3.eth.logs = [
        _log("0xtx02", 2, 0, 1_000 * 10**18),
        _log("0xtx07", 7, 0, 2_000 * 10**18),
        _log("0xtx13", 13, 0, 3_000 * 10**18),
        _log("0xtx21", 21, 0, 4_000 * 10**18),
        _log("0xtx29", 29, 0, 5_000 * 10**18),
    ]
    L = _listener(w3, tmp_path, log_chunk_blocks=5)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 5
    pending = L.ledger.pending()
    assert len(pending) == 5
    assert len({o["id"] for o in pending}) == 5  # exactly once each
    assert all(o["status"] == STATUS_PENDING for o in pending)
    # Cursor lands exactly at the confirmed tip.
    assert L.load_cursor()["block"] == 29
    # The scan really was chunked: every provider call is bounded by the
    # configured chunk size and the calls tile [0, 29] without gaps.
    calls = w3.eth.get_logs_calls
    assert len(calls) >= 6
    assert all(c["toBlock"] - c["fromBlock"] + 1 <= 5 for c in calls)
    spans = sorted((c["fromBlock"], c["toBlock"]) for c in calls)
    assert spans[0][0] == 0 and spans[-1][1] == 29
    for (_, prev_to), (next_from, _) in zip(spans, spans[1:]):
        assert next_from == prev_to + 1


# --- (b) provider range cap forces splitting ------------------------------------


def test_provider_range_cap_forces_chunk_splitting_and_completes(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 119)
    w3.eth.max_range = 50  # like 1rpc: ranges >50 blocks are rejected
    w3.eth.logs = [
        _log("0xtx10", 10, 0, 1_000 * 10**18),
        _log("0xtx55", 55, 0, 2_000 * 10**18),
        _log("0xtx119", 119, 0, 3_000 * 10**18),
    ]
    # Configured chunk (200) is far above the provider cap: the scan must
    # split rejected chunks down until the provider accepts them.
    L = _listener(w3, tmp_path, log_chunk_blocks=200)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 3
    assert len(L.ledger.pending()) == 3
    assert L.load_cursor()["block"] == 119
    calls = w3.eth.get_logs_calls
    # Splitting really happened: at least one call was rejected for range…
    assert any(c["toBlock"] - c["fromBlock"] + 1 > 50 for c in calls)
    # …and the scan still covered the full range (last call ends at 119).
    assert max(c["toBlock"] for c in calls) == 119


# --- (c) mid-scan failure leaves cursor at last completed chunk -------------------


def test_mid_scan_failure_leaves_cursor_at_last_completed_chunk(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 14)
    w3.eth.logs = [
        _log("0xtx02", 2, 0, 1_000 * 10**18),    # chunk 0-4
        _log("0xtx07", 7, 0, 2_000 * 10**18),    # chunk 5-9
        _log("0xtx12", 12, 0, 3_000 * 10**18),   # chunk 10-14
    ]
    L = _listener(w3, tmp_path, log_chunk_blocks=5)
    # The second get_logs call (chunk 5-9) dies mid-scan.
    w3.eth.fail_on_get_logs_call = 1
    out = L.run_once()
    assert out["ok"] is False
    # Cursor sits exactly at the last completed chunk boundary, with the
    # matching block hash — the failed chunk is NOT skipped.
    cursor = L.load_cursor()
    assert cursor["block"] == 4
    assert cursor["hash"] == w3.eth.blocks[4]
    # Only the first chunk's event was recorded.
    assert len(L.ledger.pending()) == 1

    # Recovery: a re-run resumes at block 5 and completes with no duplicates.
    w3.eth.fail_on_get_logs_call = None
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 2
    pending = L.ledger.pending()
    assert len(pending) == 3
    assert len({o["id"] for o in pending}) == 3
    assert L.load_cursor()["block"] == 14


# --- (d) checksum normalization at the RPC boundary --------------------------------


def test_lowercase_token_is_checksummed_before_get_logs(tmp_path, monkeypatch):
    # No FEE_LISTENER_TOKEN workaround in the environment: the config must
    # fall back to the lowercase in-repo AXIOM_TOKEN constant…
    monkeypatch.delenv("FEE_LISTENER_TOKEN", raising=False)
    w3 = _FakeW3()
    _chain(w3, 5)
    w3.eth.strict_checksum = True  # web3 v8 rejects non-checksum filters
    w3.eth.logs = [_log("0xtx01", 3, 0, 1_000 * 10**18)]
    cfg = FeeListenerConfig(
        rpc_url="https://example.invalid",
        confirmations=0,
        start_block=0,
        poll_interval_secs=1,
        cursor_path=str(tmp_path / "cursor.json"),
    )
    assert cfg.token == TOKEN and cfg.token != TOKEN_CK  # really lowercase
    ledger = ConversionLedger(str(tmp_path / "ledger.json"))
    L = FeeEventListener(config=cfg, ledger=ledger, w3_factory=lambda: w3)
    out = L.run_once()
    # …yet the live cycle completes, because the address handed to
    # eth_getLogs is EIP-55 checksummed at the boundary.
    assert out["ok"] is True
    assert out["recorded_count"] == 1
    assert len(ledger.pending()) == 1
    assert w3.eth.get_logs_calls, "provider was never called"
    assert all(c["address"] == TOKEN_CK for c in w3.eth.get_logs_calls)
